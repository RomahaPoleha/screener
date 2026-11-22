"""
Impulse Monitor — детекция амплитуды свечи (Scalp Board logic)
Сервер ловит ВСЕ импульсы >= 0.5% для обоих окон (60с и 300с).
Окна СКОЛЬЗЯЩИЕ (последние N секунд от «сейчас»), чтобы не резать
движение на границах фиксированных окон — так боты и дают больший %.
Клиент фильтрует по своим настройкам из localStorage.
"""
import asyncio
import json
import time
from collections import deque
import websockets
from django.core.cache import cache
from logging.handlers import RotatingFileHandler
import logging
import os

from .memory_config import MAX_CANDLE_AGE_SEC

LOG_DIR = '/app/data'
LOG_FILE = os.path.join(LOG_DIR, 'impulse_monitor.log')
os.makedirs(LOG_DIR, exist_ok=True)

_logger = logging.getLogger('impulse_monitor')
_logger.setLevel(logging.INFO)
_handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3)
_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
_logger.addHandler(_handler)
_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter('%(message)s'))
_logger.addHandler(_console)

# Состояние: "{symbol}_{window}" -> {...}
candle_state = {}
cooldowns = {}  # cooldowns keyed by same state key
impulse_active_symbols = set()

BINANCE_TICKER_WS = "wss://fstream.binance.com/market/ws/!miniTicker@arr"

MIN_THRESHOLD = 1.5
WINDOWS = [60, 300]

MAX_IMPULSE_SYMBOLS = 200

ticker_queue = None


def _prune(state, now_sec):
    window = state['window']
    cutoff = now_sec - window
    # prune price history
    was_empty = not state['price_history']
    while state['price_history'] and state['price_history'][0][0] < cutoff:
        state['price_history'].popleft()
    # reset reported extremes if window fully expired
    if not state['price_history'] and not was_empty:
        state['last_reported_high'] = None
        state['last_reported_low'] = None
    # prune min/max deques
    while state['min_deque'] and state['min_deque'][0][0] < cutoff:
        state['min_deque'].popleft()
    while state['max_deque'] and state['max_deque'][0][0] < cutoff:
        state['max_deque'].popleft()


def process_ticker_batch(tickers, now_sec):
    """Обработка пачки тикеров — синхронная, быстрая функция"""
    for ticker in tickers:
        symbol = ticker.get('s', '')
        if not symbol.endswith('USDT'):
            continue

        clean = symbol[:-4]
        if len(impulse_active_symbols) >= MAX_IMPULSE_SYMBOLS and clean not in impulse_active_symbols:
            continue

        try:
            price = float(ticker.get('c', 0))
            volume = float(ticker.get('q', 0))
        except (TypeError, ValueError):
            continue
        if not price:
            continue

        for window in WINDOWS:
            state_key = f"{clean}_{window}"

            state = candle_state.get(state_key)
            if state is None:
                state = {
                    'symbol': clean,
                    'window': window,
                    'price_history': deque(),
                    'min_deque': deque(),
                    'max_deque': deque(),
                    'current': price,
                    'volume': volume,
                    'last_ts': now_sec,
                    'last_reported_high': None,
                    'last_reported_low': None,
                }
                candle_state[state_key] = state
                impulse_active_symbols.add(clean)

            # reset extremes if window expired before this tick
            if not state['price_history']:
                state['last_reported_high'] = None
                state['last_reported_low'] = None

            _prune(state, now_sec)

            state['current'] = price
            state['volume'] = volume
            state['last_ts'] = now_sec

            state['price_history'].append((now_sec, price))
            while state['min_deque'] and state['min_deque'][-1][1] >= price:
                state['min_deque'].pop()
            state['min_deque'].append((now_sec, price))
            while state['max_deque'] and state['max_deque'][-1][1] <= price:
                state['max_deque'].pop()
            state['max_deque'].append((now_sec, price))


async def ticker_processor():
    global ticker_queue
    while True:
        try:
            tickers, now_sec = await ticker_queue.get()
            process_ticker_batch(tickers, now_sec)
        except Exception as e:
            _logger.warning(f"⚠️ Ticker processor error: {e}")
            await asyncio.sleep(0.1)


def detect_impulses(now_sec):
    """Детектирует импульсы для ВСЕХ окон параллельно (скользящие окна)"""
    now_ms = now_sec * 1000
    impulses = []

    # Очистка старых состояний
    old_keys = [
        k for k, v in candle_state.items()
        if now_sec - v.get('last_ts', 0) > MAX_CANDLE_AGE_SEC
    ]
    for k in old_keys:
        candle_state.pop(k, None)
        cooldowns.pop(k, None)

    symbols_in_state = set()
    for state_key in candle_state.keys():
        base = state_key.split('_')[0]
        symbols_in_state.add(base)
    for s in list(impulse_active_symbols):
        if s not in symbols_in_state:
            impulse_active_symbols.discard(s)

    if len(cooldowns) > MAX_IMPULSE_SYMBOLS * len(WINDOWS):
        oldest = sorted(cooldowns.items(), key=lambda kv: kv[1])[:len(cooldowns) - MAX_IMPULSE_SYMBOLS * len(WINDOWS)]
        for k, _ in oldest:
            cooldowns.pop(k, None)

    for window in WINDOWS:
        cooldown_ms = max(30000, window * 1000)

        for state_key, state in list(candle_state.items()):
            if state.get('window') != window:
                continue

            _prune(state, now_sec)

            if not state['min_deque'] or not state['max_deque']:
                continue
            low = state['min_deque'][0][1]
            high = state['max_deque'][0][1]
            if low == 0 or high == 0:
                continue

            amplitude = ((high - low) / low) * 100
            if amplitude < MIN_THRESHOLD:
                continue

            # не репортим, если нет нового экстремума
            lrh = state.get('last_reported_high')
            lrl = state.get('last_reported_low')
            if lrh is not None and high <= lrh and low >= lrl:
                continue

            last_alert = cooldowns.get(state_key, 0)
            if now_ms - last_alert < cooldown_ms:
                continue

            cooldowns[state_key] = now_ms
            state['last_reported_high'] = high
            state['last_reported_low'] = low

            open_price = state['price_history'][0][1] if state['price_history'] else state.get('current', 0)
            direction = 'up' if state.get('current', 0) >= open_price else 'down'

            impulses.append({
                'symbol': state.get('symbol', ''),
                'price_change': round(amplitude, 2),
                'direction': direction,
                'current_price': state.get('current', 0),
                'window': window,
                'timestamp': now_sec,
                'volume': round(state.get('volume', 0), 2),
            })
    return impulses


async def impulse_ws_listener():
    global ticker_queue
    ticker_queue = asyncio.Queue(maxsize=1000)

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
                        tickers = json.loads(msg)
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
                    _logger.info(f"🔥 IMPULSE: {imp['symbol']} {imp['direction']} {imp['price_change']}% (V: ${imp['volume']}, {imp['window']}s)")
        except Exception as e:
            _logger.error(f"❌ Impulse checker ошибка: {e}")


async def main():
    _logger.info("🚀 Запуск Impulse Monitor (Sliding Window Mode)...")
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
