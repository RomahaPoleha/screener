"""
MEXC Monitor — мониторинг плотностей MEXC Futures и Spot
Формат push.depth: [price, qty, orderCount] — дельта-обновления
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
mexc_futures_order_books = {}
mexc_futures_density_timestamps = {}
mexc_futures_order_books_lock = threading.Lock()
mexc_futures_symbols = []
mexc_futures_message_queue = Queue(maxsize=50000)

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — SPOT
# ==========================================
mexc_spot_order_books = {}
mexc_spot_density_timestamps = {}
mexc_spot_order_books_lock = threading.Lock()
mexc_spot_symbols = []
mexc_spot_message_queue = Queue(maxsize=50000)

# URLs
MEXC_SPOT_REST_URL = "https://api.mexc.com/api/v3/depth?symbol={}USDT&limit=100"
MEXC_SPOT_WS_URL = "wss://wbs.mexc.com/ws"
MEXC_FUTURES_REST_URL = "https://contract.mexc.com/api/v1/contract/depth/{}_USDT?limit=100"
MEXC_FUTURES_WS_URL = "wss://contract.mexc.com/edge"

mexc_futures_ws_stop_event = threading.Event()
mexc_futures_ws_instance = None
mexc_spot_ws_stop_event = threading.Event()
mexc_spot_ws_instance = None

last_sync_time = {}

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
    """Отбор MEXC Futures по гибридной формуле (RVOL+NATR+%)"""
    try:
        exchange = ccxt.mexc({
            'enableRateLimit': True,
            'timeout': 15000,
            'options': {'defaultType': 'swap'}
        })
        tickers = exchange.fetch_tickers()

        # Страховка: если ccxt не посчитал quoteVolume
        for symbol, data in tickers.items():
            if not (data.get('quoteVolume') or 0):
                try:
                    info = data.get('info', {})
                    amount24 = float(info.get('amount24') or 0)
                    if amount24 > 0:
                        data['quoteVolume'] = amount24
                    else:
                        vol_contracts = float(info.get('volume24') or info.get('volume_24h') or 0)
                        last_price = float(data.get('last') or info.get('lastPrice') or 0)
                        data['quoteVolume'] = vol_contracts * last_price
                except Exception:
                    pass

        coin_selection.update_volume_history(tickers, coin_selection.clean_swap)
        candidates = coin_selection.select_candidates(
            tickers, coin_selection.clean_swap, limit=limit * 2,
            log_func=lambda msg: print(msg)
        )
        return candidates[:limit]
    except Exception as e:
        print(f"❌ Ошибка в get_top_symbols(mexc futures): {e}")
        return []


def get_top_spot_symbols(limit=30):
    """Отбор MEXC Spot по гибридной формуле"""
    try:
        exchange = ccxt.mexc({
            'enableRateLimit': True,
            'timeout': 15000,
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
        print(f"❌ Ошибка в get_top_spot_symbols(mexc spot): {e}")
        return []


def init_order_book(symbol, log_func=print):
    """Инициализация стакана MEXC Futures через REST.
    REST формат: {"success":true,"data":{"bids":[[p,q,c],...],"asks":[[p,q,c],...]}}
    """
    try:
        url = MEXC_FUTURES_REST_URL.format(symbol)
        res = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})

        if not res.ok:
            log_func(f"⚠️ mexc futures {symbol}: HTTP {res.status_code}")
            return 0

        try:
            data = res.json()
        except Exception:
            log_func(f"⚠️ mexc futures {symbol}: не JSON")
            return 0

        if not isinstance(data, dict) or data.get('success') is False:
            log_func(f"⚠️ mexc futures {symbol}: API ошибка")
            return 0

        inner = data.get('data') or {}
        raw_bids = inner.get('bids') or []
        raw_asks = inner.get('asks') or []

        bids = {}
        asks = {}

        # Формат: [price, qty, orderCount]
        for row in raw_bids:
            try:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    price = float(row[0])
                    qty = abs(float(row[1]))  # row[1] = qty
                    if price > 0 and qty > 0:
                        bids[price] = qty
            except Exception:
                continue

        for row in raw_asks:
            try:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    price = float(row[0])
                    qty = abs(float(row[1]))  # row[1] = qty
                    if price > 0 and qty > 0:
                        asks[price] = qty
            except Exception:
                continue

        if not bids and not asks:
            log_func(f"⚠️ mexc futures {symbol}: пустой стакан")
            return 0

        with mexc_futures_order_books_lock:
            mexc_futures_order_books[symbol] = {'bids': bids, 'asks': asks}
            mexc_futures_density_timestamps[symbol] = {}

        saved_count = sync_to_cache(symbol, log_func)
        log_func(f"✅ mexc futures Стакан {symbol}: {len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}")
        return saved_count

    except requests.exceptions.Timeout:
        log_func(f"❌ init_order_book(mexc futures {symbol}): timeout")
        return 0
    except Exception as e:
        log_func(f"❌ init_order_book(mexc futures {symbol}): {e}")
        return 0


def init_spot_order_book(symbol, log_func=print):
    """Инициализация стакана MEXC Spot через REST"""
    try:
        url = MEXC_SPOT_REST_URL.format(symbol)
        res = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})

        if not res.ok:
            log_func(f"⚠️ mexc spot {symbol}: HTTP {res.status_code}")
            return 0

        try:
            data = res.json()
        except Exception:
            log_func(f"⚠️ mexc spot {symbol}: не JSON")
            return 0

        if isinstance(data, list):
            return 0
        if not isinstance(data, dict):
            return 0
        if 'code' in data and data.get('code') != 0:
            return 0

        raw_bids = data.get('bids') or []
        raw_asks = data.get('asks') or []

        bids = {}
        asks = {}

        for row in raw_bids:
            try:
                if isinstance(row, (list, tuple)):
                    price = float(row[0])
                    qty = abs(float(row[1]))
                    if price > 0 and qty > 0:
                        bids[price] = qty
            except Exception:
                continue

        for row in raw_asks:
            try:
                if isinstance(row, (list, tuple)):
                    price = float(row[0])
                    qty = abs(float(row[1]))
                    if price > 0 and qty > 0:
                        asks[price] = qty
            except Exception:
                continue

        if not bids and not asks:
            log_func(f"⚠️ mexc spot {symbol}: пустой стакан")
            return 0

        with mexc_spot_order_books_lock:
            mexc_spot_order_books[symbol] = {'bids': bids, 'asks': asks}
            mexc_spot_density_timestamps[symbol] = {}

        saved_count = sync_spot_to_cache(symbol, log_func)
        log_func(f"✅ mexc spot Стакан {symbol}: {len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}")
        return saved_count

    except requests.exceptions.Timeout:
        log_func(f"❌ init_spot_order_book(mexc spot {symbol}): timeout")
        return 0
    except Exception as e:
        log_func(f"❌ init_spot_order_book(mexc spot {symbol}): {e}")
        return 0


def sync_to_cache(symbol, log_func=print):
    try:
        with mexc_futures_order_books_lock:
            book = mexc_futures_order_books.get(symbol, {})
            ts = mexc_futures_density_timestamps.get(symbol, {})
            if not book:
                return 0

        key = f"scalp:futures:mexc:{symbol}"
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
                    'exchange': 'mexc'
                })

        cache.set(key, densities, CACHE_TTL)

        with mexc_futures_order_books_lock:
            mexc_futures_density_timestamps[symbol] = ts

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_to_cache(mexc futures {symbol}): {e}")
        return 0


def sync_spot_to_cache(symbol, log_func=print):
    try:
        with mexc_spot_order_books_lock:
            book = mexc_spot_order_books.get(symbol, {})
            ts = mexc_spot_density_timestamps.get(symbol, {})
            if not book:
                return 0

        key = f"scalp:spot:mexc:{symbol}"
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
                    'exchange': 'mexc'
                })

        cache.set(key, densities, CACHE_TTL)

        with mexc_spot_order_books_lock:
            mexc_spot_density_timestamps[symbol] = ts

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_spot_to_cache(mexc spot {symbol}): {e}")
        return 0


def process_message_queue(log_func=print):
    """MEXC Futures: push.depth — дельта-обновления.
    Формат: {"channel":"push.depth","symbol":"BTC_USDT","data":{"bids":[[p,q,c],...],"asks":[[p,q,c],...]}}
    Где [price, qty, orderCount]. qty=0 означает удаление уровня.
    """
    while True:
        try:
            message = mexc_futures_message_queue.get(timeout=1)
            data = json.loads(message)

            channel = data.get('channel', '')
            if channel != 'push.depth':
                continue

            sym = data.get('symbol', '')
            symbol = sym[:-5] if sym.endswith('_USDT') else sym

            inner = data.get('data') or {}
            bids = inner.get('bids') or []
            asks = inner.get('asks') or []

            if not (bids or asks):
                continue

            with mexc_futures_order_books_lock:
                if symbol not in mexc_futures_order_books:
                    continue

                book = mexc_futures_order_books[symbol]
                ts = mexc_futures_density_timestamps.get(symbol, {})
                changed = False

                # Обработка bids
                for row in bids:
                    try:
                        if not isinstance(row, (list, tuple)) or len(row) < 2:
                            continue
                        price = float(row[0])
                        qty = abs(float(row[1]))  # row[1] = qty!

                        if qty == 0:
                            # Удаление уровня
                            if price in book['bids']:
                                del book['bids'][price]
                                ts.pop(price, None)
                                changed = True
                        else:
                            # Добавление/обновление уровня
                            book['bids'][price] = qty
                            if price not in ts:
                                ts[price] = time.time()
                            changed = True
                    except Exception:
                        continue

                # Обработка asks
                for row in asks:
                    try:
                        if not isinstance(row, (list, tuple)) or len(row) < 2:
                            continue
                        price = float(row[0])
                        qty = abs(float(row[1]))  # row[1] = qty!

                        if qty == 0:
                            # Удаление уровня
                            if price in book['asks']:
                                del book['asks'][price]
                                ts.pop(price, None)
                                changed = True
                        else:
                            # Добавление/обновление уровня
                            book['asks'][price] = qty
                            if price not in ts:
                                ts[price] = time.time()
                            changed = True
                    except Exception:
                        continue

            if changed:
                now = time.time()
                key = f"mexc:futures:{symbol}"
                if key not in last_sync_time or (now - last_sync_time[key]) >= 3:
                    sync_to_cache(symbol, log_func)
                    last_sync_time[key] = now

        except Empty:
            continue
        except Exception as e:
            log_func(f"❌ mexc futures Ошибка обработки сообщения: {e}")


def process_spot_message_queue(log_func=print):
    """MEXC Spot: канал spot@public.depth.v3.api@SYMBOL"""
    while True:
        try:
            message = mexc_spot_message_queue.get(timeout=1)
            data = json.loads(message)

            channel = data.get('c', '')
            if not channel.startswith('spot@public.depth'):
                continue

            sym = data.get('s', '')
            symbol = sym[:-4] if sym.endswith('USDT') else sym

            inner = data.get('d') or {}
            bids = inner.get('bids') or []
            asks = inner.get('asks') or []

            if not (bids or asks):
                continue

            # Spot: полная замена стакана
            new_bids = {}
            new_asks = {}

            for row in bids:
                try:
                    if isinstance(row, (list, tuple)) and len(row) >= 2:
                        price = float(row[0])
                        qty = abs(float(row[1]))
                        if price > 0 and qty > 0:
                            new_bids[price] = qty
                except Exception:
                    continue

            for row in asks:
                try:
                    if isinstance(row, (list, tuple)) and len(row) >= 2:
                        price = float(row[0])
                        qty = abs(float(row[1]))
                        if price > 0 and qty > 0:
                            new_asks[price] = qty
                except Exception:
                    continue

            with mexc_spot_order_books_lock:
                old_ts = mexc_spot_density_timestamps.get(symbol, {})
                new_ts = {}
                for p in new_bids:
                    if p in old_ts:
                        new_ts[p] = old_ts[p]
                    else:
                        new_ts[p] = time.time()
                for p in new_asks:
                    if p in old_ts:
                        new_ts[p] = old_ts[p]
                    else:
                        new_ts[p] = time.time()

                mexc_spot_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
                mexc_spot_density_timestamps[symbol] = new_ts

            sync_spot_to_cache(symbol, log_func)

        except Empty:
            continue
        except Exception as e:
            log_func(f"❌ mexc spot Ошибка обработки сообщения: {e}")


# ==========================================
# WEBSOCKET
# ==========================================
def on_message(ws, message):
    try:
        mexc_futures_message_queue.put_nowait(message)
    except Exception as e:
        print(f"❌ mexc futures on_message: {e}")


def on_message_spot(ws, message):
    try:
        mexc_spot_message_queue.put_nowait(message)
    except Exception as e:
        print(f"❌ mexc spot on_message: {e}")


def on_open(ws):
    print(f"✅ mexc futures WebSocket открыт: {len(ws.symbols)} символов")
    for symbol in ws.symbols:
        sub = {"method": "sub.depth", "param": {"symbol": f"{symbol}_USDT"}}
        ws.send(json.dumps(sub))


def on_open_spot(ws):
    print(f"✅ mexc spot WebSocket открыт: {len(ws.symbols)} символов")
    params = [f"spot@public.depth.v3.api@{s}USDT" for s in ws.symbols]
    sub = {"method": "SUBSCRIPTION", "params": params}
    ws.send(json.dumps(sub))


def start_websocket(symbols_list, log_func=print):
    global mexc_futures_ws_stop_event, mexc_futures_ws_instance

    while not mexc_futures_ws_stop_event.is_set():
        ws = None
        stop_event = threading.Event()

        try:
            ws = websocket.WebSocketApp(
                MEXC_FUTURES_WS_URL,
                on_open=lambda ws: on_open(ws),
                on_message=lambda ws, msg: on_message(ws, msg),
                on_error=lambda ws, err: log_func(f"❌ mexc futures WS ошибка: {err}"),
                on_close=lambda ws, code, msg: log_func(f"⚠️ mexc futures WS закрыт: {code} {msg}")
            )

            ws.symbols = symbols_list
            mexc_futures_ws_instance = ws

            def heartbeat():
                while not stop_event.is_set():
                    time.sleep(15)
                    try:
                        if ws and ws.sock and ws.sock.connected:
                            ws.send(json.dumps({"method": "ping"}))
                    except Exception as e:
                        log_func(f"❌ mexc futures heartbeat: {e}")
                        break

            threading.Thread(target=heartbeat, daemon=True).start()
            ws.run_forever(ping_interval=20, ping_timeout=10)

        except Exception as e:
            log_func(f"❌ Ошибка в start_websocket(mexc futures): {e}")

        finally:
            stop_event.set()
            mexc_futures_ws_instance = None

        if mexc_futures_ws_stop_event.is_set():
            log_func("🛑 MEXC futures WebSocket остановлен")
            break

        log_func("🔁 MEXC futures WebSocket переподключение через 3 секунды...")
        time.sleep(3)


def start_spot_websocket(symbols_list, log_func=print):
    global mexc_spot_ws_stop_event, mexc_spot_ws_instance

    while not mexc_spot_ws_stop_event.is_set():
        ws = None
        stop_event = threading.Event()

        try:
            ws = websocket.WebSocketApp(
                MEXC_SPOT_WS_URL,
                on_open=lambda ws: on_open_spot(ws),
                on_message=lambda ws, msg: on_message_spot(ws, msg),
                on_error=lambda ws, err: log_func(f"❌ mexc spot WS ошибка: {err}"),
                on_close=lambda ws, code, msg: log_func(f"⚠️ mexc spot WS закрыт: {code} {msg}")
            )

            ws.symbols = symbols_list
            mexc_spot_ws_instance = ws

            def heartbeat():
                while not stop_event.is_set():
                    time.sleep(15)
                    try:
                        if ws and ws.sock and ws.sock.connected:
                            ws.send(json.dumps({"method": "ping"}))
                    except Exception as e:
                        log_func(f"❌ mexc spot heartbeat: {e}")
                        break

            threading.Thread(target=heartbeat, daemon=True).start()
            ws.run_forever(ping_interval=20, ping_timeout=10)

        except Exception as e:
            log_func(f"❌ Ошибка в start_spot_websocket(mexc spot): {e}")

        finally:
            stop_event.set()
            mexc_spot_ws_instance = None

        if mexc_spot_ws_stop_event.is_set():
            log_func("🛑 MEXC spot WebSocket остановлен")
            break

        log_func("🔁 MEXC spot WebSocket переподключение через 3 секунды...")
        time.sleep(3)


# ==========================================
# ЗАПУСК МОНИТОРОВ
# ==========================================
def start_mexc_monitor(log_func=print):
    global mexc_futures_symbols

    log_func("🚀 Запуск MEXC Futures Monitor...")

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
            log_func(f"✅ mexc futures {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ mexc futures {symbol}: пропущен (нет плотностей > $10K)")
        time.sleep(0.05)

    mexc_futures_symbols = active_symbols

    if mexc_futures_symbols:
        threading.Thread(
            target=lambda: start_websocket(mexc_futures_symbols, log_func),
            daemon=True
        ).start()

    log_func(f"✅ MEXC Futures Monitor запущен. Активных монет: {len(mexc_futures_symbols)}")

    threading.Thread(
        target=lambda: periodic_mexc_futures_refresh(log_func),
        daemon=True
    ).start()


def start_mexc_spot_monitor(log_func=print):
    global mexc_spot_symbols

    log_func("🚀 Запуск MEXC Spot Monitor...")

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
            log_func(f"✅ mexc spot {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ mexc spot {symbol}: пропущен (нет плотностей > $10K)")
        time.sleep(0.05)

    mexc_spot_symbols = active_symbols

    if mexc_spot_symbols:
        threading.Thread(
            target=lambda: start_spot_websocket(mexc_spot_symbols, log_func),
            daemon=True
        ).start()

    log_func(f"✅ MEXC Spot Monitor запущен. Активных монет: {len(mexc_spot_symbols)}")

    threading.Thread(
        target=lambda: periodic_mexc_spot_refresh(log_func),
        daemon=True
    ).start()


# ==========================================
# ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ СПИСКОВ
# ==========================================
def refresh_mexc_futures_symbols(log_func=print):
    global mexc_futures_symbols, mexc_futures_ws_stop_event, mexc_futures_ws_instance

    log_func("🔄 Обновление списка MEXC Futures...")

    old_symbols = set(mexc_futures_symbols)
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
            log_func(f"✅ mexc futures {symbol}: добавлен (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ mexc futures {symbol}: пропущен (нет плотностей > $10K)")
        time.sleep(0.05)

    new_symbols = set(new_active)
    removed = old_symbols - new_symbols
    added = new_symbols - old_symbols

    if removed:
        with mexc_futures_order_books_lock:
            for symbol in removed:
                mexc_futures_order_books.pop(symbol, None)
                mexc_futures_density_timestamps.pop(symbol, None)
        log_func(f"🗑️ mexc futures удалены: {', '.join(sorted(removed))}")

    mexc_futures_symbols = new_active

    if removed or added:
        log_func(f"🔄 mexc futures: добавлено {len(added)}, удалено {len(removed)}")
        mexc_futures_ws_stop_event.set()
        if mexc_futures_ws_instance:
            try:
                mexc_futures_ws_instance.close()
            except Exception:
                pass
        time.sleep(2)
        mexc_futures_ws_stop_event.clear()

        if mexc_futures_symbols:
            threading.Thread(
                target=lambda: start_websocket(mexc_futures_symbols, log_func),
                daemon=True
            ).start()
    else:
        log_func("✅ mexc futures: список не изменился")


def refresh_mexc_spot_symbols(log_func=print):
    global mexc_spot_symbols, mexc_spot_ws_stop_event, mexc_spot_ws_instance

    log_func("🔄 Обновление списка MEXC Spot...")

    old_symbols = set(mexc_spot_symbols)
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
            log_func(f"✅ mexc spot {symbol}: добавлен (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ mexc spot {symbol}: пропущен (нет плотностей > $10K)")
        time.sleep(0.05)

    new_symbols = set(new_active)
    removed = old_symbols - new_symbols
    added = new_symbols - old_symbols

    if removed:
        with mexc_spot_order_books_lock:
            for symbol in removed:
                mexc_spot_order_books.pop(symbol, None)
                mexc_spot_density_timestamps.pop(symbol, None)
        log_func(f"🗑️ mexc spot удалены: {', '.join(sorted(removed))}")

    mexc_spot_symbols = new_active

    if removed or added:
        log_func(f"🔄 mexc spot: добавлено {len(added)}, удалено {len(removed)}")
        mexc_spot_ws_stop_event.set()
        if mexc_spot_ws_instance:
            try:
                mexc_spot_ws_instance.close()
            except Exception:
                pass
        time.sleep(2)
        mexc_spot_ws_stop_event.clear()
        if mexc_spot_symbols:
            threading.Thread(
                target=lambda: start_spot_websocket(mexc_spot_symbols, log_func),
                daemon=True
            ).start()
    else:
        log_func("✅ mexc spot: список не изменился")


def periodic_mexc_futures_refresh(log_func=print):
    REFRESH_INTERVAL = 300
    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            refresh_mexc_futures_symbols(log_func)
        except Exception as e:
            log_func(f"❌ Ошибка при обновлении списка MEXC Futures: {e}")


def periodic_mexc_spot_refresh(log_func=print):
    REFRESH_INTERVAL = 300
    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            refresh_mexc_spot_symbols(log_func)
        except Exception as e:
            log_func(f"❌ Ошибка при обновлении списка MEXC Spot: {e}")