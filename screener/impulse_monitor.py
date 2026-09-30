"""
Impulse Monitor — детекция амплитуды свечи (Scalp Board logic)
"""
import asyncio
import json
import time
import websockets
from django.core.cache import cache
from logging.handlers import RotatingFileHandler
import logging
import os

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

# Состояние свечей: symbol -> {'start_time': int, 'low': float, 'high': float, 'current': float, 'volume': float, 'window': int}
candle_state = {}
cooldowns = {}  # symbol -> timestamp последнего алерта

BINANCE_TICKER_WS = "wss://fstream.binance.com/market/ws/!miniTicker@arr"

# Жёсткий минимальный порог для защиты от коллапса
MIN_THRESHOLD = 0.5
# Сервер считает ОБА окна параллельно
WINDOWS = [60, 300]


def detect_impulses(now_sec):
    """Детектирует импульсы для ВСЕХ окон параллельно"""
    now_ms = now_sec * 1000
    impulses = []

    for window in WINDOWS:
        cooldown_ms = max(30000, min(300000, window * 20))
        current_window_start = int(now_sec // window) * window

        for symbol, state in candle_state.items():
            if state.get('window') != window or state['start_time'] < current_window_start:
                continue
            low = state.get('low', 0)
            high = state.get('high', 0)
            if low == 0 or high == 0:
                continue
            amplitude = ((high - low) / low) * 100
            if amplitude < MIN_THRESHOLD:
                continue
            last_alert = cooldowns.get(f"{symbol}_{window}", 0)
            if now_ms - last_alert < cooldown_ms:
                continue
            cooldowns[f"{symbol}_{window}"] = now_ms
            mid_price = (high + low) / 2
            direction = 'up' if state.get('current', mid_price) >= mid_price else 'down'
            impulses.append({
                'symbol': symbol,
                'price_change': round(amplitude, 2),
                'direction': direction,
                'current_price': state.get('current', 0),
                'window': window,
                'timestamp': now_sec,
                'volume': round(state.get('volume', 0), 2),
            })
    return impulses

async def impulse_ws_listener():
    while True:
        try:
            _logger.info("🔌 Impulse WS: подключение к Binance...")
            async with websockets.connect(BINANCE_TICKER_WS, ping_interval=20, ping_timeout=20) as ws:
                _logger.info("✅ Impulse WS: подключен")
                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30)
                        tickers = json.loads(msg)
                        now_sec = time.time()
                        # Сервер считает ОБА окна параллельно
                        for window in WINDOWS:
                            current_window_start = int(now_sec // window) * window

                        for ticker in tickers:
                            symbol = ticker.get('s', '')
                            if not symbol.endswith('USDT'):
                                continue

                            clean = symbol[:-4]
                            price = float(ticker.get('c', 0))
                            volume = float(ticker.get('q', 0))
                            if not price:
                                continue

                            # Если это новая свеча (или первый тик для этой монеты)
                            # Обновляем состояние для КАЖДОГО окна
                            for window in WINDOWS:
                                state_key = f"{clean}_{window}"
                                current_window_start = int(now_sec // window) * window
                                if state_key not in candle_state or candle_state[state_key][
                                    'start_time'] != current_window_start:
                                    candle_state[state_key] = {
                                        'start_time': current_window_start,
                                        'low': price,
                                        'high': price,
                                        'current': price,
                                        'volume': volume,
                                        'window': window,
                                        'symbol': clean
                                    }
                                else:
                                    if price < candle_state[state_key]['low']:
                                        candle_state[state_key]['low'] = price
                                    if price > candle_state[state_key]['high']:
                                        candle_state[state_key]['high'] = price
                                    candle_state[state_key]['current'] = price
                                    candle_state[state_key]['volume'] = volume
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        _logger.warning(f"⚠️ Impulse WS parse: {e}")
        except Exception as e:
            _logger.error(f"❌ Impulse WS ошибка: {e}, reconnect 3s")
            await asyncio.sleep(3)

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
    _logger.info("🚀 Запуск Impulse Monitor (Amplitude Mode)...")
    await asyncio.gather(impulse_ws_listener(), impulse_checker())

def start_impulse_monitor():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except Exception as e:
        _logger.error(f"❌ Impulse Monitor упал: {e}")