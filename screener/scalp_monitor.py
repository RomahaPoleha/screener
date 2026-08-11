"""
Scalp Monitor — мониторинг плотностей в реальном времени
WebSocket + Django cache. Futures + Spot. Топ-100.
Асинхронная обработка через очередь.
"""
import json
import time
import threading
import traceback
import requests
import websocket
from queue import Queue, Empty
from logging.handlers import RotatingFileHandler
from django.core.cache import cache
import ccxt
import os

# Минимальный объём для записи в кэш (USDT)
GLOBAL_MIN_VOLUME = 10000

# Минимальное время жизни плотности (секунды)
MIN_AGE_SECONDS = 180

# Количество монет для мониторинга
TOP_SYMBOLS_COUNT = 30

# TTL кэша (15 минут)
CACHE_TTL = 900

# Максимальный размер очереди сообщений
MAX_QUEUE_SIZE = 50000

# URLs для разных рынков
FUTURES_WS_URL = "wss://fstream.binance.com/ws"
FUTURES_REST_URL = "https://fapi.binance.com/fapi/v1/depth"

SPOT_WS_URL = "wss://stream.binance.com:9443/ws"
SPOT_REST_URL = "https://api.binance.com/api/v3/depth"
# Bybit Futures
BYBIT_FUTURES_WS_URL = "wss://stream.bybit.com/v5/public/linear"
BYBIT_FUTURES_REST_URL = "https://api.bybit.com/v5/market/orderbook?category=linear&symbol={}USDT"

# Глобальное состояние для Bybit Futures
bybit_futures_order_books = {}
bybit_futures_density_timestamps = {}
bybit_futures_order_books_lock = threading.Lock()
bybit_futures_symbols = []
bybit_futures_message_queue = Queue(maxsize=MAX_QUEUE_SIZE)

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


def is_valid_symbol(symbol):
    if '-' in symbol:
        return False
    if len(symbol) < 2 or len(symbol) > 15:
        return False
    if not symbol.replace('_', '').isalnum():
        return False
    return True


def get_top_symbols(limit=TOP_SYMBOLS_COUNT, market='futures', exchange='binance'):
    """Получает топ монет по рейтингу: Объем * |Изменение %|"""
    log(f"🔥 get_top_symbols() СТАРТ для {exchange} {market} (лимит: {limit})")
    try:
        if exchange == 'binance':
            ccxt_market = 'future' if market == 'futures' else 'spot'
            ex = ccxt.binance({
                'enableRateLimit': True,
                'timeout': 10000,
                'options': {'defaultType': ccxt_market}
            })
        else:  # bybit
            ex = ccxt.bybit({
                'enableRateLimit': True,
                'timeout': 10000,
                'options': {'defaultType': 'linear'}
            })

        tickers = ex.fetch_tickers()
        symbols_with_score = []
        stablecoins = {'USDT', 'USDC', 'FDUSD', 'DAI', 'TUSD', 'BUSD', 'USDP', 'EURC'}

        for symbol, data in tickers.items():
            if ':USDT' not in symbol and not symbol.endswith('/USDT'):
                continue

            clean_symbol = symbol.replace('/USDT', '').replace(':USDT', '')

            if clean_symbol in stablecoins:
                continue

            if not is_valid_symbol(clean_symbol):
                continue

            volume = data.get('quoteVolume') or 0
            percentage = data.get('percentage') or 0
            score = volume * abs(percentage)

            if score > 0:
                symbols_with_score.append((clean_symbol, score))

        symbols_with_score.sort(key=lambda x: x[1], reverse=True)
        symbols = [s[0] for s in symbols_with_score[:limit]]

        log(f"✅ {exchange} {market} Топ-{limit} по рейтингу: найдено {len(symbols)} монет")
        return symbols

    except Exception as e:
        log(f"❌ Ошибка в get_top_symbols({exchange} {market}): {e}")
        log(traceback.format_exc())
        return []


# Глобальное состояние для Futures
futures_order_books = {}
futures_density_timestamps = {}
futures_order_books_lock = threading.Lock()
futures_symbols = []
futures_message_queue = Queue(maxsize=MAX_QUEUE_SIZE)

# Глобальное состояние для Spot
spot_order_books = {}
spot_density_timestamps = {}
spot_order_books_lock = threading.Lock()
spot_symbols = []
spot_message_queue = Queue(maxsize=MAX_QUEUE_SIZE)

shutdown_event = threading.Event()
last_sync_time = {}


def init_order_book(symbol, market='futures', exchange='binance'):
    """Инициализация стакана для конкретного рынка и биржи"""
    try:
        if exchange == 'binance':
            if market == 'futures':
                url = f"{FUTURES_REST_URL}?symbol={symbol}USDT&limit=100"
                order_books = futures_order_books
                timestamps = futures_density_timestamps
                lock = futures_order_books_lock
            else:
                url = f"{SPOT_REST_URL}?symbol={symbol}USDT&limit=100"
                order_books = spot_order_books
                timestamps = spot_density_timestamps
                lock = spot_order_books_lock
        else:  # bybit
            url = BYBIT_FUTURES_REST_URL.format(symbol)
            order_books = bybit_futures_order_books
            timestamps = bybit_futures_density_timestamps
            lock = bybit_futures_order_books_lock

        res = requests.get(url, timeout=10)
        if not res.ok:
            log(f"⚠️ {exchange} {market} {symbol}: HTTP {res.status_code}")
            return False

        data = res.json()

        if exchange == 'binance':
            bids = {float(price): float(qty) for price, qty in data.get('bids', [])}
            asks = {float(price): float(qty) for price, qty in data.get('asks', [])}
        else:  # bybit
            result = data.get('result', {})
            bids = {float(price): float(qty) for price, qty in result.get('b', [])}
            asks = {float(price): float(qty) for price, qty in result.get('a', [])}

        with lock:
            order_books[symbol] = {'bids': bids, 'asks': asks}
            timestamps[symbol] = {}

        sync_to_cache(symbol, market, exchange)

        log(f"✅ {exchange} {market} Стакан {symbol}: {len(bids)} bids, {len(asks)} asks")
        return True

    except Exception as e:
        log(f"❌ init_order_book({exchange} {market} {symbol}): {e}")
        return False


def sync_to_cache(symbol, market='futures', exchange='binance'):
    """Синхронизация стакана в Redis"""
    try:
        if exchange == 'binance':
            if market == 'futures':
                order_books = futures_order_books
                timestamps = futures_density_timestamps
                lock = futures_order_books_lock
            else:
                order_books = spot_order_books
                timestamps = spot_density_timestamps
                lock = spot_order_books_lock
        else:  # bybit
            order_books = bybit_futures_order_books
            timestamps = bybit_futures_density_timestamps
            lock = bybit_futures_order_books_lock

        with lock:
            book = order_books.get(symbol, {})
            ts = timestamps.get(symbol, {})
            if not book:
                return

        # Отдельный ключ для Bybit
        if exchange == 'bybit':
            key = f"scalp:{market}:bybit:{symbol}"
        else:
            key = f"scalp:{market}:{symbol}"

        now = time.time()
        densities = []

        for side, side_name in [('bids', 'buy'), ('asks', 'sell')]:
            for price, qty in book.get(side, {}).items():
                volume = price * qty

                if volume < GLOBAL_MIN_VOLUME:
                    continue

                if price in ts:
                    age = now - ts[price]
                    if age < MIN_AGE_SECONDS:
                        continue
                else:
                    ts[price] = now
                    continue

                densities.append({
                    'price': price,
                    'volume': volume,
                    'side': side_name,
                    'timestamp': ts[price],
                    'exchange': exchange
                })

        cache.set(key, densities, CACHE_TTL)

        with lock:
            timestamps[symbol] = ts

    except Exception as e:
        log(f"❌ sync_to_cache({exchange} {market} {symbol}): {e}")
        log(traceback.format_exc())


def update_order_book(symbol, bids_delta, asks_delta, market='futures', exchange='binance'):
    """Обновление стакана данными из WebSocket"""
    global last_sync_time

    try:
        if exchange == 'binance':
            if market == 'futures':
                order_books = futures_order_books
                timestamps = futures_density_timestamps
                lock = futures_order_books_lock
            else:
                order_books = spot_order_books
                timestamps = spot_density_timestamps
                lock = spot_order_books_lock
        else:  # bybit
            order_books = bybit_futures_order_books
            timestamps = bybit_futures_density_timestamps
            lock = bybit_futures_order_books_lock

        if not hasattr(update_order_book, 'call_counts'):
            update_order_book.call_counts = {}

        key = f"{exchange}:{market}:{symbol}"
        if key not in update_order_book.call_counts:
            update_order_book.call_counts[key] = 0
        update_order_book.call_counts[key] += 1

        if update_order_book.call_counts[key] <= 3:
            log(f"🔄 {exchange} {market} update_order_book({symbol}) вызов #{update_order_book.call_counts[key]}")

        with lock:
            if symbol not in order_books:
                if update_order_book.call_counts[key] <= 3:
                    log(f"⚠️ {exchange} {market} update_order_book({symbol}): нет в order_books")
                return

            book = order_books[symbol]
            ts = timestamps.get(symbol, {})
            changed = False

            for price_str, qty_str in bids_delta:
                price = float(price_str)
                qty = float(qty_str)

                if qty == 0:
                    if price in book['bids']:
                        del book['bids'][price]
                        if price in ts:
                            del ts[price]
                        changed = True
                else:
                    book['bids'][price] = qty
                    if price not in ts:
                        ts[price] = time.time()
                    changed = True

            for price_str, qty_str in asks_delta:
                price = float(price_str)
                qty = float(qty_str)

                if qty == 0:
                    if price in book['asks']:
                        del book['asks'][price]
                        if price in ts:
                            del ts[price]
                        changed = True
                else:
                    book['asks'][price] = qty
                    if price not in ts:
                        ts[price] = time.time()
                    changed = True

        if changed:
            now = time.time()
            if key not in last_sync_time or (now - last_sync_time[key]) >= 3:
                sync_to_cache(symbol, market, exchange)
                last_sync_time[key] = now

    except Exception as e:
        log(f"❌ update_order_book({exchange} {market} {symbol}): {e}")
        log(traceback.format_exc())


def process_message_queue(market='futures'):
    """Обработка очереди сообщений для конкретного рынка"""
    log(f"🔄 {market} Процессор очереди сообщений запущен")

    if market == 'futures':
        message_queue = futures_message_queue
    else:
        message_queue = spot_message_queue

    while not shutdown_event.is_set():
        try:
            message = message_queue.get(timeout=1)
            data = json.loads(message)

            # Формат 1: с обёрткой data (для /stream?streams=...)
            if 'data' in data:
                stream_data = data['data']
                symbol = stream_data.get('s', '')
                if symbol.endswith('USDT'):
                    symbol = symbol[:-4]
                bids = stream_data.get('b', [])
                asks = stream_data.get('a', [])

            # Формат 2: прямое сообщение (для /ws + SUBSCRIBE)
            elif 's' in data:
                symbol = data.get('s', '')
                if symbol.endswith('USDT'):
                    symbol = symbol[:-4]
                bids = data.get('b', [])
                asks = data.get('a', [])
            else:
                continue

            if bids or asks:
                update_order_book(symbol, bids, asks, market)

        except Empty:
            continue
        except Exception as e:
            log(f"❌ {market} Ошибка обработки сообщения: {e}")
            log(traceback.format_exc())


def on_message(ws, message, market='futures'):
    """WebSocket только складывает сообщения в очередь"""
    try:
        if market == 'futures':
            message_queue = futures_message_queue
        else:
            message_queue = spot_message_queue

        # Логируем первые 5 сообщений
        if not hasattr(ws, 'message_count'):
            ws.message_count = 0
        ws.message_count += 1

        if ws.message_count <= 5:
            log(f"📨 {market} Сообщение #{ws.message_count}: {message[:200]}...")

        if ws.message_count % 1000 == 0:
            log(f"📨 {market} Обработано {ws.message_count} сообщений, очередь: {message_queue.qsize()}")

        try:
            message_queue.put_nowait(message)
        except:
            pass

    except Exception as e:
        log(f"❌ {market} on_message: {e}")


def on_error(ws, error, market='futures'):
    log(f"❌ {market} WebSocket ошибка: {error}")
    log("🔄 Переподключение через 5 секунд...")
    time.sleep(5)

    if shutdown_event.is_set():
        return

    try:
        if market == 'futures':
            url = FUTURES_WS_URL
        else:
            url = SPOT_WS_URL

        new_ws = websocket.WebSocketApp(
            url,
            on_open=lambda ws: on_open(ws, market),
            on_message=lambda ws, msg: on_message(ws, msg, market),
            on_error=lambda ws, err: on_error(ws, err, market),
            on_close=lambda ws, code, msg: on_close(ws, code, msg, market)
        )
        new_ws.symbols = ws.symbols
        new_ws.run_forever(ping_interval=20, ping_timeout=10)

    except Exception as e:
        log(f"❌ {market} Ошибка переподключения: {e}")


def on_close(ws, close_status_code, close_msg, market='futures'):
    log(f"⚠️ {market} WebSocket закрыт: code={close_status_code}, msg={close_msg}")
    log("🔄 Переподключение через 5 секунд...")
    time.sleep(5)

    if shutdown_event.is_set():
        return

    try:
        if market == 'futures':
            url = FUTURES_WS_URL
        else:
            url = SPOT_WS_URL

        new_ws = websocket.WebSocketApp(
            url,
            on_open=lambda ws: on_open(ws, market),
            on_message=lambda ws, msg: on_message(ws, msg, market),
            on_error=lambda ws, err: on_error(ws, err, market),
            on_close=lambda ws, code, msg: on_close(ws, code, msg, market)
        )
        new_ws.symbols = ws.symbols
        new_ws.run_forever(ping_interval=20, ping_timeout=10)

    except Exception as e:
        log(f"❌ {market} Ошибка переподключения: {e}")


def on_open(ws, market='futures'):
    log(f"✅ {market} WebSocket открыт: {len(ws.symbols)} символов")

    streams = [f"{s.lower()}usdt@depth@100ms" for s in ws.symbols]
    subscribe_msg = {
        "method": "SUBSCRIBE",
        "params": streams,
        "id": 1
    }
    ws.send(json.dumps(subscribe_msg))
    log(f"✅ {market} Подписка отправлена: {len(streams)} стримов")


def start_websocket(symbols_list, market='futures'):
    """Запуск WebSocket для конкретного рынка"""
    try:
        if market == 'futures':
            url = FUTURES_WS_URL
        else:
            url = SPOT_WS_URL

        log(f"🔌 {market} Подключение WebSocket для {len(symbols_list)} символов...")

        ws = websocket.WebSocketApp(
            url,
            on_open=lambda ws: on_open(ws, market),
            on_message=lambda ws, msg: on_message(ws, msg, market),
            on_error=lambda ws, err: on_error(ws, err, market),
            on_close=lambda ws, code, msg: on_close(ws, code, msg, market)
        )
        ws.symbols = symbols_list

        log(f"🚀 {market} Запуск ws.run_forever()...")
        ws.run_forever(ping_interval=20, ping_timeout=10)

    except Exception as e:
        log(f"❌ {market} Ошибка в start_websocket: {e}")
        log(traceback.format_exc())


def scalp_monitor_loop():
    """Основной цикл мониторинга"""
    global futures_symbols, spot_symbols

    log("🚀 Scalp Monitor запущен (асинхронный)!")
    log(f"📝 Лог-файл: {LOG_FILE}")
    log(f"⏱️ Минимальный возраст: {MIN_AGE_SECONDS} сек")
    log(f"📊 Мониторинг топ-{TOP_SYMBOLS_COUNT} монет для Futures и Spot")
    time.sleep(5)

    # Запускаем процессоры очередей
    futures_processor_thread = threading.Thread(
        target=lambda: process_message_queue('futures'),
        name='Futures-Queue-Processor',
        daemon=True
    )
    futures_processor_thread.start()
    log(f"✅ Futures процессор очереди запущен")

    spot_processor_thread = threading.Thread(
        target=lambda: process_message_queue('spot'),
        name='Spot-Queue-Processor',
        daemon=True
    )
    spot_processor_thread.start()
    log(f"✅ Spot процессор очереди запущен")

    # Получаем топ монет для обоих рынков
    futures_symbols = get_top_symbols(TOP_SYMBOLS_COUNT, 'futures')
    spot_symbols = get_top_symbols(TOP_SYMBOLS_COUNT, 'spot')

    if not futures_symbols and not spot_symbols:
        log("️ Нет символов для мониторинга")
        return

    # Инициализация стаканов Futures
    if futures_symbols:
        log(f"🔄 Futures инициализация стаканов для {len(futures_symbols)} символов...")
        for idx, symbol in enumerate(futures_symbols, 1):
            init_order_book(symbol, 'futures')
            time.sleep(0.05)
            if idx % 20 == 0:
                log(f"  Futures прогресс: {idx}/{len(futures_symbols)}")
        log(f"✅ Futures все стаканы инициализированы")

    # Инициализация стаканов Spot
    if spot_symbols:
        log(f"🔄 Spot инициализация стаканов для {len(spot_symbols)} символов...")
        for idx, symbol in enumerate(spot_symbols, 1):
            init_order_book(symbol, 'spot')
            time.sleep(0.05)
            if idx % 20 == 0:
                log(f"  Spot прогресс: {idx}/{len(spot_symbols)}")
        log(f"✅ Spot все стаканы инициализированы")

    # Запуск WebSocket для Futures
    if futures_symbols:
        futures_ws_thread = threading.Thread(
            target=lambda: start_websocket(futures_symbols, 'futures'),
            name='Futures-WebSocket',
            daemon=True
        )
        futures_ws_thread.start()
        log(f"✅ Futures WebSocket поток запущен")

    # Запуск WebSocket для Spot
    if spot_symbols:
        spot_ws_thread = threading.Thread(
            target=lambda: start_websocket(spot_symbols, 'spot'),
            name='Spot-WebSocket',
            daemon=True
        )
        spot_ws_thread.start()
        log(f"✅ Spot WebSocket поток запущен")

    heartbeat_counter = 0
    empty_queue_count = 0

    while not shutdown_event.is_set():
        heartbeat_counter += 1
        if heartbeat_counter % 6 == 0:
            futures_qsize = futures_message_queue.qsize()
            spot_qsize = spot_message_queue.qsize()
            log(f"💓 Scalp Monitor: heartbeat (цикл {heartbeat_counter}), Futures очередь: {futures_qsize}, Spot очередь: {spot_qsize}")

            if futures_qsize == 0 and spot_qsize == 0:
                empty_queue_count += 1
                if empty_queue_count >= 3:
                    log(f"⚠️ Обе очереди пусты {empty_queue_count} раз!")
            else:
                empty_queue_count = 0

        if shutdown_event.wait(timeout=10):
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
    log(" Остановка Scalp Monitor...")
    shutdown_event.set()