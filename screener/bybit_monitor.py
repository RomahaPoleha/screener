"""
Bybit Monitor — мониторинг плотностей Bybit Futures
"""
import json
import time
import threading
import requests
import websocket
from queue import Queue, Empty
from django.core.cache import cache
import ccxt

# Глобальное состояние
bybit_futures_order_books = {}
bybit_futures_density_timestamps = {}
bybit_futures_order_books_lock = threading.Lock()
bybit_futures_symbols = []
bybit_futures_message_queue = Queue(maxsize=50000)

# URLs
BYBIT_FUTURES_WS_URL = "wss://stream.bybit.com/v5/public/linear"
BYBIT_FUTURES_REST_URL = "https://api.bybit.com/v5/market/orderbook?category=linear&symbol={}USDT&limit=50"

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


def get_top_symbols(limit=30):
    """Получает топ монет для Bybit Futures"""
    try:
        exchange = ccxt.bybit({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': 'linear'}
        })
        tickers = exchange.fetch_tickers()

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

        return symbols

    except Exception as e:
        print(f"❌ Ошибка в get_top_symbols(bybit): {e}")
        return []


def init_order_book(symbol, log_func=print):
    """Инициализация стакана Bybit"""
    try:
        url = BYBIT_FUTURES_REST_URL.format(symbol)

        order_books = bybit_futures_order_books
        timestamps = bybit_futures_density_timestamps
        lock = bybit_futures_order_books_lock

        res = requests.get(
            url,
            timeout=10,
            headers={'User-Agent': 'Mozilla/5.0'}
        )

        if not res.ok:
            log_func(f"⚠️ bybit {symbol}: HTTP {res.status_code}")
            return False

        try:
            data = res.json()
        except Exception:
            log_func(f"⚠️ bybit {symbol}: не JSON ответ: {res.text[:200]}")
            return False

        if data.get('retCode') != 0:
            log_func(
                f"⚠️ bybit {symbol}: retCode={data.get('retCode')} "
                f"retMsg={data.get('retMsg')}"
            )
            return False

        result = data.get('result') or {}

        raw_bids = result.get('b') or []
        raw_asks = result.get('a') or []

        bids = {}
        asks = {}

        for row in raw_bids:
            try:
                price = float(row[0])
                qty = float(row[1])

                if price > 0 and qty > 0:
                    bids[price] = qty

            except Exception:
                continue

        for row in raw_asks:
            try:
                price = float(row[0])
                qty = float(row[1])

                if price > 0 and qty > 0:
                    asks[price] = qty

            except Exception:
                continue

        if not bids and not asks:
            log_func(f"⚠️ bybit {symbol}: пустой стакан в REST ответе")
            return False

        with lock:
            order_books[symbol] = {
                'bids': bids,
                'asks': asks
            }
            timestamps[symbol] = {}

        sync_to_cache(symbol, log_func)

        log_func(
            f"✅ bybit futures Стакан {symbol}: "
            f"{len(bids)} bids, {len(asks)} asks"
        )

        return True

    except requests.exceptions.Timeout:
        log_func(f"❌ init_order_book(bybit {symbol}): timeout")
        return False

    except Exception as e:
        log_func(f"❌ init_order_book(bybit {symbol}): {e}")
        return False


def sync_to_cache(symbol, log_func=print):
    """Синхронизация стакана в Redis"""
    try:
        order_books = bybit_futures_order_books
        timestamps = bybit_futures_density_timestamps
        lock = bybit_futures_order_books_lock

        with lock:
            book = order_books.get(symbol, {})
            ts = timestamps.get(symbol, {})
            if not book:
                return

        key = f"scalp:futures:bybit:{symbol}"
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
                    'exchange': 'bybit'
                })

        cache.set(key, densities, CACHE_TTL)

        with lock:
            timestamps[symbol] = ts

    except Exception as e:
        log_func(f"❌ sync_to_cache(bybit {symbol}): {e}")


def update_order_book(symbol, bids_delta, asks_delta, log_func=print):
    """Обновление стакана данными из WebSocket"""
    global last_sync_time

    try:
        order_books = bybit_futures_order_books
        timestamps = bybit_futures_density_timestamps
        lock = bybit_futures_order_books_lock

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
            key = f"bybit:futures:{symbol}"
            if key not in last_sync_time or (now - last_sync_time[key]) >= 3:
                sync_to_cache(symbol, log_func)
                last_sync_time[key] = now

    except Exception as e:
        log_func(f"❌ update_order_book(bybit {symbol}): {e}")


def process_message_queue(log_func=print):
    message_queue = bybit_futures_message_queue

    while True:
        try:
            message = message_queue.get(timeout=1)
            data = json.loads(message)

            topic = data.get('topic', '')
            msg_type = data.get('type', '')

            if not topic.startswith('orderbook'):
                continue

            sym = topic.split('.')[2]
            symbol = sym[:-4] if sym.endswith('USDT') else sym
            d = data.get('data', {})
            bids = d.get('b', [])
            asks = d.get('a', [])

            # ✅ Обрабатываем snapshot как полную перезапись стакана
            if msg_type == 'snapshot':
                order_books = bybit_futures_order_books
                lock = bybit_futures_order_books_lock
                with lock:
                    order_books[symbol] = {
                        'bids': {float(p): float(q) for p, q in bids},
                        'asks': {float(p): float(q) for p, q in asks}
                    }
                sync_to_cache(symbol, log_func)
                continue

            # Delta обрабатываем как обычно
            if msg_type == 'delta' and (bids or asks):
                update_order_book(symbol, bids, asks, log_func)

        except Empty:
            continue
        except Exception as e:
            log_func(f"❌ bybit Ошибка обработки сообщения: {e}")


def on_message(ws, message):
    """WebSocket только складывает сообщения в очередь"""
    try:
        bybit_futures_message_queue.put_nowait(message)
    except Exception as e:
        print(f"❌ bybit on_message: {e}")


def on_open(ws):
    print(f"✅ bybit futures WebSocket открыт: {len(ws.symbols)} символов")

    args = [f"orderbook.50.{s}USDT" for s in ws.symbols]
    subscribe_msg = {
        "op": "subscribe",
        "args": args
    }
    ws.send(json.dumps(subscribe_msg))


def start_websocket(symbols_list, log_func=print):
    """Запуск WebSocket"""
    try:
        def custom_ping():
            return json.dumps({"op": "ping"})

        def on_pong(ws, message):
            pass  # Игнорируем pong ответы

        ws = websocket.WebSocketApp(
            BYBIT_FUTURES_WS_URL,
            on_open=lambda ws: on_open(ws),
            on_message=lambda ws, msg: on_message(ws, msg),
            on_error=lambda ws, err: log_func(f"❌ bybit WebSocket ошибка: {err}"),
            on_close=lambda ws, code, msg: log_func(f"⚠️ bybit WebSocket закрыт: {code} {msg}"),
            on_pong=on_pong
        )
        ws.symbols = symbols_list

        # ✅ КРИТИЧНО: custom_ping для Bybit v5
        ws.run_forever(
            ping_interval=20,
            ping_timeout=10,
            custom_ping=custom_ping
        )

    except Exception as e:
        log_func(f"❌ Ошибка в start_websocket(bybit): {e}")


def start_bybit_monitor(log_func=print):
    """Запуск мониторинга Bybit"""
    global bybit_futures_symbols

    log_func("🚀 Запуск Bybit Monitor...")

    # Запускаем процессор очереди
    threading.Thread(
        target=lambda: process_message_queue(log_func),
        daemon=True
    ).start()

    # Получаем топ монет
    bybit_futures_symbols = get_top_symbols(30)

    # Инициализация стаканов
    for symbol in bybit_futures_symbols:
        init_order_book(symbol, log_func)
        time.sleep(0.05)

    # Запуск WebSocket
    if bybit_futures_symbols:
        threading.Thread(
            target=lambda: start_websocket(bybit_futures_symbols, log_func),
            daemon=True
        ).start()

    log_func("✅ Bybit Monitor запущен")