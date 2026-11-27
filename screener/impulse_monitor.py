"""
Impulse Monitor v4 — Candle-based (1m & 5m)
Архитектура: WebSocket -> Агрегация 1m/5m свечей -> Сравнение с предыдущей
"""
import asyncio
import json
import time
import logging
import os
import websockets
from collections import deque
from django.core.cache import cache

LOG_DIR = '/app/data'
LOG_FILE = os.path.join(LOG_DIR, 'impulse_monitor.log')
os.makedirs(LOG_DIR, exist_ok=True)

_logger = logging.getLogger('impulse_monitor')
_logger.setLevel(logging.INFO)

import logging.handlers
_handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3)
_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
_logger.addHandler(_handler)
_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter('%(message)s'))
_logger.addHandler(_console)

# ================= КОНФИГУРАЦИЯ =================

BINANCE_TICKER_WS = "wss://fstream.binance.com/market/ws/!miniTicker@arr"

MIN_AMPLITUDE_PCT = 1.5       # Минимальный процент для импульса
WINDOWS = [60, 300]           # 1 мин и 5 мин

ticker_queue = None
cooldowns = {}

# Состояние: {symbol_window: {current candle, previous candle}}
candle_state = {}


def _window_start(ts, window):
    return int(ts // window) * window


def process_ticker_batch(tickers, now_sec):
    for ticker in tickers:
        if not isinstance(ticker, dict):
            continue
        symbol = ticker.get('s', '')
        if not symbol.endswith('USDT'):
            continue

        clean = symbol[:-4]
        try:
            price = float(ticker.get('c', 0))
            volume = float(ticker.get('q', 0))
        except (TypeError, ValueError):
            continue
        if not price:
            continue

        for window in WINDOWS:
            state_key = f"{clean}_{window}"
            minute = _window_start(now_sec, window)
            state = candle_state.get(state_key)

            if state is None:
                state = {
                    'symbol': clean,
                    'window': window,
                    'current_minute': minute,
                    'open': price,
                    'high': price,
                    'low': price,
                    'close': price,
                    'volume': volume,
            'prev_candle': None,
            'last_ts': now_sec,
            'alerted_minute': None,
            'last_alert_price': None,
        }
                candle_state[state_key] = state

            # Новая свеча окна — закрыть текущую
            if minute != state['current_minute']:
                closed = {
                    'open': state['open'],
                    'high': state['high'],
                    'low': state['low'],
                    'close': state['close'],
                    'timestamp': state['current_minute'],
                }
                state['prev_candle'] = closed

                state['current_minute'] = minute
                state['open'] = price
                state['high'] = price
                state['low'] = price
                state['close'] = price
                state['volume'] = volume
                state['alerted_minute'] = None
                state['last_alert_price'] = None

            # Обновить текущую свечу
            state['high'] = max(state['high'], price)
            state['low'] = min(state['low'], price)
            state['close'] = price
            state['volume'] = volume
            state['last_ts'] = now_sec


def detect_impulses(now_sec):
    impulses = []

    # Очистка устаревших состояний (нет тиков > 5 мин)
    old_keys = [k for k, v in candle_state.items() if now_sec - v.get('last_ts', 0) > 300]
    for k in old_keys:
        candle_state.pop(k, None)

    for state_key, state in list(candle_state.items()):
        window = state.get('window', 60)
        prev = state.get('prev_candle')
        if not prev:
            continue

        # Повторный импульс в той же свече — только если цена ушла ещё на MIN% от последнего алерта
        if state.get('alerted_minute') == state.get('current_minute'):
            last_price = state.get('last_alert_price')
            if last_price:
                move_pct = abs((cur_close - last_price) / last_price) * 100
                if move_pct < MIN_AMPLITUDE_PCT:
                    continue
            else:
                continue

        prev_close = prev.get('close', 0)
        if not prev_close:
            continue
        cur_close = state['close']
        change_pct = ((cur_close - prev_close) / prev_close) * 100

        cur_high = state['high']
        cur_low = state['low']
        if cur_low == 0:
            continue
        cur_amp = ((cur_high - cur_low) / cur_low) * 100

        if abs(change_pct) < MIN_AMPLITUDE_PCT and cur_amp < MIN_AMPLITUDE_PCT:
            continue

        state['alerted_minute'] = state['current_minute']
        state['last_alert_price'] = cur_close

        if abs(change_pct) >= MIN_AMPLITUDE_PCT:
            direction = 'up' if change_pct >= 0 else 'down'
            pct = abs(change_pct)
        else:
            direction = 'up' if cur_close >= state['open'] else 'down'
            pct = cur_amp

        impulses.append({
            'symbol': state['symbol'],
            'price_change': round(pct, 2),
            'direction': direction,
            'current_price': cur_close,
            'window': window,
            'timestamp': now_sec,
            'volume': round(state.get('volume', 0), 2),
        })

    return impulses


# ================= WEBSOCKET КЛИЕНТ =================

async def impulse_ws_listener():
    global ticker_queue
    if ticker_queue is None:
        ticker_queue = asyncio.Queue(maxsize=10000)

    while True:
        try:
            _logger.info("🔌 Impulse WS: подключение к Binance...")
            async with websockets.connect(
                BINANCE_TICKER_WS,
                ping_interval=30,
                ping_timeout=30,
                close_timeout=10
            ) as ws:
                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=20)
                        data = json.loads(msg)
                        tickers = data if isinstance(data, list) else [data]
                        now_sec = time.time()

                        try:
                            ticker_queue.put_nowait((tickers, now_sec))
                        except asyncio.QueueFull:
                            _logger.warning("⚠️ Impulse queue full, tick batch dropped")
                    except asyncio.TimeoutError:
                        continue
                    except websockets.exceptions.ConnectionClosed as e:
                        _logger.warning(f"⚠️ WS connection closed: {e}")
                        break
                    except Exception as e:
                        _logger.warning(f"⚠️ Impulse WS parse: {e}")
        except Exception as e:
            _logger.error(f"❌ Impulse WS ошибка: {e}, reconnect 5s")
            await asyncio.sleep(5)


async def ticker_processor():
    global ticker_queue
    while True:
        try:
            if ticker_queue is None:
                await asyncio.sleep(0.1)
                continue
            tickers, now_sec = await ticker_queue.get()
            process_ticker_batch(tickers, now_sec)
        except Exception as e:
            _logger.error(f"❌ Ticker processor ошибка: {e}")


async def impulse_checker():
    while True:
        try:
            await asyncio.sleep(1)
            impulses = detect_impulses(time.time())
            if impulses:
                existing = cache.get('impulse:recent') or []
                existing = impulses + existing
                cache.set('impulse:recent', existing[:50], 300)
                for imp in impulses:
                    _logger.info(
                        f"🔥 IMPULSE: {imp['symbol']} {imp['direction'].upper()} "
                        f"{imp['price_change']}% (window: {imp['window']}s)"
                    )
        except Exception as e:
            _logger.error(f"❌ Impulse checker ошибка: {e}")


async def main():
    _logger.info("🚀 Запуск Impulse Monitor (1m & 5m Candle-based)...")
    await asyncio.gather(
        impulse_ws_listener(),
        ticker_processor(),
        impulse_checker()
    )


def start_impulse_monitor():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except Exception as e:
        _logger.error(f"❌ Impulse Monitor упал: {e}")