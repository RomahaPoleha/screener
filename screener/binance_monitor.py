"""
Binance Monitor — мониторинг плотностей Binance Futures и Spot
"""
import json
import time
import threading
import requests
import websocket
from queue import Queue, Empty
from django.core.cache import cache
import ccxt

# Глобальное состояние для Futures
futures_order_books = {}
futures_density_timestamps = {}
futures_order_books_lock = threading.Lock()
futures_symbols = []
futures_message_queue = Queue(maxsize=50000)

# Глобальное состояние для Spot
spot_order_books = {}
spot_density_timestamps = {}
spot_order_books_lock = threading.Lock()
spot_symbols = []
spot_message_queue = Queue(maxsize=50000)

# URLs
FUTURES_WS_URL = "wss://fstream.binance.com/ws"
FUTURES_REST_URL = "https://fapi.binance.com/fapi/v1/depth"
SPOT_WS_URL = "wss://stream.binance.com:9443/ws"
SPOT_REST_URL = "https://api.binance.com/api/v3/depth"

# Rate limiting
last_sync_time = {}

# Минимальный возраст плотности
MIN_AGE_SECONDS = 180
CACHE_TTL = 900


def is_valid_symbol(symbol):
    if '-' in symbol:
        return False
    if len(symbol) < 2 or len(symbol) > 15:
        return False
    if not symbol.replace('_', '').isalnum():
        return False
    return True


def get_top_symbols(limit=30, market='futures'):
    """Получает топ монет для указанного рынка"""
    try:
        ccxt_market = 'future' if market == 'futures' else 'spot'
        exchange = ccxt.binance({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': ccxt_market}
        })
        tickers = exchange.fetch_tickers()

        symbols_with_score = []
        stablecoins = {'USDT', 'USDC', 'FDUSD', 'DAI', 'TUSD', 'BUSD', 'USDP', 'EURC'}

        # Минимальный объём для ликвидности (в USDT)
        MIN_LIQUIDITY_VOLUME = 10_000_000  # $10M

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

            # Фильтр по ликвидности
            if volume < MIN_LIQUIDITY_VOLUME:
                continue

            # Ограничиваем влияние волатильности
            volatility_factor = min(abs(percentage), 5.0)
            score = volume * volatility_factor

            if score > 0:
                symbols_with_score.append((clean_symbol, score))

        symbols_with_score.sort(key=lambda x: x[1], reverse=True)
        symbols = [s[0] for s in symbols_with_score[:limit]]

        return symbols

    except Exception as e:
        print(f"❌ Ошибка в get_top_symbols({market}): {e}")
        return []


def init_order_book(symbol, market, log_func=print):
    """Инициализация стакана Binance. Возвращает количество плотностей."""
    try:
        ccxt_market = 'future' if market == 'futures' else 'spot'
        exchange = ccxt.binance({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': ccxt_market}
        })
        ob = exchange.fetch_order_book(symbol, limit=100)

        if market == 'futures':
            order_books = binance_futures_order_books
            timestamps = binance_futures_density_timestamps
            lock = binance_futures_order_books_lock
        else:
            order_books = binance_spot_order_books
            timestamps = binance_spot_density_timestamps
            lock = binance_spot_order_books_lock

        bids = {}
        for price, qty in ob.get('bids', []):
            if price > 0 and qty > 0:
                bids[price] = qty

        asks = {}
        for price, qty in ob.get('asks', []):
            if price > 0 and qty > 0:
                asks[price] = qty

        if not bids and not asks:
            log_func(f"⚠️ binance {market} {symbol}: пустой стакан")
            return 0

        with lock:
            order_books[symbol] = {'bids': bids, 'asks': asks}
            timestamps[symbol] = {}

        saved_count = sync_to_cache(symbol, market, log_func)

        log_func(
            f"✅ binance {market} Стакан {symbol}: "
            f"{len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}"
        )

        return saved_count

    except Exception as e:
        log_func(f"❌ init_order_book(binance {market} {symbol}): {e}")
        return 0

def sync_to_cache(symbol, market, log_func=print):
    """Синхронизация стакана в Redis. Возвращает количество плотностей."""
    try:
        if market == 'futures':
            order_books = binance_futures_order_books
            timestamps = binance_futures_density_timestamps
            lock = binance_futures_order_books_lock
            key = f"scalp:futures:{symbol}"
        else:
            order_books = binance_spot_order_books
            timestamps = binance_spot_density_timestamps
            lock = binance_spot_order_books_lock
            key = f"scalp:spot:{symbol}"

        with lock:
            book = order_books.get(symbol, {})
            ts = timestamps.get(symbol, {})
            if not book:
                return 0

        now = time.time()
        densities = []
        is_first_load = len(ts) == 0

        for side, side_name in [('bids', 'buy'), ('asks', 'sell')]:
            for price, qty in book.get(side, {}).items():
                volume = price * qty

                if volume < 10000:
                    continue

                if price in ts:
                    age = now - ts[price]
                    if age < MIN_AGE_SECONDS:
                        continue
                else:
                    if is_first_load:
                        ts[price] = now - 20
                    else:
                        ts[price] = now
                        continue

                densities.append({
                    'price': price,
                    'volume': volume,
                    'side': side_name,
                    'timestamp': ts[price],
                    'exchange': 'binance'
                })

        cache.set(key, densities, CACHE_TTL)

        with lock:
            timestamps[symbol] = ts

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_to_cache(binance {market} {symbol}): {e}")
        return 0


def update_order_book(symbol, bids_delta, asks_delta, market='futures', log_func=print):
    """Обновление стакана данными из WebSocket"""
    global last_sync_time

    try:
        if market == 'futures':
            order_books = futures_order_books
            timestamps = futures_density_timestamps
            lock = futures_order_books_lock
        else:
            order_books = spot_order_books
            timestamps = spot_density_timestamps
            lock = spot_order_books_lock

        with lock:
            if symbol not in order_books:
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
            key = f"binance:{market}:{symbol}"
            if key not in last_sync_time or (now - last_sync_time[key]) >= 3:
                sync_to_cache(symbol, market, log_func)
                last_sync_time[key] = now

    except Exception as e:
        log_func(f"❌ update_order_book({market} {symbol}): {e}")


def process_message_queue(market='futures', log_func=print):
    """Обработка очереди сообщений"""
    if market == 'futures':
        message_queue = futures_message_queue
    else:
        message_queue = spot_message_queue

    while True:
        try:
            message = message_queue.get(timeout=1)
            data = json.loads(message)

            if 'data' in data:
                stream_data = data['data']
                symbol = stream_data.get('s', '')
                if symbol.endswith('USDT'):
                    symbol = symbol[:-4]
                bids = stream_data.get('b', [])
                asks = stream_data.get('a', [])
            elif 's' in data:
                symbol = data.get('s', '')
                if symbol.endswith('USDT'):
                    symbol = symbol[:-4]
                bids = data.get('b', [])
                asks = data.get('a', [])
            else:
                continue

            if bids or asks:
                update_order_book(symbol, bids, asks, market, log_func)

        except Empty:
            continue
        except Exception as e:
            log_func(f"❌ {market} Ошибка обработки сообщения: {e}")


def on_message(ws, message, market='futures'):
    """WebSocket только складывает сообщения в очередь"""
    try:
        if market == 'futures':
            message_queue = futures_message_queue
        else:
            message_queue = spot_message_queue

        message_queue.put_nowait(message)
    except Exception as e:
        print(f"❌ {market} on_message: {e}")


def on_open(ws, market='futures'):
    print(f"✅ binance {market} WebSocket открыт: {len(ws.symbols)} символов")

    streams = [f"{s.lower()}usdt@depth@100ms" for s in ws.symbols]
    subscribe_msg = {
        "method": "SUBSCRIBE",
        "params": streams,
        "id": 1
    }
    ws.send(json.dumps(subscribe_msg))


def start_websocket(symbols_list, market='futures', log_func=print):
    """Запуск WebSocket"""
    try:
        if market == 'futures':
            url = FUTURES_WS_URL
        else:
            url = SPOT_WS_URL

        ws = websocket.WebSocketApp(
            url,
            on_open=lambda ws: on_open(ws, market),
            on_message=lambda ws, msg: on_message(ws, msg, market),
            on_error=lambda ws, err: print(f"❌ {market} WebSocket ошибка: {err}"),
            on_close=lambda ws, code, msg: print(f"⚠️ {market} WebSocket закрыт: {code}")
        )
        ws.symbols = symbols_list

        ws.run_forever(ping_interval=20, ping_timeout=10)

    except Exception as e:
        log_func(f"❌ Ошибка в start_websocket: {e}")


def start_binance_monitor(log_func=print):
    """Запуск мониторинга Binance с валидацией монет"""
    global futures_symbols, spot_symbols

    log_func("🚀 Запуск Binance Monitor...")

    threading.Thread(
        target=lambda: process_message_queue(log_func),
        daemon=True
    ).start()

    TARGET = 30

    # --- Futures ---
    futures_candidates = get_top_symbols(60, 'futures')
    active_futures = []

    for symbol in futures_candidates:
        if len(active_futures) >= TARGET:
            break

        saved_count = init_order_book(symbol, 'futures', log_func)

        if saved_count > 0:
            active_futures.append(symbol)
            log_func(f"✅ binance futures {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ binance futures {symbol}: пропущен (нет плотностей > $10K)")

        time.sleep(0.05)

    futures_symbols = active_futures

    # --- Spot ---
    spot_candidates = get_top_symbols(60, 'spot')
    active_spot = []

    for symbol in spot_candidates:
        if len(active_spot) >= TARGET:
            break

        saved_count = init_order_book(symbol, 'spot', log_func)

        if saved_count > 0:
            active_spot.append(symbol)
            log_func(f"✅ binance spot {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ binance spot {symbol}: пропущен (нет плотностей > $10K)")

        time.sleep(0.05)

    spot_symbols = active_spot

    if futures_symbols:
        threading.Thread(
            target=lambda: start_websocket(futures_symbols, 'futures', log_func),
            daemon=True
        ).start()

    if spot_symbols:
        threading.Thread(
            target=lambda: start_websocket(spot_symbols, 'spot', log_func),
            daemon=True
        ).start()

    log_func(f"✅ Binance Monitor запущен. Futures: {len(futures_symbols)}, Spot: {len(spot_symbols)}")