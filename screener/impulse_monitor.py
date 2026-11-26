"""
Impulse Monitor v3 — Candle-based (miniTicker WS)
Архитектура: WebSocket -> Агрегация 1m свечей -> Детекция импульсов
"""
import asyncio
import json
import time
import logging
import os
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

# Параметры импульса (из исследования)
VOLUME_LOOKBACK = 3       # Свечей для среднего объёма
VOLUME_MULTIPLE = 3.0     # Объём >= 3× среднего
RISE_ATR_MULTIPLE = 2.0   # Тело свечи >= 2× ATR(14)
ATR_PERIOD = 14            # Период ATR
IMPULSE_NEED = 16          # Минимум свечей для детекции (15 для ATR + 1 judged)
COOLDOWN_SEC = 60          # Не спамить одним символом чаще раза в минуту
MAX_CANDLE_HISTORY = 30   # Хранить последние N закрытых свечей
MAX_SYMBOLS = 100000      # Лимит отслеживаемых символов

ticker_queue = None
cooldowns = {}            # {symbol: last_alert_timestamp}

# Состояние: {symbol: {current_candle, history, prev_q}}
candle_state = {}


# ================= АГРЕГАЦИЯ СВЕЧЕЙ =================

def _get_minute(ts):
    """Округляет timestamp до начала минуты."""
    return int(ts // 60) * 60


def process_ticker_batch(tickers, now_sec):
    """Агрегирует тики в 1m свечи."""
    for ticker in tickers:
        if not isinstance(ticker, dict):
            continue
        symbol = ticker.get('s', '')
        if not symbol.endswith('USDT'):
            continue

        clean = symbol[:-4]
        try:
            price = float(ticker.get('c', 0))
            q_volume = float(ticker.get('q', 0))  # 24h quote volume
        except (TypeError, ValueError):
            continue
        if not price:
            continue

        minute = _get_minute(now_sec)

        state = candle_state.get(clean)
        if state is None:
            if len(candle_state) >= MAX_SYMBOLS:
                continue
            state = {
                'symbol': clean,
                'current_minute': minute,
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'volume': 0.0,
                'prev_q': q_volume,
                'last_ts': now_sec,
                'history': deque(maxlen=MAX_CANDLE_HISTORY),
            }
            candle_state[clean] = state

        # Если новая минута — закрыть текущую свечу, начать новую
        if minute != state['current_minute']:
            # Закрыть свечу
            closed_candle = {
                'open': state['open'],
                'high': state['high'],
                'low': state['low'],
                'close': state['close'],
                'volume': state['volume'],
                'timestamp': state['current_minute'],
            }
            state['history'].append(closed_candle)

            # Начать новую свечу
            state['current_minute'] = minute
            state['open'] = price
            state['high'] = price
            state['low'] = price
            state['close'] = price
            state['volume'] = 0.0

        # Обновить текущую свечу
        state['high'] = max(state['high'], price)
        state['low'] = min(state['low'], price)
        state['close'] = price
        # Объём: дельта 24h volume
        vol_delta = q_volume - state['prev_q']
        if 0 < vol_delta < 10_000_000:  # cap to prevent reset spikes
            state['volume'] += vol_delta
        state['prev_q'] = q_volume
        state['last_ts'] = now_sec


# ================= ATR РАСЧЁТ =================

def _calculate_atr(candles, period=14):
    """Рассчитывает ATR(period) по списку закрытых свечей."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        c = candles[i]
        prev_close = candles[i-1]['close']
        tr = max(c['high'] - c['low'],
                 abs(c['high'] - prev_close),
                 abs(c['low'] - prev_close))
        trs.append(tr)
    # ATR = среднее TR за последние `period` свечей
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


# ================= ДЕТЕКЦИЯ ИМПУЛЬСОВ =================

def detect_impulses(now_sec):
    """Детектирует импульсы по закрытым свечам."""
    now_ms = int(now_sec * 1000)
    impulses = []

    # Очистка устаревших cooldown-ов
    expired = [k for k, v in cooldowns.items() if now_sec - v > 300]
    for k in expired:
        cooldowns.pop(k, None)

    # Очистка устаревших состояний
    old_keys = [k for k, v in candle_state.items() if now_sec - v.get('last_ts', 0) > 300]
    for k in old_keys:
        candle_state.pop(k, None)

    for symbol, state in list(candle_state.items()):
        history = state['history']
        if len(history) < IMPULSE_NEED:
            continue

        # Последняя закрытая свеча — judged
        judged = history[-1]
        # Baseline — все кроме judged
        prior = list(history)[:-1]

        # ATR(14) по prior
        atr = _calculate_atr(prior, ATR_PERIOD)
        if atr is None or atr <= 0:
            continue

        # Volume average по volumeLookback свечам перед judged
        vol_candles = prior[-VOLUME_LOOKBACK:]
        if len(vol_candles) < VOLUME_LOOKBACK:
            continue
        avg_volume = sum(c['volume'] for c in vol_candles) / VOLUME_LOOKBACK

        # Тело свечи
        body = judged['close'] - judged['open']
        body_atr = body / atr

        # Объём
        volume_ratio = judged['volume'] / avg_volume if avg_volume > 0 else 0

        # Проверки
        if avg_volume > 0 and volume_ratio < VOLUME_MULTIPLE:
            continue
        if abs(body_atr) < RISE_ATR_MULTIPLE:
            continue

        # Cooldown
        last_alert = cooldowns.get(symbol, 0)
        if now_sec - last_alert < COOLDOWN_SEC:
            continue

        cooldowns[symbol] = now_sec

        direction = 'up' if body > 0 else 'down'

        impulses.append({
            'symbol': symbol,
            'price_change': round(abs(judged['close'] - judged['open']) / judged['open'] * 100, 2),
            'direction': direction,
            'current_price': judged['close'],
            'window': 60,
            'timestamp': judged['timestamp'],
            'volume': round(judged['volume'], 2),
            'volume_ratio': round(volume_ratio, 1),
            'body_atr': round(body_atr, 2),
        })

    return impulses


# ================= WEBSOCKET КЛИЕНТ =================

async def impulse_ws_listener():
    global ticker_queue
    ticker_queue = asyncio.Queue(maxsize=2000)

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
                            pass
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
                ticker_queue = asyncio.Queue(maxsize=2000)
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
                        f"🔥 IMPULSE: {imp['symbol']} "
                        f"{imp['direction'].upper()} {imp['price_change']}% (V: ${imp['volume']:,.0f}, body_atr: {imp['body_atr']})"
                    )
        except Exception as e:
            _logger.error(f"❌ Impulse checker ошибка: {e}")


async def main():
    _logger.info("🚀 Запуск Impulse Monitor v3 (Candle-based)...")
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
PYEOF