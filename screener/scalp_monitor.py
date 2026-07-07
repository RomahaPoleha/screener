"""
Scalp Monitor — фоновый мониторинг плотностей в реальном времени
WebSocket + Django cache. Хранит только плотности старше 1 минуты.
Только Futures.
"""
import os
import json
import time
import threading
import traceback
import requests
from logging.handlers import RotatingFileHandler
from django.core.cache import cache

# Минимальный объём для записи в кэш (USDT)
GLOBAL_MIN_VOLUME = 5000

# Минимальное время жизни плотности (секунды)
MIN_AGE_SECONDS = 60

# Количество монет для мониторинга
TOP_SYMBOLS_COUNT = 200

# Максимум монет на одно WebSocket подключение
MAX_SYMBOLS_PER_WS = 70

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
        import ccxt
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
order_books = {}
density_timestamps = {}
order_books_lock = threading.Lock()
symbols = []
shutdown_event = threading.Event()


def init_order_book(symbol):
    try:
        url = f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol}USDT&limit=1000"
        res = requests.get(url, timeout=10)
        if not res.ok:
            return False

        data = res.json()
        bids = {float(price): float(qty) for price, qty in data.get('bids', [])}
        asks = {float(price): float(qty) for price, qty in data.get('asks', [])}

        with order_books_lock:
            order_books[symbol] = {'bids': bids, 'asks': asks}
            density_timestamps[symbol] = {}

        sync_to_cache(symbol)

        log(f"✅ Стакан {symbol}: {len(bids)} bids, {len(asks)} asks")
        return True

    except Exception as e:
        log(f"❌ init_order_book({symbol}): {e}")
        return False


def sync_to_cache(symbol):
    try:
        with order_books_lock:
            book = order_books.get(symbol, {})
            timestamps = density_timestamps.get(symbol, {})
            if not book:
                return

        key = f"scalp:{symbol}"
        now = time.time()

        densities = []

        for side, side_name in [('bids', 'buy'), ('asks', 'sell')]:
            for price, qty in book.get(side, {}).items():
                volume = price * qty

                if volume < GLOBAL_MIN_VOLUME:
                    continue

                if price in timestamps:
                    age = now - timestamps[price]
                    if age < MIN_AGE_SECONDS:
                        continue
                else:
                    timestamps[price] = now
                    continue

                densities.append({
                    'price': price,
                    'volume': volume,
                    'side': side_name,
                    'timestamp': timestamps[price]
                })

        cache.set(key, densities, CACHE_TTL)

        with order_books_lock:
            density_timestamps[symbol] = timestamps

    except Exception as e:
        log(f"❌ sync_to_cache({symbol}): {e}")


def update_order_book(symbol, bids_delta, asks_delta):
    try:
        with order_books_lock:
            if symbol not in order_books:
                return

            book = order_books[symbol]
            timestamps = density_timestamps.get(symbol, {})
            changed = False

            for price_str, qty_str in bids_delta:
                price = float(price_str)
                qty = float(qty_str)

                if qty == 0:
                    if price in book['bids']:
                        del book['bids'][price]
                        if price in timestamps:
                            del timestamps[price]
                        changed = True
                else:
                    book['bids'][price] = qty
                    if price not in timestamps:
                        timestamps[price] = time.time()
                    changed = True

            for price_str, qty_str in asks_delta:
                price = float(price_str)
                qty = float(qty_str)

                if qty == 0:
                    if price in book['asks']:
                        del book['asks'][price]
                        if price in timestamps:
                            del timestamps[price]
                        changed = True
                else:
                    book['asks'][price] = qty
                    if price not in timestamps:
                        timestamps[price] = time.time()
                    changed = True

        if changed:
            sync_to_cache(symbol)

    except Exception as e:
        log(f" update_order_book({symbol}): {e}")


def on_message(ws, message):
    try:
        data = json.loads(message)

        if 'data' in data:
            stream_data = data['data']
            symbol = stream_data.get('s', '')
            if symbol.endswith('USDT'):
                symbol = symbol[:-4]

            bids = stream_data.get('b', [])
            asks = stream_data.get('a', [])

            if bids or asks:
                update_order_book(symbol, bids, asks)

    except Exception as e:
        log(f"❌ on_message: {e}")


def on_error(ws, error):
    log(f"❌ WebSocket ошибка: {error}")


def on_close(ws, close_status_code, close_msg):
    log(f"⚠️ WebSocket закрыт: {close_status_code} {close_msg}")
    log("🔄 Переподключение через 5 секунд...")
    time.sleep(5)

    if shutdown_event.is_set():
        return

    # Переподключаемся
    try:
        import websocket
        streams = [f"{s.lower()}usdt@depth@100ms" for s in ws.symbols]
        url = f"wss://fstream.binance.com/stream?streams={'/'.join(streams)}"

        log(f"🔌 Переподключение WebSocket для группы {ws.symbol_group}...")

        new_ws = websocket.WebSocketApp(
            url,
            on_open=lambda ws: on_open(ws),
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        new_ws.symbol_group = ws.symbol_group
        new_ws.symbols = ws.symbols

        new_ws.run_forever()

    except Exception as e:
        log(f"❌ Ошибка переподключения: {e}")


def on_open(ws):
    symbol_group = ws.symbol_group
    log(f"✅ WebSocket открыт для группы {symbol_group}: {len(ws.symbols)} символов")

    streams = [f"{s.lower()}usdt@depth@100ms" for s in ws.symbols]
    subscribe_msg = {
        "method": "SUBSCRIBE",
        "params": streams,
        "id": 1
    }
    ws.send(json.dumps(subscribe_msg))


def start_websocket_connection(symbol_group, symbols_list):
    try:
        import websocket

        if not symbols_list:
            return

        log(f"🔄 Инициализация стаканов для группы {symbol_group} ({len(symbols_list)} символов)...")
        for symbol in symbols_list:
            init_order_book(symbol)
            time.sleep(0.05)

        streams = [f"{s.lower()}usdt@depth@100ms" for s in symbols_list]
        url = f"wss://fstream.binance.com/stream?streams={'/'.join(streams)}"

        log(f" Подключение WebSocket для группы {symbol_group}...")

        ws = websocket.WebSocketApp(
            url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        ws.symbol_group = symbol_group
        ws.symbols = symbols_list

        ws.run_forever()

    except Exception as e:
        log(f"❌ start_websocket_connection({symbol_group}): {e}")
        log(traceback.format_exc())


def start_all_websocket_connections():
    global symbols

    if not symbols:
        symbols = get_top_symbols(TOP_SYMBOLS_COUNT)
        if not symbols:
            log("⚠️ Нет символов для мониторинга")
            return

    num_groups = (len(symbols) + MAX_SYMBOLS_PER_WS - 1) // MAX_SYMBOLS_PER_WS
    symbol_groups = [symbols[i::num_groups] for i in range(num_groups)]

    log(f"🚀 Запуск {num_groups} WebSocket подключений...")

    threads = []
    for idx, symbol_group in enumerate(symbol_groups):
        thread = threading.Thread(
            target=start_websocket_connection,
            args=(idx, symbol_group),
            name=f'Scalp-WS-{idx}',
            daemon=True
        )
        threads.append(thread)
        thread.start()
        time.sleep(1)


def scalp_monitor_loop():
    setup_excepthook()
    log("🚀 Scalp Monitor запущен!")
    log(f" Лог-файл: {LOG_FILE}")
    log(f"⏱️ Минимальный возраст: {MIN_AGE_SECONDS} сек")
    time.sleep(5)

    heartbeat_counter = 0

    start_all_websocket_connections()

    while not shutdown_event.is_set():
        try:
            heartbeat_counter += 1
            if heartbeat_counter % 6 == 0:
                log(f"💓 Scalp Monitor: heartbeat (цикл {heartbeat_counter})")

            if heartbeat_counter % 60 == 0:
                new_symbols = get_top_symbols(TOP_SYMBOLS_COUNT)
                if new_symbols != symbols[:TOP_SYMBOLS_COUNT]:
                    log(f"🔄 Список символов обновлён")
                    symbols.clear()
                    symbols.extend(new_symbols)

            if shutdown_event.is_set():
                break

            time.sleep(10)

        except Exception as e:
            log(f"❌ Ошибка в цикле: {e}")
            log(traceback.format_exc())
            if shutdown_event.wait(timeout=60):
                break


def start_scalp_monitor():
    thread = threading.Thread(
        target=scalp_monitor_loop,
        name='Scalp-Monitor',
        daemon=True
    )
    thread.start()
    log(f"✅ Scalp Monitor запущен (PID: {thread.ident})")


def stop_scalp_monitor():
    log("📤 Остановка Scalp Monitor...")
    shutdown_event.set()