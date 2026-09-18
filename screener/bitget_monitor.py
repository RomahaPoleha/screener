"""
Bitget Monitor — мониторинг плотностей Bitget Futures и Spot
Формат WS: {"action":"snapshot/update","arg":{"channel":"books15","instId":"BTCUSDT","instType":"USDT-FUTURES"},"data":[{"bids":[[p,q],...],"asks":[[p,q],...]}]}
Формат REST: {"code":"00000","data":{"bids":[[p,q],...],"asks":[[p,q],...]}}
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

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — FUTURES
# ==========================================
bitget_futures_order_books = {}
bitget_futures_density_timestamps = {}
bitget_futures_order_books_lock = threading.Lock()
bitget_futures_symbols = []
bitget_futures_message_queue = Queue(maxsize=50000)

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — SPOT
# ==========================================
bitget_spot_order_books = {}
bitget_spot_density_timestamps = {}
bitget_spot_order_books_lock = threading.Lock()
bitget_spot_symbols = []
bitget_spot_message_queue = Queue(maxsize=50000)

# URLs
BITGET_FUTURES_WS_URL = "wss://ws.bitget.com/v2/ws/public"
BITGET_FUTURES_REST_URL = "https://api.bitget.com/api/v2/mix/market/merge-depth?symbol={}USDT&productType=USDT-FUTURES&limit=100"
BITGET_SPOT_WS_URL = "wss://ws.bitget.com/v2/ws/public"
BITGET_SPOT_REST_URL = "https://api.bitget.com/api/v2/spot/market/merge-depth?symbol={}USDT&limit=100"

# Управление WebSocket
bitget_futures_ws_stop_event = threading.Event()
bitget_futures_ws_instance = None
bitget_spot_ws_stop_event = threading.Event()
bitget_spot_ws_instance = None

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


# ==========================================
# ТОП МОНЕТ (гибридная формула)
# ==========================================
def get_top_symbols(limit=30):
    """Отбор Bitget Futures по гибридной формуле (RVOL+NATR+%)"""
    try:
        exchange = ccxt.bitget({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': 'swap'}
        })
        tickers = exchange.fetch_tickers()

        coin_selection.update_volume_history(tickers, coin_selection.clean_swap)
        candidates = coin_selection.select_candidates(
            tickers, coin_selection.clean_swap, limit=limit * 2,
            log_func=lambda msg: print(msg)
        )
        return candidates[:limit]
    except Exception as e:
        print(f"❌ Ошибка в get_top_symbols(bitget futures): {e}")
        return []


def get_top_spot_symbols(limit=30):
    """Отбор Bitget Spot по гибридной формуле (RVOL+NATR+%)"""
    try:
        exchange = ccxt.bitget({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': 'spot'}
        })
        tickers = exchange.fetch_tickers()

        coin_selection.update_volume_history(tickers, coin_selection.clean_spot)
        candidates = coin_selection.select_candidates(
            tickers, coin_selection.clean_spot, limit=limit * 2,
            log_func=lambda msg: print(msg)
        )
        return candidates[:limit]
    except Exception as e:
        print(f"❌ Ошибка в get_top_spot_symbols(bitget spot): {e}")
        return []


# ==========================================
# ИНИЦИАЛИЗАЦИЯ СТАКАНОВ ЧЕРЕЗ REST
# ==========================================
def init_order_book(symbol, log_func=print):
    """Инициализация стакана Bitget Futures через REST"""
    try:
        url = BITGET_FUTURES_REST_URL.format(symbol)
        res = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})

        if not res.ok:
            log_func(f"⚠️ bitget futures {symbol}: HTTP {res.status_code}")
            return 0

        try:
            data = res.json()
        except Exception:
            log_func(f"⚠️ bitget futures {symbol}: не JSON: {res.text[:200]}")
            return 0

        if data.get('code') != '00000':
            log_func(f"⚠️ bitget futures {symbol}: code={data.get('code')} msg={data.get('msg')}")
            return 0

        result = data.get('data') or {}
        raw_bids = result.get('bids') or []
        raw_asks = result.get('asks') or []

        bids = {}
        asks = {}

        # Bitget REST формат: [[price, qty], ...]
        for row in raw_bids:
            try:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    price = float(row[0])
                    qty = float(row[1])
                    if price > 0 and qty > 0:
                        bids[price] = qty
            except Exception:
                continue

        for row in raw_asks:
            try:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    price = float(row[0])
                    qty = float(row[1])
                    if price > 0 and qty > 0:
                        asks[price] = qty
            except Exception:
                continue

        if not bids and not asks:
            log_func(f"⚠️ bitget futures {symbol}: пустой стакан")
            return 0

        with bitget_futures_order_books_lock:
            bitget_futures_order_books[symbol] = {'bids': bids, 'asks': asks}
            bitget_futures_density_timestamps[symbol] = {}

        saved_count = sync_to_cache(symbol, log_func)
        log_func(f"✅ bitget futures Стакан {symbol}: {len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}")
        return saved_count

    except requests.exceptions.Timeout:
        log_func(f"❌ init_order_book(bitget futures {symbol}): timeout")
        return 0
    except Exception as e:
        log_func(f"❌ init_order_book(bitget futures {symbol}): {e}")
        return 0


def init_spot_order_book(symbol, log_func=print):
    """Инициализация стакана Bitget Spot через REST"""
    try:
        url = BITGET_SPOT_REST_URL.format(symbol)
        res = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})

        if not res.ok:
            log_func(f"⚠️ bitget spot {symbol}: HTTP {res.status_code}")
            return 0

        try:
            data = res.json()
        except Exception:
            log_func(f"⚠️ bitget spot {symbol}: не JSON: {res.text[:200]}")
            return 0

        if data.get('code') != '00000':
            log_func(f"⚠️ bitget spot {symbol}: code={data.get('code')} msg={data.get('msg')}")
            return 0

        result = data.get('data') or {}
        raw_bids = result.get('bids') or []
        raw_asks = result.get('asks') or []

        bids = {}
        asks = {}

        for row in raw_bids:
            try:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    price = float(row[0])
                    qty = float(row[1])
                    if price > 0 and qty > 0:
                        bids[price] = qty
            except Exception:
                continue

        for row in raw_asks:
            try:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    price = float(row[0])
                    qty = float(row[1])
                    if price > 0 and qty > 0:
                        asks[price] = qty
            except Exception:
                continue

        if not bids and not asks:
            log_func(f"⚠️ bitget spot {symbol}: пустой стакан")
            return 0

        with bitget_spot_order_books_lock:
            bitget_spot_order_books[symbol] = {'bids': bids, 'asks': asks}
            bitget_spot_density_timestamps[symbol] = {}

        saved_count = sync_spot_to_cache(symbol, log_func)
        log_func(f"✅ bitget spot Стакан {symbol}: {len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}")
        return saved_count

    except requests.exceptions.Timeout:
        log_func(f"❌ init_spot_order_book(bitget spot {symbol}): timeout")
        return 0
    except Exception as e:
        log_func(f"❌ init_spot_order_book(bitget spot {symbol}): {e}")
        return 0


# ==========================================
# СИНХРОНИЗАЦИЯ В REDIS
# ==========================================
def sync_to_cache(symbol, log_func=print):
    try:
        with bitget_futures_order_books_lock:
            book = bitget_futures_order_books.get(symbol, {})
            ts = bitget_futures_density_timestamps.get(symbol, {})
            if not book:
                return 0

        key = f"scalp:futures:bitget:{symbol}"
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
                    'exchange': 'bitget'
                })

        cache.set(key, densities, CACHE_TTL)

        with bitget_futures_order_books_lock:
            bitget_futures_density_timestamps[symbol] = ts

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_to_cache(bitget futures {symbol}): {e}")
        return 0


def sync_spot_to_cache(symbol, log_func=print):
    try:
        with bitget_spot_order_books_lock:
            book = bitget_spot_order_books.get(symbol, {})
            ts = bitget_spot_density_timestamps.get(symbol, {})
            if not book:
                return 0

        key = f"scalp:spot:bitget:{symbol}"
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
                    'exchange': 'bitget'
                })

        cache.set(key, densities, CACHE_TTL)

        with bitget_spot_order_books_lock:
            bitget_spot_density_timestamps[symbol] = ts

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_spot_to_cache(bitget spot {symbol}): {e}")
        return 0


# ==========================================
# ОБНОВЛЕНИЕ ПО WS ДЕЛЬТАМ
# ==========================================
def update_order_book(symbol, bids_delta, asks_delta, log_func=print):
    global last_sync_time

    try:
        with bitget_futures_order_books_lock:
            if symbol not in bitget_futures_order_books:
                return

            book = bitget_futures_order_books[symbol]
            ts = bitget_futures_density_timestamps.get(symbol, {})
            changed = False

            for row in bids_delta:
                try:
                    if not isinstance(row, (list, tuple)) or len(row) < 2:
                        continue
                    price = float(row[0])
                    qty = float(row[1])

                    if qty == 0:
                        if price in book['bids']:
                            del book['bids'][price]
                            ts.pop(price, None)
                            changed = True
                    else:
                        book['bids'][price] = qty
                        if price not in ts:
                            ts[price] = time.time()
                        changed = True
                except Exception:
                    continue

            for row in asks_delta:
                try:
                    if not isinstance(row, (list, tuple)) or len(row) < 2:
                        continue
                    price = float(row[0])
                    qty = float(row[1])

                    if qty == 0:
                        if price in book['asks']:
                            del book['asks'][price]
                            ts.pop(price, None)
                            changed = True
                    else:
                        book['asks'][price] = qty
                        if price not in ts:
                            ts[price] = time.time()
                        changed = True
                except Exception:
                    continue

        if changed:
            now = time.time()
            key = f"bitget:futures:{symbol}"
            if key not in last_sync_time or (now - last_sync_time[key]) >= 3:
                sync_to_cache(symbol, log_func)
                last_sync_time[key] = now

    except Exception as e:
        log_func(f"❌ update_order_book(bitget futures {symbol}): {e}")


def update_spot_order_book(symbol, bids_delta, asks_delta, log_func=print):
    global last_sync_time

    try:
        with bitget_spot_order_books_lock:
            if symbol not in bitget_spot_order_books:
                return

            book = bitget_spot_order_books[symbol]
            ts = bitget_spot_density_timestamps.get(symbol, {})
            changed = False

            for row in bids_delta:
                try:
                    if not isinstance(row, (list, tuple)) or len(row) < 2:
                        continue
                    price = float(row[0])
                    qty = float(row[1])

                    if qty == 0:
                        if price in book['bids']:
                            del book['bids'][price]
                            ts.pop(price, None)
                            changed = True
                    else:
                        book['bids'][price] = qty
                        if price not in ts:
                            ts[price] = time.time()
                        changed = True
                except Exception:
                    continue

            for row in asks_delta:
                try:
                    if not isinstance(row, (list, tuple)) or len(row) < 2:
                        continue
                    price = float(row[0])
                    qty = float(row[1])

                    if qty == 0:
                        if price in book['asks']:
                            del book['asks'][price]
                            ts.pop(price, None)
                            changed = True
                    else:
                        book['asks'][price] = qty
                        if price not in ts:
                            ts[price] = time.time()
                        changed = True
                except Exception:
                    continue

        if changed:
            now = time.time()
            key = f"bitget:spot:{symbol}"
            if key not in last_sync_time or (now - last_sync_time[key]) >= 3:
                sync_spot_to_cache(symbol, log_func)
                last_sync_time[key] = now

    except Exception as e:
        log_func(f"❌ update_spot_order_book(bitget spot {symbol}): {e}")


# ==========================================
# ОБРАБОТКА ОЧЕРЕДЕЙ WS СООБЩЕНИЙ
# ==========================================
def process_message_queue(log_func=print):
    """Bitget Futures: books15 channel"""
    while True:
        try:
            message = bitget_futures_message_queue.get(timeout=1)

            # Bitget шлёт "pong" строкой
            if message == 'pong':
                continue

            data = json.loads(message)

            action = data.get('action', '')
            if action not in ('snapshot', 'update'):
                continue

            arg = data.get('arg', {})
            if arg.get('channel') not in ('books', 'books15'):
                continue
            if arg.get('instType') != 'USDT-FUTURES':
                continue

            inst_id = arg.get('instId', '')
            if not inst_id.endswith('USDT'):
                continue
            symbol = inst_id[:-4]

            data_list = data.get('data', [])
            if not data_list:
                continue

            for entry in data_list:
                raw_bids = entry.get('bids', [])
                raw_asks = entry.get('asks', [])

                if action == 'snapshot':
                    with bitget_futures_order_books_lock:
                        old_ts = bitget_futures_density_timestamps.get(symbol, {})
                        new_ts = {}
                        new_bids = {}
                        new_asks = {}

                        for row in raw_bids:
                            try:
                                if isinstance(row, (list, tuple)) and len(row) >= 2:
                                    p, q = float(row[0]), float(row[1])
                                    if p > 0 and q > 0:
                                        new_bids[p] = q
                            except Exception:
                                continue

                        for row in raw_asks:
                            try:
                                if isinstance(row, (list, tuple)) and len(row) >= 2:
                                    p, q = float(row[0]), float(row[1])
                                    if p > 0 and q > 0:
                                        new_asks[p] = q
                            except Exception:
                                continue

                        for p in new_bids:
                            if p in old_ts:
                                new_ts[p] = old_ts[p]
                        for p in new_asks:
                            if p in old_ts:
                                new_ts[p] = old_ts[p]

                        bitget_futures_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
                        bitget_futures_density_timestamps[symbol] = new_ts

                    sync_to_cache(symbol, log_func)
                    continue

                if action == 'update' and (raw_bids or raw_asks):
                    update_order_book(symbol, raw_bids, raw_asks, log_func)

        except Empty:
            continue
        except Exception as e:
            log_func(f"❌ bitget futures Ошибка обработки сообщения: {e}")


def process_spot_message_queue(log_func=print):
    """Bitget Spot: books15 channel"""
    while True:
        try:
            message = bitget_spot_message_queue.get(timeout=1)

            if message == 'pong':
                continue

            data = json.loads(message)

            action = data.get('action', '')
            if action not in ('snapshot', 'update'):
                continue

            arg = data.get('arg', {})
            if arg.get('channel') not in ('books', 'books15'):
                continue
            if arg.get('instType') != 'SPOT':
                continue

            inst_id = arg.get('instId', '')
            if not inst_id.endswith('USDT'):
                continue
            symbol = inst_id[:-4]

            data_list = data.get('data', [])
            if not data_list:
                continue

            for entry in data_list:
                raw_bids = entry.get('bids', [])
                raw_asks = entry.get('asks', [])

                if action == 'snapshot':
                    with bitget_spot_order_books_lock:
                        old_ts = bitget_spot_density_timestamps.get(symbol, {})
                        new_ts = {}
                        new_bids = {}
                        new_asks = {}

                        for row in raw_bids:
                            try:
                                if isinstance(row, (list, tuple)) and len(row) >= 2:
                                    p, q = float(row[0]), float(row[1])
                                    if p > 0 and q > 0:
                                        new_bids[p] = q
                            except Exception:
                                continue

                        for row in raw_asks:
                            try:
                                if isinstance(row, (list, tuple)) and len(row) >= 2:
                                    p, q = float(row[0]), float(row[1])
                                    if p > 0 and q > 0:
                                        new_asks[p] = q
                            except Exception:
                                continue

                        for p in new_bids:
                            if p in old_ts:
                                new_ts[p] = old_ts[p]
                        for p in new_asks:
                            if p in old_ts:
                                new_ts[p] = old_ts[p]

                        bitget_spot_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
                        bitget_spot_density_timestamps[symbol] = new_ts

                    sync_spot_to_cache(symbol, log_func)
                    continue

                if action == 'update' and (raw_bids or raw_asks):
                    update_spot_order_book(symbol, raw_bids, raw_asks, log_func)

        except Empty:
            continue
        except Exception as e:
            log_func(f"❌ bitget spot Ошибка обработки сообщения: {e}")


# ==========================================
# WEBSOCKET
# ==========================================
def on_message(ws, message):
    try:
        bitget_futures_message_queue.put_nowait(message)
    except Exception as e:
        print(f"❌ bitget futures on_message: {e}")


def on_message_spot(ws, message):
    try:
        bitget_spot_message_queue.put_nowait(message)
    except Exception as e:
        print(f"❌ bitget spot on_message: {e}")


def on_open(ws):
    print(f"✅ bitget futures WebSocket открыт: {len(ws.symbols)} символов")
    args = [
        {
            "instId": f"{s}USDT",
            "channel": "books15",
            "instType": "USDT-FUTURES"
        }
        for s in ws.symbols
    ]
    ws.send(json.dumps({"op": "subscribe", "args": args}))


def on_open_spot(ws):
    print(f"✅ bitget spot WebSocket открыт: {len(ws.symbols)} символов")
    args = [
        {
            "instId": f"{s}USDT",
            "channel": "books15",
            "instType": "SPOT"
        }
        for s in ws.symbols
    ]
    ws.send(json.dumps({"op": "subscribe", "args": args}))


def start_websocket(symbols_list, log_func=print):
    global bitget_futures_ws_stop_event, bitget_futures_ws_instance

    while not bitget_futures_ws_stop_event.is_set():
        ws = None
        stop_event = threading.Event()

        try:
            ws = websocket.WebSocketApp(
                BITGET_FUTURES_WS_URL,
                on_open=lambda ws: on_open(ws),
                on_message=lambda ws, msg: on_message(ws, msg),
                on_error=lambda ws, err: log_func(f"❌ bitget futures WS ошибка: {err}"),
                on_close=lambda ws, code, msg: log_func(f"⚠️ bitget futures WS закрыт: {code} {msg}")
            )

            ws.symbols = symbols_list
            bitget_futures_ws_instance = ws

            def heartbeat():
                while not stop_event.is_set():
                    time.sleep(25)
                    try:
                        if ws and ws.sock and ws.sock.connected:
                            ws.send("ping")
                    except Exception as e:
                        log_func(f"❌ bitget futures heartbeat: {e}")
                        break

            threading.Thread(target=heartbeat, daemon=True).start()
            ws.run_forever(ping_interval=0)

        except Exception as e:
            log_func(f"❌ Ошибка в start_websocket(bitget futures): {e}")

        finally:
            stop_event.set()
            bitget_futures_ws_instance = None

        if bitget_futures_ws_stop_event.is_set():
            log_func("🛑 Bitget futures WebSocket остановлен")
            break

        log_func("🔁 Bitget futures WebSocket переподключение через 3 секунды...")
        time.sleep(3)


def start_spot_websocket(symbols_list, log_func=print):
    global bitget_spot_ws_stop_event, bitget_spot_ws_instance

    while not bitget_spot_ws_stop_event.is_set():
        ws = None
        stop_event = threading.Event()

        try:
            ws = websocket.WebSocketApp(
                BITGET_SPOT_WS_URL,
                on_open=lambda ws: on_open_spot(ws),
                on_message=lambda ws, msg: on_message_spot(ws, msg),
                on_error=lambda ws, err: log_func(f"❌ bitget spot WS ошибка: {err}"),
                on_close=lambda ws, code, msg: log_func(f"⚠️ bitget spot WS закрыт: {code} {msg}")
            )

            ws.symbols = symbols_list
            bitget_spot_ws_instance = ws

            def heartbeat():
                while not stop_event.is_set():
                    time.sleep(25)
                    try:
                        if ws and ws.sock and ws.sock.connected:
                            ws.send("ping")
                    except Exception as e:
                        log_func(f"❌ bitget spot heartbeat: {e}")
                        break

            threading.Thread(target=heartbeat, daemon=True).start()
            ws.run_forever(ping_interval=0)

        except Exception as e:
            log_func(f"❌ Ошибка в start_spot_websocket(bitget spot): {e}")

        finally:
            stop_event.set()
            bitget_spot_ws_instance = None

        if bitget_spot_ws_stop_event.is_set():
            log_func("🛑 Bitget spot WebSocket остановлен")
            break

        log_func("🔁 Bitget spot WebSocket переподключение через 3 секунды...")
        time.sleep(3)


# ==========================================
# ЗАПУСК МОНИТОРОВ
# ==========================================
def start_bitget_monitor(log_func=print):
    global bitget_futures_symbols

    log_func("🚀 Запуск Bitget Futures Monitor...")

    threading.Thread(
        target=lambda: process_message_queue(log_func),
        daemon=True
    ).start()

    candidates = get_top_symbols(60)
    active_symbols = []
    TARGET = 30

    for symbol in candidates:
        if len(active_symbols) >= TARGET:
            break
        saved_count = init_order_book(symbol, log_func)
        if saved_count > 0:
            active_symbols.append(symbol)
            log_func(f"✅ bitget futures {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ bitget futures {symbol}: пропущен (нет плотностей > $10K)")
        time.sleep(0.05)

    bitget_futures_symbols = active_symbols

    if bitget_futures_symbols:
        threading.Thread(
            target=lambda: start_websocket(bitget_futures_symbols, log_func),
            daemon=True
        ).start()

    log_func(f"✅ Bitget Futures Monitor запущен. Активных монет: {len(bitget_futures_symbols)}")

    threading.Thread(
        target=lambda: periodic_bitget_futures_refresh(log_func),
        daemon=True
    ).start()


def start_bitget_spot_monitor(log_func=print):
    global bitget_spot_symbols

    log_func("🚀 Запуск Bitget Spot Monitor...")

    threading.Thread(
        target=lambda: process_spot_message_queue(log_func),
        daemon=True
    ).start()

    candidates = get_top_spot_symbols(60)
    active_symbols = []
    TARGET = 30

    for symbol in candidates:
        if len(active_symbols) >= TARGET:
            break
        saved_count = init_spot_order_book(symbol, log_func)
        if saved_count > 0:
            active_symbols.append(symbol)
            log_func(f"✅ bitget spot {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ bitget spot {symbol}: пропущен (нет плотностей > $10K)")
        time.sleep(0.05)

    bitget_spot_symbols = active_symbols

    if bitget_spot_symbols:
        threading.Thread(
            target=lambda: start_spot_websocket(bitget_spot_symbols, log_func),
            daemon=True
        ).start()

    log_func(f"✅ Bitget Spot Monitor запущен. Активных монет: {len(bitget_spot_symbols)}")

    threading.Thread(
        target=lambda: periodic_bitget_spot_refresh(log_func),
        daemon=True
    ).start()


# ==========================================
# ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ СПИСКОВ
# ==========================================
def refresh_bitget_futures_symbols(log_func=print):
    global bitget_futures_symbols, bitget_futures_ws_stop_event, bitget_futures_ws_instance

    log_func("🔄 Обновление списка Bitget Futures...")

    old_symbols = set(bitget_futures_symbols)
    candidates = get_top_symbols(60)

    new_active = []
    TARGET = 30

    for symbol in candidates:
        if len(new_active) >= TARGET:
            break
        if symbol in old_symbols:
            new_active.append(symbol)
            continue
        saved_count = init_order_book(symbol, log_func)
        if saved_count > 0:
            new_active.append(symbol)
            log_func(f"✅ bitget futures {symbol}: добавлен (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ bitget futures {symbol}: пропущен (нет плотностей > $10K)")
        time.sleep(0.05)

    new_symbols = set(new_active)
    removed = old_symbols - new_symbols
    added = new_symbols - old_symbols

    if removed:
        with bitget_futures_order_books_lock:
            for symbol in removed:
                bitget_futures_order_books.pop(symbol, None)
                bitget_futures_density_timestamps.pop(symbol, None)
                last_sync_time.pop(f"bitget:futures:{symbol}", None)
        log_func(f"🗑️ bitget futures удалены: {', '.join(sorted(removed))}")

    bitget_futures_symbols = new_active

    if removed or added:
        log_func(f"🔄 bitget futures: добавлено {len(added)}, удалено {len(removed)}")
        bitget_futures_ws_stop_event.set()
        if bitget_futures_ws_instance:
            try:
                bitget_futures_ws_instance.close()
            except Exception:
                pass
        time.sleep(2)
        bitget_futures_ws_stop_event.clear()

        if bitget_futures_symbols:
            threading.Thread(
                target=lambda: start_websocket(bitget_futures_symbols, log_func),
                daemon=True
            ).start()
    else:
        log_func("✅ bitget futures: список не изменился")


def refresh_bitget_spot_symbols(log_func=print):
    global bitget_spot_symbols, bitget_spot_ws_stop_event, bitget_spot_ws_instance

    log_func("🔄 Обновление списка Bitget Spot...")

    old_symbols = set(bitget_spot_symbols)
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
            log_func(f"✅ bitget spot {symbol}: добавлен (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ bitget spot {symbol}: пропущен (нет плотностей > $10K)")
        time.sleep(0.05)

    new_symbols = set(new_active)
    removed = old_symbols - new_symbols
    added = new_symbols - old_symbols

    if removed:
        with bitget_spot_order_books_lock:
            for symbol in removed:
                bitget_spot_order_books.pop(symbol, None)
                bitget_spot_density_timestamps.pop(symbol, None)
                last_sync_time.pop(f"bitget:spot:{symbol}", None)
        log_func(f"🗑️ bitget spot удалены: {', '.join(sorted(removed))}")

    bitget_spot_symbols = new_active

    if removed or added:
        log_func(f"🔄 bitget spot: добавлено {len(added)}, удалено {len(removed)}")
        bitget_spot_ws_stop_event.set()
        if bitget_spot_ws_instance:
            try:
                bitget_spot_ws_instance.close()
            except Exception:
                pass
        time.sleep(2)
        bitget_spot_ws_stop_event.clear()

        if bitget_spot_symbols:
            threading.Thread(
                target=lambda: start_spot_websocket(bitget_spot_symbols, log_func),
                daemon=True
            ).start()
    else:
        log_func("✅ bitget spot: список не изменился")


def periodic_bitget_futures_refresh(log_func=print):
    REFRESH_INTERVAL = 300
    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            refresh_bitget_futures_symbols(log_func)
        except Exception as e:
            log_func(f"❌ Ошибка при обновлении списка Bitget Futures: {e}")


def periodic_bitget_spot_refresh(log_func=print):
    REFRESH_INTERVAL = 300
    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            refresh_bitget_spot_symbols(log_func)
        except Exception as e:
            log_func(f"❌ Ошибка при обновлении списка Bitget Spot: {e}")