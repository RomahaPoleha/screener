"""
Scalp Monitor — фоновый мониторинг плотностей в реальном времени
WebSocket + Django cache. Хранит только плотности старше 1 минуты.
Поддерживает Futures и Spot.
"""
import os
import json
import time
import threading
import traceback
import requests
from logging.handlers import RotatingFileHandler
from django.core.cache import cache

# Минимальный объём для записи в кэш (USDT) — глобальный порог
GLOBAL_MIN_VOLUME = 5000

# Минимальное время жизни плотности (секунды) — младше не храним
MIN_AGE_SECONDS = 60

# Количество монет для мониторинга
TOP_SYMBOLS_COUNT = 200

# Максимум монет на одно WebSocket подключение
MAX_SYMBOLS_PER_WS = 70

# TTL кэша (15 минут без обновлений = удаление)
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
    """Получить топ монет по объёму (Futures)"""
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

        log(f"✅ Топ-{limit}: найдено {len(symbols)} монет для мониторинга")
        return symbols

    except Exception as e:
        log(f"❌ Ошибка в get_top_symbols(): {e}")
        log(traceback.format_exc())
        return []


# Глобальное состояние
order_books = {}  # {symbol: {'bids': {price: qty}, 'asks': {price: qty}}}
density_timestamps = {}  # {symbol: {price: timestamp}} — когда появилась плотность
order_books_lock = threading.Lock()
symbols = []
shutdown_event = threading.Event()


def init_order_book(symbol, market='future'):
    """Загрузить полный стакан через REST API"""
    try:
        if market == 'future':
            url = f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol}USDT&limit=1000"
        else:
            url = f"https://api.binance.com/api/v3/depth?symbol={symbol}USDT&limit=1000"

        res = requests.get(url, timeout=10)
        if not res.ok:
            return False

        data = res.json()
        bids = {float(price): float(qty) for price, qty in data.get('bids', [])}
        asks = {float(price): float(qty) for price, qty in data.get('asks', [])}

        book_key = f"{market}_{symbol}"
        with order_books_lock:
            order_books[book_key] = {'bids': bids, 'asks': asks}
            density_timestamps[book_key] = {}

        sync_to_cache(symbol, market)

        log(f"✅ Инициализирован стакан для {market.upper()} {symbol}: {len(bids)} bids, {len(asks)} asks")
        return True

    except Exception as e:
        log(f"❌ Ошибка init_order_book({market} {symbol}): {e}")
        return False


def sync_to_cache(symbol, market='future'):
    """
    Синхронизировать локальный стакан с Django cache.
    ВАЖНО: записываем только плотности старше MIN_AGE_SECONDS (60 сек).
    """
    try:
        book_key = f"{market}_{symbol}"
        with order_books_lock:
            book = order_books.get(book_key, {})
            timestamps = density_timestamps.get(book_key, {})
            if not book:
                return

        key = f"scalp:{market}:{symbol}"
        now = time.time()

        densities = []

        for side, side_name in [('bids', 'buy'), ('asks', 'sell')]:
            for price, qty in book.get(side, {}).items():
                volume = price * qty

                if volume < GLOBAL_MIN_VOLUME:
                    continue

                # Проверяем возраст плотности
                if price in timestamps:
                    age = now - timestamps[price]
                    if age < MIN_AGE_SECONDS:
                        continue
                else:
                    # Новая плотность — запоминаем timestamp
                    timestamps[price] = now
                    continue

                # Плотность старше 1 минуты — добавляем
                densities.append({
                    'price': price,
                    'volume': volume,
                    'side': side_name,
                    'timestamp': timestamps[price]
                })

        # Сохраняем в cache с TTL
        cache.set(key, densities, CACHE_TTL)

        # Сохраняем обновлённые timestamps
        with order_books_lock:
            density_timestamps[book_key] = timestamps

    except Exception as e:
        log(f"❌ Ошибка sync_to_cache({market} {symbol}): {e}")


def update_order_book(symbol, market, bids_delta, asks_delta):
    """Обновить локальный стакан дельтами из WebSocket"""
    try:
        book_key = f"{market}_{symbol}"
        with order_books_lock:
            if book_key not in order_books:
                return

            book = order_books[book_key]
            timestamps = density_timestamps.get(book_key, {})
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
            sync_to_cache(symbol, market)

    except Exception as e:
        log(f"❌ Ошибка update_order_book({market} {symbol}): {e}")


def make_on_message(market):
    """Создать обработчик сообщений WebSocket для конкретного рынка"""
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
                    update_order_book(symbol, market, bids, asks)

        except Exception as e:
            log(f"❌ Ошибка on_message ({market}): {e}")

    return on_message


def make_on_error(market):
    def on_error(ws, error):
        log(f"❌ WebSocket ошибка ({market}): {error}")
    return on_error


def make_on_close(market, symbols_list):
    def on_close(ws, close_status_code, close_msg):
        log(f"⚠️ WebSocket закрыт ({market}): {close_status_code} {close_msg}")
        log(f"🔄 Переподключение {market} через 5 секунд...")
        time.sleep(5)

        try:
            import websocket
            if market == 'future':
                streams = [f"{s.lower()}usdt@depth@100ms" for s in symbols_list]
                url = f"wss://fstream.binance.com/stream?streams={'/'.join(streams)}"
            else:
                streams = [f"{s.lower()}usdt@depth@100ms" for s in symbols_list]
                url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"

            new_ws = websocket.WebSocketApp(
                url,
                on_open=lambda ws: on_open(ws, market),
                on_message=make_on_message(market),
                on_error=make_on_error(market),
                on_close=make_on_close(market, symbols_list)
            )
            new_ws.symbol_group = ws.symbol_group
            new_ws.symbols = symbols_list
            new_ws.market = market

            new_ws.run_forever()
        except Exception as e:
            log(f"❌ Ошибка переподключения ({market}): {e}")

    return on_close


def on_open(ws, market=None):
    symbol_group = ws.symbol_group
    m = market or getattr(ws, 'market', 'unknown')
    log(f"✅ WebSocket открыт ({m}) для группы {symbol_group}: {len(ws.symbols)} символов")

    streams = [f"{s.lower()}usdt@depth@100ms" for s in ws.symbols]
    subscribe_msg = {
        "method": "SUBSCRIBE",
        "params": streams,
        "id": 1
    }
    ws.send(json.dumps(subscribe_msg))


def start_websocket_connection(symbol_group, symbols_list, market='future'):
    """Запустить одно WebSocket подключение для группы символов"""
    try:
        import websocket

        if not symbols_list:
            return

        log(f"🔄 Инициализация стаканов ({market}) для группы {symbol_group} ({len(symbols_list)} символов)...")
        for symbol in symbols_list:
            init_order_book(symbol, market)
            time.sleep(0.05)

        streams = [f"{s.lower()}usdt@depth@100ms" for s in symbols_list]

        if market == 'future':
            url = f"wss://fstream.binance.com/stream?streams={'/'.join(streams)}"
        else:
            url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"

        log(f"🔌 Подключение WebSocket ({market}) для группы {symbol_group}...")

        ws = websocket.WebSocketApp(
            url,
            on_open=lambda ws: on_open(ws, market),
            on_message=make_on_message(market),
            on_error=make_on_error(market),
            on_close=make_on_close(market, symbols_list)
        )
        ws.symbol_group = symbol_group
        ws.symbols = symbols_list
        ws.market = market

        ws.run_forever()

    except Exception as e:
        log(f"❌ Ошибка start_websocket_connection({market} {symbol_group}): {e}")
        log(traceback.format_exc())


def start_all_websocket_connections():
    """Запустить все WebSocket подключения для Futures и Spot"""
    global symbols

    if not symbols:
        symbols = get_top_symbols(TOP_SYMBOLS_COUNT)
        if not symbols:
            log("⚠️ Нет символов для мониторинга")
            return

    num_groups = (len(symbols) + MAX_SYMBOLS_PER_WS - 1) // MAX_SYMBOLS_PER_WS
    symbol_groups = [symbols[i::num_groups] for i in range(num_groups)]

    log(f"🚀 Запуск WebSocket подключений для Futures и Spot...")

    # Futures подключения
    for idx, symbol_group in enumerate(symbol_groups):
        thread = threading.Thread(
            target=start_websocket_connection,
            args=(f"F{idx}", symbol_group, 'future'),
            name=f'Scalp-Future-{idx}',
            daemon=True
        )
        thread.start()
        time.sleep(1)

    # Spot подключения
    for idx, symbol_group in enumerate(symbol_groups):
        thread = threading.Thread(
            target=start_websocket_connection,
            args=(f"S{idx}", symbol_group, 'spot'),
            name=f'Scalp-Spot-{idx}',
            daemon=True
        )
        thread.start()
        time.sleep(1)


def scalp_monitor_loop():
    setup_excepthook()
    log("🚀 Scalp Monitor запущен (WebSocket + Django cache, Futures + Spot)!")
    log(f"📝 Лог-файл: {LOG_FILE}")
    log(f"⏱️ Минимальный возраст плотности: {MIN_AGE_SECONDS} сек")
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
            log(f"❌ Ошибка в цикле Scalp Monitor: {e}")
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
    log(f"✅ Scalp Monitor поток запущен (daemon=True, PID: {thread.ident})")


def stop_scalp_monitor():
    log("📤 Отправка сигнала остановки Scalp Monitor...")
    shutdown_event.set()