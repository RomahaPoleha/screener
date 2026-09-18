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
from . import coin_selection

# Глобальное состояние
bybit_futures_order_books = {}
bybit_futures_density_timestamps = {}
bybit_futures_order_books_lock = threading.Lock()
bybit_futures_symbols = []
bybit_futures_message_queue = Queue(maxsize=50000)

# Глобальное состояние для Bybit Spot
bybit_spot_order_books = {}
bybit_spot_density_timestamps = {}
bybit_spot_order_books_lock = threading.Lock()
bybit_spot_symbols = []
bybit_spot_message_queue = Queue(maxsize=50000)

# URLs
BYBIT_FUTURES_WS_URL = "wss://stream.bybit.com/v5/public/linear"
BYBIT_FUTURES_REST_URL = "https://api.bybit.com/v5/market/orderbook?category=linear&symbol={}USDT&limit=200"
# URLs для Bybit Spot
BYBIT_SPOT_WS_URL = "wss://stream.bybit.com/v5/public/spot"
BYBIT_SPOT_REST_URL = "https://api.bybit.com/v5/market/orderbook?category=spot&symbol={}USDT&limit=200"

# Управление WebSocket для обновления списка
bybit_futures_ws_stop_event = threading.Event()
bybit_futures_ws_instance = None

# Управление WebSocket для Bybit Spot
bybit_spot_ws_stop_event = threading.Event()
bybit_spot_ws_instance = None

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
    """Отбор Bybit Futures по гибридной формуле (RVOL+NATR+%)"""
    try:
        exchange = ccxt.bybit({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': 'linear'}
        })
        tickers = exchange.fetch_tickers()

        # Накапливаем историю объёма для RVOL
        coin_selection.update_volume_history(tickers, coin_selection.clean_swap)

        # Отбор по гибридной формуле
        candidates = coin_selection.select_candidates(
            tickers, coin_selection.clean_swap, limit=limit * 2,
            log_func=lambda msg: print(msg)
        )

        return candidates[:limit]

    except Exception as e:
        print(f"❌ Ошибка в get_top_symbols(bybit): {e}")
        return []


def get_top_spot_symbols(limit=30):
    """Отбор Bybit Spot по гибридной формуле (RVOL+NATR+%)"""
    try:
        exchange = ccxt.bybit({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': 'spot'}
        })
        tickers = exchange.fetch_tickers()

        # Накапливаем историю объёма для RVOL
        coin_selection.update_volume_history(tickers, coin_selection.clean_spot)

        # Отбор по гибридной формуле
        candidates = coin_selection.select_candidates(
            tickers, coin_selection.clean_spot, limit=limit * 2,
            log_func=lambda msg: print(msg)
        )

        return candidates[:limit]

    except Exception as e:
        print(f"❌ Ошибка в get_top_spot_symbols(bybit spot): {e}")
        return []

def init_order_book(symbol, log_func=print):
    """Инициализация стакана Bybit Futures. Возвращает количество плотностей."""
    try:
        url = BYBIT_FUTURES_REST_URL.format(symbol)

        order_books = bybit_futures_order_books
        timestamps = bybit_futures_density_timestamps
        lock = bybit_futures_order_books_lock

        res = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})

        if not res.ok:
            log_func(f"⚠️ bybit {symbol}: HTTP {res.status_code}")
            return 0

        try:
            data = res.json()
        except Exception:
            log_func(f"⚠️ bybit {symbol}: не JSON ответ: {res.text[:200]}")
            return 0

        if data.get('retCode') != 0:
            log_func(f"⚠️ bybit {symbol}: retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
            return 0

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
            return 0

        with lock:
            order_books[symbol] = {'bids': bids, 'asks': asks}
            timestamps[symbol] = {}

        saved_count = sync_to_cache(symbol, log_func)

        log_func(
            f"✅ bybit futures Стакан {symbol}: "
            f"{len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}"
        )

        return saved_count

    except requests.exceptions.Timeout:
        log_func(f"❌ init_order_book(bybit {symbol}): timeout")
        return 0
    except Exception as e:
        log_func(f"❌ init_order_book(bybit {symbol}): {e}")
        return 0


def init_spot_order_book(symbol, log_func=print):
    """Инициализация стакана Bybit Spot. Возвращает количество плотностей."""
    try:
        url = BYBIT_SPOT_REST_URL.format(symbol)

        order_books = bybit_spot_order_books
        timestamps = bybit_spot_density_timestamps
        lock = bybit_spot_order_books_lock

        res = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})

        if not res.ok:
            log_func(f"⚠️ bybit spot {symbol}: HTTP {res.status_code}")
            return 0

        try:
            data = res.json()
        except Exception:
            log_func(f"⚠️ bybit spot {symbol}: не JSON ответ: {res.text[:200]}")
            return 0

        if data.get('retCode') != 0:
            log_func(f"⚠️ bybit spot {symbol}: retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
            return 0

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
            log_func(f"⚠️ bybit spot {symbol}: пустой стакан в REST ответе")
            return 0

        with lock:
            order_books[symbol] = {'bids': bids, 'asks': asks}
            timestamps[symbol] = {}

        saved_count = sync_spot_to_cache(symbol, log_func)

        log_func(
            f"✅ bybit spot Стакан {symbol}: "
            f"{len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}"
        )

        return saved_count

    except requests.exceptions.Timeout:
        log_func(f"❌ init_spot_order_book(bybit spot {symbol}): timeout")
        return 0
    except Exception as e:
        log_func(f"❌ init_spot_order_book(bybit spot {symbol}): {e}")
        return 0

def sync_to_cache(symbol, log_func=print):
    """Синхронизация стакана в Redis. Возвращает количество сохранённых плотностей."""
    try:
        order_books = bybit_futures_order_books
        timestamps = bybit_futures_density_timestamps
        lock = bybit_futures_order_books_lock

        with lock:
            book = order_books.get(symbol, {})
            ts = timestamps.get(symbol, {})
            if not book:
                return 0

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

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_to_cache(bybit {symbol}): {e}")
        return 0


def sync_spot_to_cache(symbol, log_func=print):
    """Синхронизация стакана Bybit Spot в Redis. Возвращает количество плотностей."""
    try:
        order_books = bybit_spot_order_books
        timestamps = bybit_spot_density_timestamps
        lock = bybit_spot_order_books_lock

        with lock:
            book = order_books.get(symbol, {})
            ts = timestamps.get(symbol, {})
            if not book:
                return 0

        key = f"scalp:spot:bybit:{symbol}"
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

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_spot_to_cache(bybit spot {symbol}): {e}")
        return 0


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


def update_spot_order_book(symbol, bids_delta, asks_delta, log_func=print):
    """Обновление стакана Bybit Spot данными из WebSocket"""
    global last_sync_time

    try:
        order_books = bybit_spot_order_books
        timestamps = bybit_spot_density_timestamps
        lock = bybit_spot_order_books_lock

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
            key = f"bybit:spot:{symbol}"

            if key not in last_sync_time or (now - last_sync_time[key]) >= 3:
                sync_spot_to_cache(symbol, log_func)
                last_sync_time[key] = now

    except Exception as e:
        log_func(f"❌ update_spot_order_book(bybit spot {symbol}): {e}")



def process_message_queue(log_func=print):
    """Обработка очереди сообщений Bybit Futures"""
    message_queue = bybit_futures_message_queue

    while True:
        try:
            message = message_queue.get(timeout=1)
            data = json.loads(message)

            topic = data.get('topic', '')
            msg_type = data.get('type', '')

            if not topic.startswith('orderbook'):
                continue

            parts = topic.split('.')

            if len(parts) < 3:
                continue

            sym = parts[2]
            symbol = sym[:-4] if sym.endswith('USDT') else sym

            d = data.get('data', {})
            bids = d.get('b', [])
            asks = d.get('a', [])

            if msg_type == 'snapshot':
                order_books = bybit_futures_order_books
                timestamps = bybit_futures_density_timestamps
                lock = bybit_futures_order_books_lock

                new_bids = {}
                new_asks = {}

                for price_str, qty_str in bids:
                    try:
                        price = float(price_str)
                        qty = float(qty_str)

                        if price > 0 and qty > 0:
                            new_bids[price] = qty

                    except Exception:
                        continue

                for price_str, qty_str in asks:
                    try:
                        price = float(price_str)
                        qty = float(qty_str)

                        if price > 0 and qty > 0:
                            new_asks[price] = qty

                    except Exception:
                        continue

                with lock:
                    old_ts = timestamps.get(symbol, {})
                    new_ts = {}

                    for price in new_bids:
                        if price in old_ts:
                            new_ts[price] = old_ts[price]

                    for price in new_asks:
                        if price in old_ts:
                            new_ts[price] = old_ts[price]

                    order_books[symbol] = {
                        'bids': new_bids,
                        'asks': new_asks
                    }
                    timestamps[symbol] = new_ts

                sync_to_cache(symbol, log_func)
                continue

            if msg_type == 'delta' and (bids or asks):
                update_order_book(symbol, bids, asks, log_func)

        except Empty:
            continue
        except Exception as e:
            log_func(f"❌ bybit futures Ошибка обработки сообщения: {e}")


def process_spot_message_queue(log_func=print):
    """Обработка очереди сообщений Bybit Spot"""
    message_queue = bybit_spot_message_queue

    while True:
        try:
            message = message_queue.get(timeout=1)
            data = json.loads(message)

            topic = data.get('topic', '')
            msg_type = data.get('type', '')

            if not topic.startswith('orderbook'):
                continue

            parts = topic.split('.')

            if len(parts) < 3:
                continue

            sym = parts[2]
            symbol = sym[:-4] if sym.endswith('USDT') else sym

            d = data.get('data', {})
            bids = d.get('b', [])
            asks = d.get('a', [])

            if msg_type == 'snapshot':
                order_books = bybit_spot_order_books
                timestamps = bybit_spot_density_timestamps
                lock = bybit_spot_order_books_lock

                new_bids = {}
                new_asks = {}

                for price_str, qty_str in bids:
                    try:
                        price = float(price_str)
                        qty = float(qty_str)

                        if price > 0 and qty > 0:
                            new_bids[price] = qty

                    except Exception:
                        continue

                for price_str, qty_str in asks:
                    try:
                        price = float(price_str)
                        qty = float(qty_str)

                        if price > 0 and qty > 0:
                            new_asks[price] = qty

                    except Exception:
                        continue

                with lock:
                    old_ts = timestamps.get(symbol, {})
                    new_ts = {}

                    for price in new_bids:
                        if price in old_ts:
                            new_ts[price] = old_ts[price]

                    for price in new_asks:
                        if price in old_ts:
                            new_ts[price] = old_ts[price]

                    order_books[symbol] = {
                        'bids': new_bids,
                        'asks': new_asks
                    }
                    timestamps[symbol] = new_ts

                sync_spot_to_cache(symbol, log_func)
                continue

            if msg_type == 'delta' and (bids or asks):
                update_spot_order_book(symbol, bids, asks, log_func)

        except Empty:
            continue
        except Exception as e:
            log_func(f"❌ bybit spot Ошибка обработки сообщения: {e}")

def on_message(ws, message):
    """WebSocket только складывает сообщения в очередь"""
    try:
        bybit_futures_message_queue.put_nowait(message)
    except Exception as e:
        print(f"❌ bybit on_message: {e}")


def on_open(ws):
    print(f"✅ bybit futures WebSocket открыт: {len(ws.symbols)} символов")

    args = [f"orderbook.200.{s}USDT" for s in ws.symbols]
    subscribe_msg = {
        "op": "subscribe",
        "args": args
    }
    ws.send(json.dumps(subscribe_msg))


def on_open_spot(ws):
    print(f"✅ bybit spot WebSocket открыт: {len(ws.symbols)} символов")

    args = [f"orderbook.200.{s}USDT" for s in ws.symbols]

    subscribe_msg = {
        "op": "subscribe",
        "args": args
    }

    ws.send(json.dumps(subscribe_msg))

def start_websocket(symbols_list, log_func=print):
    """Запуск WebSocket Bybit Futures с переподключением и поддержкой остановки"""
    global bybit_futures_ws_stop_event, bybit_futures_ws_instance

    while not bybit_futures_ws_stop_event.is_set():
        ws = None
        stop_event = threading.Event()

        try:
            ws = websocket.WebSocketApp(
                BYBIT_FUTURES_WS_URL,
                on_open=lambda ws: on_open(ws),
                on_message=lambda ws, msg: on_message(ws, msg),
                on_error=lambda ws, err: log_func(f"❌ bybit futures WebSocket ошибка: {err}"),
                on_close=lambda ws, code, msg: log_func(f"⚠️ bybit futures WebSocket закрыт: {code} {msg}")
            )

            ws.symbols = symbols_list
            bybit_futures_ws_instance = ws

            def heartbeat():
                while not stop_event.is_set():
                    time.sleep(15)
                    try:
                        if ws and ws.sock and ws.sock.connected:
                            ws.send(json.dumps({"op": "ping"}))
                    except Exception as e:
                        log_func(f"❌ bybit futures heartbeat: {e}")
                        break

            threading.Thread(target=heartbeat, daemon=True).start()

            ws.run_forever(ping_interval=20, ping_timeout=10)

        except Exception as e:
            log_func(f"❌ Ошибка в start_websocket(bybit futures): {e}")

        finally:
            stop_event.set()
            bybit_futures_ws_instance = None

        if bybit_futures_ws_stop_event.is_set():
            log_func("🛑 Bybit futures WebSocket остановлен для обновления списка")
            break

        log_func("🔁 Bybit futures WebSocket переподключение через 3 секунды...")
        time.sleep(3)

def start_spot_websocket(symbols_list, log_func=print):
    """Запуск WebSocket Bybit Spot с переподключением и поддержкой остановки"""
    global bybit_spot_ws_stop_event, bybit_spot_ws_instance

    while not bybit_spot_ws_stop_event.is_set():
        ws = None
        stop_event = threading.Event()

        try:
            ws = websocket.WebSocketApp(
                BYBIT_SPOT_WS_URL,
                on_open=lambda ws: on_open_spot(ws),
                on_message=lambda ws, msg: on_message_spot(ws, msg),
                on_error=lambda ws, err: log_func(f"❌ bybit spot WebSocket ошибка: {err}"),
                on_close=lambda ws, code, msg: log_func(f"⚠️ bybit spot WebSocket закрыт: {code} {msg}")
            )

            ws.symbols = symbols_list
            bybit_spot_ws_instance = ws

            def heartbeat():
                while not stop_event.is_set():
                    time.sleep(15)
                    try:
                        if ws and ws.sock and ws.sock.connected:
                            ws.send(json.dumps({"op": "ping"}))
                    except Exception as e:
                        log_func(f"❌ bybit spot heartbeat: {e}")
                        break

            threading.Thread(target=heartbeat, daemon=True).start()

            ws.run_forever(ping_interval=20, ping_timeout=10)

        except Exception as e:
            log_func(f"❌ Ошибка в start_spot_websocket(bybit spot): {e}")

        finally:
            stop_event.set()
            bybit_spot_ws_instance = None

        if bybit_spot_ws_stop_event.is_set():
            log_func("🛑 Bybit spot WebSocket остановлен для обновления списка")
            break

        log_func("🔁 Bybit spot WebSocket переподключение через 3 секунды...")
        time.sleep(3)



def start_bybit_monitor(log_func=print):
    """Запуск мониторинга Bybit Futures с валидацией монет"""
    global bybit_futures_symbols

    log_func("🚀 Запуск Bybit Monitor...")

    threading.Thread(
        target=lambda: process_message_queue(log_func),
        daemon=True
    ).start()

    # Берём кандидатов с запасом
    candidates = get_top_symbols(60)
    active_symbols = []
    TARGET = 20

    for symbol in candidates:
        if len(active_symbols) >= TARGET:
            break

        saved_count = init_order_book(symbol, log_func)

        if saved_count > 0:
            active_symbols.append(symbol)
            log_func(f"✅ bybit futures {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ bybit futures {symbol}: пропущен (нет плотностей > $10K)")

        time.sleep(0.05)

    bybit_futures_symbols = active_symbols

    if bybit_futures_symbols:
        threading.Thread(
            target=lambda: start_websocket(bybit_futures_symbols, log_func),
            daemon=True
        ).start()

    log_func(f"✅ Bybit Monitor запущен. Активных монет: {len(bybit_futures_symbols)}")

    # Запуск периодического обновления списка монет
    threading.Thread(
        target=lambda: periodic_bybit_futures_refresh(log_func),
        daemon=True
    ).start()

def refresh_bybit_futures_symbols(log_func=print):
    """Частичная ротация списка монет Bybit Futures"""
    global bybit_futures_symbols, bybit_futures_ws_stop_event, bybit_futures_ws_instance

    log_func("🔄 Обновление списка Bybit Futures...")

    old_symbols = set(bybit_futures_symbols)

    # Пересчитываем топ монет
    candidates = get_top_symbols(60)

    # Формируем новый список
    new_active = []
    TARGET = 30

    for symbol in candidates:
        if len(new_active) >= TARGET:
            break

        # Если монета уже в мониторинге — оставляем без реинициализации
        if symbol in old_symbols:
            new_active.append(symbol)
            continue

        # Новая монета — инициализируем с валидацией
        saved_count = init_order_book(symbol, log_func)

        if saved_count > 0:
            new_active.append(symbol)
            log_func(f"✅ bybit futures {symbol}: добавлен (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ bybit futures {symbol}: пропущен (нет плотностей > $10K)")

        time.sleep(0.05)

    new_symbols = set(new_active)

    # Находим изменения
    removed = old_symbols - new_symbols
    added = new_symbols - old_symbols

    # Удаляем старые монеты из памяти
    if removed:
        with bybit_futures_order_books_lock:
            for symbol in removed:
                bybit_futures_order_books.pop(symbol, None)
                bybit_futures_density_timestamps.pop(symbol, None)

        log_func(f"🗑️ bybit futures удалены: {', '.join(sorted(removed))}")

    # Обновляем глобальный список
    bybit_futures_symbols = new_active

    # Если список изменился — переподключаем WebSocket
    if removed or added:
        log_func(f"🔄 bybit futures: добавлено {len(added)}, удалено {len(removed)}")

        # Останавливаем текущий WebSocket
        bybit_futures_ws_stop_event.set()

        if bybit_futures_ws_instance:
            try:
                bybit_futures_ws_instance.close()
            except Exception:
                pass

        time.sleep(2)

        # Сбрасываем stop_event для нового запуска
        bybit_futures_ws_stop_event.clear()

        # Запускаем новый WebSocket с обновлённым списком
        if bybit_futures_symbols:
            threading.Thread(
                target=lambda: start_websocket(bybit_futures_symbols, log_func),
                daemon=True
            ).start()
    else:
        log_func("✅ bybit futures: список не изменился")


def start_bybit_spot_monitor(log_func=print):
    """Запуск мониторинга Bybit Spot с валидацией монет"""
    global bybit_spot_symbols

    log_func("🚀 Запуск Bybit Spot Monitor...")

    threading.Thread(
        target=lambda: process_spot_message_queue(log_func),
        daemon=True
    ).start()

    # Берём кандидатов с запасом
    candidates = get_top_spot_symbols(60)
    active_symbols = []
    TARGET = 30

    for symbol in candidates:
        if len(active_symbols) >= TARGET:
            break

        saved_count = init_spot_order_book(symbol, log_func)

        if saved_count > 0:
            active_symbols.append(symbol)
            log_func(f"✅ bybit spot {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ bybit spot {symbol}: пропущен (нет плотностей > $10K)")

        time.sleep(0.05)

    bybit_spot_symbols = active_symbols

    if bybit_spot_symbols:
        threading.Thread(
            target=lambda: start_spot_websocket(bybit_spot_symbols, log_func),
            daemon=True
        ).start()

    log_func(f"✅ Bybit Spot Monitor запущен. Активных монет: {len(bybit_spot_symbols)}")

    threading.Thread(
        target=lambda: periodic_bybit_spot_refresh(log_func),
        daemon=True
    ).start()

def refresh_bybit_spot_symbols(log_func=print):
    """Частичная ротация списка монет Bybit Spot"""
    global bybit_spot_symbols, bybit_spot_ws_stop_event, bybit_spot_ws_instance

    log_func("🔄 Обновление списка Bybit Spot...")

    old_symbols = set(bybit_spot_symbols)

    candidates = get_top_spot_symbols(60)

    new_active = []
    TARGET = 30

    for symbol in candidates:
        if len(new_active) >= TARGET:
            break

        if symbol in old_symbols:
            new_active.append(symbol)
            continue

        saved_count = init_spot_order_book(symbol, log_func)

        if saved_count > 0:
            new_active.append(symbol)
            log_func(f"✅ bybit spot {symbol}: добавлен (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ bybit spot {symbol}: пропущен (нет плотностей > $10K)")

        time.sleep(0.05)

    new_symbols = set(new_active)

    removed = old_symbols - new_symbols
    added = new_symbols - old_symbols

    if removed:
        with bybit_spot_order_books_lock:
            for symbol in removed:
                bybit_spot_order_books.pop(symbol, None)
                bybit_spot_density_timestamps.pop(symbol, None)

        log_func(f"🗑️ bybit spot удалены: {', '.join(sorted(removed))}")

    bybit_spot_symbols = new_active

    if removed or added:
        log_func(f"🔄 bybit spot: добавлено {len(added)}, удалено {len(removed)}")

        bybit_spot_ws_stop_event.set()

        if bybit_spot_ws_instance:
            try:
                bybit_spot_ws_instance.close()
            except Exception:
                pass

        time.sleep(2)

        bybit_spot_ws_stop_event.clear()

        if bybit_spot_symbols:
            threading.Thread(
                target=lambda: start_spot_websocket(bybit_spot_symbols, log_func),
                daemon=True
            ).start()
    else:
        log_func("✅ bybit spot: список не изменился")


def periodic_bybit_spot_refresh(log_func=print):
    """Периодическое обновление списка монет Bybit Spot"""
    REFRESH_INTERVAL = 300  # 5 минут

    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            refresh_bybit_spot_symbols(log_func)
        except Exception as e:
            log_func(f"❌ Ошибка при обновлении списка Bybit Spot: {e}")


def periodic_bybit_futures_refresh(log_func=print):
    """Периодическое обновление списка монет каждые 30 минут"""
    REFRESH_INTERVAL = 300  # 30 минут в секундах

    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            refresh_bybit_futures_symbols(log_func)
        except Exception as e:
            log_func(f"❌ Ошибка при обновлении списка Bybit Futures: {e}")

def on_message_spot(ws, message):
    """WebSocket Bybit Spot складывает сообщения в очередь"""
    try:
        bybit_spot_message_queue.put_nowait(message)
    except Exception as e:
        print(f"❌ bybit spot on_message: {e}")