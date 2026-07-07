"""
Scalp Monitor — простой мониторинг плотностей через REST API
Django cache (Redis). Только Futures. Топ-100.
"""
import os
import sys
import time
import threading
import traceback
import requests
from logging.handlers import RotatingFileHandler
from django.core.cache import cache
import ccxt

# Минимальный объём для записи в кэш (USDT)
GLOBAL_MIN_VOLUME = 5000

# Минимальное время жизни плотности (секунды)
MIN_AGE_SECONDS = 60

# Количество монет для мониторинга
TOP_SYMBOLS_COUNT = 100

# Интервал между циклами (секунды)
UPDATE_INTERVAL = 10

# TTL кэша
CACHE_TTL = 900

# Настройка логов
LOG_DIR = '/app/data'
LOG_FILE = os.path.join(LOG_DIR, 'scalp_monitor.log')

os.makedirs(LOG_DIR, exist_ok=True)

_scalp_logger = __import__('logging').getLogger('scalp_monitor')
_scalp_logger.setLevel(__import__('logging').INFO)

_rotating_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding='utf-8'
)
_rotating_handler.setFormatter(__import__('logging').Formatter('%(asctime)s - %(message)s'))
_scalp_logger.addHandler(_rotating_handler)

_console_handler = __import__('logging').StreamHandler()
_console_handler.setFormatter(__import__('logging').Formatter('%(message)s'))
_scalp_logger.addHandler(_console_handler)


def log(msg):
    _scalp_logger.info(msg)


def setup_excepthook():
    def excepthook(exc_type, exc_value, exc_tb):
        log(f"❌ НЕОБРАБОТАННОЕ ИСКЛЮЧЕНИЕ: {exc_type.__name__}: {exc_value}")
        log(''.join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    sys.excepthook = excepthook


def is_valid_symbol(symbol):
    if '-' in symbol:
        return False
    if len(symbol) < 2 or len(symbol) > 15:
        return False
    if not symbol.replace('_', '').isalnum():
        return False
    return True


def get_top_symbols(limit=TOP_SYMBOLS_COUNT):
    log(f"🔥 get_top_symbols() СТАРТ")
    try:
        exchange = ccxt.binance({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': 'future'}
        })
        tickers = exchange.fetch_tickers()

        symbols_with_volume = []
        for symbol, data in tickers.items():
            if ':USDT' not in symbol and not symbol.endswith('/USDT'):
                continue

            volume = data.get('quoteVolume') or 0
            if volume < 1000000:
                continue

            clean_symbol = symbol.replace('/USDT', '').replace(':USDT', '')

            if not is_valid_symbol(clean_symbol):
                continue

            symbols_with_volume.append((clean_symbol, volume))

        symbols_with_volume.sort(key=lambda x: x[1], reverse=True)
        symbols = [s[0] for s in symbols_with_volume[:limit]]

        log(f"✅ Топ-{limit}: найдено {len(symbols)} монет")
        return symbols

    except Exception as e:
        log(f"❌ Ошибка в get_top_symbols(): {e}")
        log(traceback.format_exc())
        return []


# Глобальное состояние
density_timestamps = {}  # {symbol: {price: timestamp}}
shutdown_event = threading.Event()


def fetch_order_book(symbol):
    """Получить стакан через REST API"""
    try:
        url = f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol}USDT&limit=1000"
        res = requests.get(url, timeout=10)
        if not res.ok:
            return None
        return res.json()
    except Exception as e:
        log(f"⚠️ fetch_order_book({symbol}): {e}")
        return None


def update_scalp_for_symbol(symbol):
    """Обновить плотности для одной монеты"""
    try:
        depth = fetch_order_book(symbol)
        if not depth:
            return False

        now = time.time()
        key = f"scalp:{symbol}"

        # Получаем текущие timestamps для этой монеты
        if symbol not in density_timestamps:
            density_timestamps[symbol] = {}

        timestamps = density_timestamps[symbol]

        # Собираем текущие цены из стакана
        current_prices = set()

        # Проверяем bids
        for price_str, qty_str in depth.get('bids', []):
            price = float(price_str)
            qty = float(qty_str)
            volume = price * qty

            if volume >= GLOBAL_MIN_VOLUME:
                current_prices.add(price)
                if price not in timestamps:
                    # Новая плотность — запоминаем время появления
                    timestamps[price] = now

        # Проверяем asks
        for price_str, qty_str in depth.get('asks', []):
            price = float(price_str)
            qty = float(qty_str)
            volume = price * qty

            if volume >= GLOBAL_MIN_VOLUME:
                current_prices.add(price)
                if price not in timestamps:
                    timestamps[price] = now

        # Удаляем timestamps для исчезнувших плотностей
        for price in list(timestamps.keys()):
            if price not in current_prices:
                del timestamps[price]

        # Собираем плотности старше 60 секунд
        densities = []
        for price in current_prices:
            if price in timestamps:
                age = now - timestamps[price]
                if age >= MIN_AGE_SECONDS:
                    # Определяем сторону
                    side = None
                    volume = 0
                    for p, q in depth.get('bids', []):
                        if float(p) == price:
                            side = 'buy'
                            volume = price * float(q)
                            break
                    if side is None:
                        for p, q in depth.get('asks', []):
                            if float(p) == price:
                                side = 'sell'
                                volume = price * float(q)
                                break

                    if side:
                        densities.append({
                            'price': price,
                            'volume': volume,
                            'side': side,
                            'timestamp': timestamps[price]
                        })

        # Сохраняем в cache
        cache.set(key, densities, CACHE_TTL)

        # Логируем только для BTC
        if symbol == 'BTC':
            log(f"📊 {symbol}: {len(densities)} плотностей старше 60с, всего в стакане {len(current_prices)}")

        return True

    except Exception as e:
        log(f"❌ update_scalp_for_symbol({symbol}): {e}")
        log(traceback.format_exc())
        return False


def update_scalp_all(symbols):
    """Обновить плотности для всех монет"""
    log(f"🔄 Обновление плотностей для {len(symbols)} монет...")

    success = 0
    errors = 0

    for idx, symbol in enumerate(symbols, 1):
        if shutdown_event.is_set():
            break

        try:
            if update_scalp_for_symbol(symbol):
                success += 1
            else:
                errors += 1

            # Задержка чтобы не превысить rate limit
            time.sleep(0.1)

            if idx % 20 == 0:
                log(f"  Прогресс: {idx}/{len(symbols)} | ✅ {success} | ❌ {errors}")

        except Exception as e:
            errors += 1
            log(f"⚠️ Ошибка для {symbol}: {e}")
            continue

    log(f"✅ Завершено: {success}/{len(symbols)} успешно, {errors} ошибок")


def scalp_monitor_loop():
    setup_excepthook()
    log("🚀 Scalp Monitor запущен (REST API режим)!")
    log(f"📝 Лог-файл: {LOG_FILE}")
    log(f"⏱️ Минимальный возраст: {MIN_AGE_SECONDS} сек")
    log(f"📊 Мониторинг топ-{TOP_SYMBOLS_COUNT} монет")
    log(f"🔄 Интервал обновления: {UPDATE_INTERVAL} сек")
    time.sleep(10)

    heartbeat_counter = 0

    while not shutdown_event.is_set():
        try:
            heartbeat_counter += 1
            if heartbeat_counter % 6 == 0:
                log(f"💓 Scalp Monitor: heartbeat (цикл {heartbeat_counter})")

            # Получаем топ монет
            symbols = get_top_symbols(TOP_SYMBOLS_COUNT)
            if not symbols:
                log("⚠️ Нет символов для мониторинга")
                if shutdown_event.wait(timeout=UPDATE_INTERVAL):
                    break
                continue

            # Обновляем плотности
            try:
                update_scalp_all(symbols)
            except Exception as e:
                log(f"❌ Цикл обновления упал: {e}")
                log(traceback.format_exc())

            if shutdown_event.is_set():
                break

            log(f"💤 Scalp Monitor: сон {UPDATE_INTERVAL} секунд...")
            if shutdown_event.wait(timeout=UPDATE_INTERVAL):
                log("🛑 Получен сигнал остановки")
                break

        except Exception as e:
            log(f"❌ Ошибка в цикле Scalp Monitor: {e}")
            log(traceback.format_exc())
            if shutdown_event.wait(timeout=60):
                break


def start_scalp_monitor():
    log("🔧 Вызов start_scalp_monitor()...")
    thread = threading.Thread(
        target=scalp_monitor_loop,
        name='Scalp-Monitor',
        daemon=True
    )
    thread.start()
    log(f"✅ Scalp Monitor поток запущен (PID: {thread.ident})")


def stop_scalp_monitor():
    log("📤 Остановка Scalp Monitor...")
    shutdown_event.set()