"""
Scalp Monitor — мониторинг плотностей в реальном времени
WebSocket + Django cache. Только Futures. Топ-100.
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
MIN_AGE_SECONDS = 0

# Количество монет для мониторинга
TOP_SYMBOLS_COUNT = 100

# TTL кэша (15 минут)
CACHE_TTL = 900

# Максимальный размер очереди сообщений
MAX_QUEUE_SIZE = 10000

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
order_books = {}
density_timestamps = {}
order_books_lock = threading.Lock()
symbols = []
shutdown_event = threading.Event()
message_queue = Queue(maxsize=MAX_QUEUE_SIZE)


def init_order_book(symbol):
    try:
        url = f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol}USDT&limit=1000"
        res = requests.get(url, timeout=10)
        if not res.ok:
            log(f"⚠️ {symbol}: HTTP {res.status_code}")
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

        # Логируем для BTC и ETH
        if symbol in ['BTC', 'ETH']:
            log(f"📊 sync_to_cache({symbol}): {len(densities)} плотностей, всего уровней: {len(book.get('bids', {})) + len(book.get('asks', {}))}")

        cache.set(key, densities, CACHE_TTL)

        with order_books_lock:
            density_timestamps[symbol] = timestamps

    except Exception as e:
        log(f"❌ sync_to_cache({symbol}): {e}")
        log(traceback.format_exc())


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
            # Логируем для ETH
            if symbol == 'ETH':
                log(f"🔄 update_order_book({symbol}): changed=True, вызван sync_to_cache")

    except Exception as e:
        log(f" update_order_book({symbol}): {e}")


def process_message_queue():
    """Отдельный поток для обработки очереди сообщений"""
    log("🔄 Процессор очереди сообщений запущен")

    while not shutdown_event.is_set():
        try:
            # Получаем сообщение из очереди (таймаут 1 сек)
            message = message_queue.get(timeout=1)

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

        except Empty:
            # Очередь пуста, продолжаем
            continue
        except Exception as e:
            log(f"❌ Ошибка обработки сообщения: {e}")


def on_message(ws, message):
    """WebSocket только складывает сообщения в очередь"""
    try:
        # Логируем первые 5 сообщений
        if not hasattr(ws, 'message_count'):
            ws.message_count = 0
        ws.message_count += 1

        if ws.message_count <= 5:
            log(f"📨 Сообщение #{ws.message_count}: {message[:200]}...")

        # Логируем каждые 1000 сообщений
        if ws.message_count % 1000 == 0:
            log(f"📨 Обработано {ws.message_count} сообщений, очередь: {message_queue.qsize()}")

        # Складываем в очередь (неблокирующе)
        try:
            message_queue.put_nowait(message)
        except:
            # Очередь полная, пропускаем сообщение
            pass

    except Exception as e:
        log(f"❌ on_message: {e}")


def on_error(ws, error):
    log(f"❌ WebSocket ошибка: {error}")
    log(traceback.format_exc())


def on_close(ws, close_status_code, close_msg):
    log(f"⚠️ WebSocket закрыт: code={close_status_code}, msg={close_msg}")
    log("🔄 Переподключение через 5 секунд...")
    time.sleep(5)

    if shutdown_event.is_set():
        return

    try:
        url = "wss://fstream.binance.com/ws"

        log(f" Переподключение WebSocket...")

        new_ws = websocket.WebSocketApp(
            url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        new_ws.symbols = ws.symbols
        new_ws.run_forever(ping_interval=20, ping_timeout=10)

    except Exception as e:
        log(f"❌ Ошибка переподключения: {e}")


# def on_open(ws):
#     log(f"✅ WebSocket открыт: {len(ws.symbols)} символов")
#
#     streams = [f"{s.lower()}usdt@depth@100ms" for s in ws.symbols]
#     subscribe_msg = {
#         "method": "SUBSCRIBE",
#         "params": streams,
#         "id": 1
#     }
#     ws.send(json.dumps(subscribe_msg))
#     log(f"✅ Подписка отправлена: {len(streams)} стримов")


# def start_websocket(symbols_list):
#     try:
#         url = "wss://fstream.binance.com/ws"
#         log(f"🔌 Подключение WebSocket для {len(symbols_list)} символов...")
#
#         ws = websocket.WebSocketApp(
#             url,
#             on_open=on_open,
#             on_message=on_message,
#             on_error=on_error,
#             on_close=on_close
#         )
#         ws.symbols = symbols_list
#
#         log(f"🚀 Запуск ws.run_forever()...")
#         ws.run_forever(ping_interval=20, ping_timeout=10)
#
#     except Exception as e:
#         log(f"❌ Ошибка в start_websocket: {e}")
#         log(traceback.format_exc())

def start_websocket(symbols_list):
    try:
        # Используем ссылку как для свечей - через /market/ws/
        # Подписываемся на все stream'ы сразу
        streams = [f"{s.lower()}usdt@depth@100ms" for s in symbols_list]
        url = f"wss://fstream.binance.com/market/ws/{'/'.join(streams)}"

        log(f"🔌 Подключение WebSocket: {url[:100]}...")

        ws = websocket.WebSocketApp(
            url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        ws.symbols = symbols_list

        log(f"🚀 Запуск ws.run_forever()...")
        ws.run_forever(ping_interval=20, ping_timeout=10)

    except Exception as e:
        log(f"❌ Ошибка в start_websocket: {e}")
        log(traceback.format_exc())


def scalp_monitor_loop():
    global symbols

    log("🚀 Scalp Monitor запущен (асинхронный)!")
    log(f"📝 Лог-файл: {LOG_FILE}")
    log(f"⏱️ Минимальный возраст: {MIN_AGE_SECONDS} сек")
    log(f"📊 Мониторинг топ-{TOP_SYMBOLS_COUNT} монет")
    time.sleep(5)

    # Запускаем процессор очереди сообщений
    processor_thread = threading.Thread(
        target=process_message_queue,
        name='Queue-Processor',
        daemon=True
    )
    processor_thread.start()
    log(f"✅ Процессор очереди запущен")

    symbols = get_top_symbols(TOP_SYMBOLS_COUNT)
    if not symbols:
        log("️ Нет символов для мониторинга")
        return

    log(f"🔄 Инициализация стаканов для {len(symbols)} символов...")
    for idx, symbol in enumerate(symbols, 1):
        init_order_book(symbol)
        time.sleep(0.05)
        if idx % 20 == 0:
            log(f"  Прогресс: {idx}/{len(symbols)}")

    log(f"✅ Все стаканы инициализированы")

    ws_thread = threading.Thread(
        target=start_websocket,
        args=(symbols,),
        name='Scalp-WebSocket',
        daemon=True
    )
    ws_thread.start()
    log(f"✅ WebSocket поток запущен")

    heartbeat_counter = 0
    while not shutdown_event.is_set():
        heartbeat_counter += 1
        if heartbeat_counter % 6 == 0:
            log(f"💓 Scalp Monitor: heartbeat (цикл {heartbeat_counter}), очередь: {message_queue.qsize()}")

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