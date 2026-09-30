"""
Impulse Monitor — серверная детекция ценовых импульсов
Заменяет клиентский WebSocket к Binance miniTicker
"""
import asyncio
import json
import time
import threading
import websockets
from django.core.cache import cache
from logging.handlers import RotatingFileHandler
import logging
import os

# ==========================================
# ЛОГИ
# ==========================================
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

# ==========================================
# СОСТОЯНИЕ (в памяти процесса)
# ==========================================
price_history = {}  # symbol -> [{time: float, price: float}]
HISTORY_MAX_AGE = 300  # 5 минут, как на клиенте
MAX_HISTORY_LEN = 600  # макс записей на монету

# ==========================================
# НАСТРОЙКИ (читаем из Redis, дефолты как на клиенте)
# ==========================================
DEFAULT_THRESHOLD = 1.0   # процент
DEFAULT_WINDOW = 60       # секунд
COOLDOWN_MULTIPLIER = 20  # кулдаун = window * multiplier (мс)

# Кулдаун по монетам
cooldowns = {}  # symbol -> timestamp последнего алерта

# WebSocket URL (тот же что на клиенте)
BINANCE_TICKER_WS = "wss://fstream.binance.com/market/ws/!miniTicker@arr"


def get_settings():
    """Читаем настройки из Redis (или дефолты)"""
    threshold = cache.get('impulse:settings:threshold') or DEFAULT_THRESHOLD
    window = cache.get('impulse:settings:window') or DEFAULT_WINDOW
    return float(threshold), int(window)


def detect_impulses(now_sec):
    """
    Проверяет все монеты на импульс.
    Возвращает список обнаруженных импульсов.
    """
    threshold, window = get_settings()
    cooldown_ms = max(30000, min(300000, window * COOLDOWN_MULTIPLIER))
    now_ms = now_sec * 1000
    target_time = now_sec - window
    impulses = []

    for symbol, history in price_history.items():
        if not history:
            continue

        # Проверка: есть ли данные достаточно старые
        if history[0]['time'] > target_time:
            continue

        # Находим цену в момент target_time
        reference_price = None
        for i in range(len(history) - 1, -1, -1):
            if history[i]['time'] <= target_time:
                reference_price = history[i]['price']
                break

        if not reference_price or reference_price == 0:
            continue

        current_price = history[-1]['price']
        price_change = ((current_price - reference_price) / reference_price) * 100
        abs_change = abs(price_change)

        if abs_change < threshold:
            continue

        # Кулдаун
        last_alert = cooldowns.get(symbol, 0)
        if now_ms - last_alert < cooldown_ms:
            continue

        cooldowns[symbol] = now_ms
        direction = 'up' if price_change > 0 else 'down'

        impulses.append({
            'symbol': symbol,
            'price_change': round(abs_change, 2),
            'direction': direction,
            'current_price': current_price,
            'window': window,
            'timestamp': now_sec,
        })

    return impulses


def trim_history():
    """Обрезает старую историю (как на клиенте)"""
    now = time.time()
    cutoff = now - HISTORY_MAX_AGE
    for symbol in list(price_history.keys()):
        history = price_history[symbol]
        # Удаляем записи старше cutoff
        while history and history[0]['time'] < cutoff:
            history.pop(0)
        # Ограничиваем длину
        if len(history) > MAX_HISTORY_LEN:
            price_history[symbol] = history[-MAX_HISTORY_LEN:]


async def impulse_ws_listener():
    """Подключается к Binance miniTicker и собирает цены"""
    while True:
        try:
            _logger.info("🔌 Impulse WS: подключение к Binance...")
            async with websockets.connect(
                BINANCE_TICKER_WS,
                ping_interval=20,
                ping_timeout=20
            ) as ws:
                _logger.info("✅ Impulse WS: подключен")

                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30)
                        tickers = json.loads(msg)
                        now_sec = time.time()

                        for ticker in tickers:
                            symbol = ticker.get('s', '')
                            if not symbol.endswith('USDT'):
                                continue
                            clean = symbol[:-4]  # убираем USDT
                            price = float(ticker.get('c', 0))
                            if not price:
                                continue

                            if clean not in price_history:
                                price_history[clean] = []

                            price_history[clean].append({
                                'time': now_sec,
                                'price': price
                            })

                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        _logger.warning(f"⚠️ Impulse WS parse: {e}")

        except Exception as e:
            _logger.error(f"❌ Impulse WS ошибка: {e}, reconnect 3s")
            await asyncio.sleep(3)


async def impulse_checker():
    """Каждую секунду проверяет импульсы и пишет в Redis"""
    while True:
        try:
            await asyncio.sleep(1)
            now_sec = time.time()

            # Обрезаем старую историю
            trim_history()

            # Детектируем импульсы
            impulses = detect_impulses(now_sec)

            if impulses:
                # Сохраняем в Redis (список последних 50)
                existing = cache.get('impulse:recent') or []
                existing = impulses + existing
                existing = existing[:50]  # храним 50 последних

                cache.set('impulse:recent', existing, 300)

                for imp in impulses:
                    _logger.info(
                        f"🔥 IMPULSE: {imp['symbol']} "
                        f"{imp['direction']} {imp['price_change']}% "
                        f"(окно {imp['window']}с)"
                    )

        except Exception as e:
            _logger.error(f"❌ Impulse checker ошибка: {e}")


async def main():
    """Главная async функция"""
    _logger.info("🚀 Запуск Impulse Monitor...")
    await asyncio.gather(
        impulse_ws_listener(),
        impulse_checker(),
    )


def start_impulse_monitor():
    """Точка входа — запускается в отдельном потоке"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except Exception as e:
        _logger.error(f"❌ Impulse Monitor упал: {e}")
        import traceback
        _logger.error(traceback.format_exc())