"""
Impulse Monitor — детекция амплитуды свечи (Scalp Board logic)
Сервер ловит ВСЕ импульсы >= 0.5% для обоих окон (60с и 300с).
Клиент фильтрует по своим настройкам из localStorage.
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

# Состояние свечей: "{symbol}_{window}" -> {'start_time', 'low', 'high', 'current', 'volume', 'window', 'symbol'}
candle_state = {}
cooldowns = {}  # "{symbol}_{window}" -> timestamp последнего алерта

BINANCE_TICKER_WS = "wss://fstream.binance.com/market/ws/!miniTicker@arr"

# Жёсткий минимальный порог для защиты от коллапса
MIN_THRESHOLD = 1.5
# Сервер считает ОБА окна параллельно
WINDOWS = [60, 300]

# Очередь для разделения приёма и обработки
ticker_queue = None


def process_ticker_batch(tickers, now_sec):
    """Обработка пачки тикеров — синхронная, быстрая функция"""
    for ticker in tickers:
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

        # Обновляем состояние для КАЖДОГО окна независимо
        for window in WINDOWS:
            state_key = f"{clean}_{window}"
            current_window_start = int(now_sec // window) * window

            state = candle_state.get(state_key)
            if state is None or state['start_time'] != current_window_start:
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
                if price < state['low']:
                    state['low'] = price
                if price > state['high']:
                    state['high'] = price
                state['current'] = price
                state['volume'] = volume


async def ticker_processor():
    """Фоновая задача — обрабатывает тикеры из очереди"""
    global ticker_queue
    while True:
        try:
            tickers, now_sec = await ticker_queue.get()
            # Выполняем синхронную обработку в executor, чтобы не блокировать event loop
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, process_ticker_batch, tickers, now_sec)
        except Exception as e:
            _logger.warning(f"⚠️ Ticker processor error: {e}")
            await asyncio.sleep(0.1)


def detect_impulses(now_sec):
    """Детектирует импульсы для ВСЕХ окон параллельно"""
    now_ms = now_sec * 1000
    impulses = []

    for window in WINDOWS:
        cooldown_ms = max(30000, window * 1000)  # 1 мин → 60с, 5 мин → 300с
        current_window_start = int(now_sec // window) * window

        for state_key, state in candle_state.items():
            if state.get('window') != window or state['start_time'] < current_window_start:
                continue
            low = state.get('low', 0)
            high = state.get('high', 0)
            if low == 0 or high == 0:
                continue

            amplitude = ((high - low) / low) * 100
            if amplitude < MIN_THRESHOLD:
                continue

            last_alert = cooldowns.get(state_key, 0)
            if now_ms - last_alert < cooldown_ms:
                continue

            cooldowns[state_key] = now_ms
            mid_price = (high + low) / 2
            direction = 'up' if state.get('current', mid_price) >= mid_price else 'down'

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
    # Очередь с ограничением размера — защита от переполнения памяти
    ticker_queue = asyncio.Queue(maxsize=100)

    while True:
        try:
            _logger.info("🔌 Impulse WS: подключение к Binance...")
            # ✅ УВЕЛИЧЕНЫ ping параметры: 30с интервал, 30с таймаут
            async with websockets.connect(
                BINANCE_TICKER_WS,
                ping_interval=30,
                ping_timeout=30,
                close_timeout=10
            ) as ws:
                _logger.info("✅ Impulse WS: подключен")
                while True:
                    try:
                        # ✅ Уменьшен timeout приёма — быстрее реагируем на ошибки
                        msg = await asyncio.wait_for(ws.recv(), timeout=20)
                        tickers = json.loads(msg)
                        now_sec = time.time()

                        # ✅ КЛАДЁМ В ОЧЕРЕДЬ, а не обрабатываем сразу
                        try:
                            ticker_queue.put_nowait((tickers, now_sec))
                        except asyncio.QueueFull:
                            # Если очередь переполнена — пропускаем (лучше потерять данные, чем уронить WS)
                            pass

                    except asyncio.TimeoutError:
                        # Таймаут приёма — нормальная ситуация, продолжаем
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
    _logger.info("🚀 Запуск Impulse Monitor (Amplitude Mode)...")
    # ✅ Запускаем 3 задачи параллельно: WS, обработчик очереди, чекер алертов
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