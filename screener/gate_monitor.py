"""
Gate Monitor — мониторинг плотностей Gate.io Futures и Spot
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

# Поддержка старых и новых версий ccxt
GateExchange = getattr(ccxt, 'gateio', None) or getattr(ccxt, 'gate', None)
if GateExchange is None:
    raise ImportError("ccxt не поддерживает Gate.io (нет ни 'gateio' ни 'gate')")

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — FUTURES
# ==========================================
gate_futures_order_books = {}
gate_futures_density_timestamps = {}
gate_futures_order_books_lock = threading.Lock()
gate_futures_symbols = []
gate_futures_message_queue = Queue(maxsize=50000)

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — SPOT
# ==========================================
gate_spot_order_books = {}
gate_spot_density_timestamps = {}
gate_spot_order_books_lock = threading.Lock()
gate_spot_symbols = []
gate_spot_message_queue = Queue(maxsize=50000)

# ==========================================
# URLs Gate.io
# ==========================================
GATE_FUTURES_WS_URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
GATE_FUTURES_REST_URL = "https://api.gateio.ws/api/v4/futures/usdt/order_book?contract={}_USDT&limit=50"

GATE_SPOT_WS_URL = "wss://api.gateio.ws/ws/v4/"
GATE_SPOT_REST_URL = "https://api.gateio.ws/api/v4/spot/order_book?currency_pair={}_USDT&limit=50"

# Управление WebSocket
gate_futures_ws_stop_event = threading.Event()
gate_futures_ws_instance = None
gate_spot_ws_stop_event = threading.Event()
gate_spot_ws_instance = None

# Rate limiting
last_sync_time = {}
gate_spot_last_sync_time = {}

# Минимальный возраст плотности
MIN_AGE_SECONDS = 1800
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
# ОБОБЩЁННАЯ ЛОГИКА СКАЧАНИЯ ТОПА МОНЕТ
# ==========================================

# ==========================================
# ОБОБЩЁННАЯ ЛОГИКА СКАЧАНИЯ ТОПА МОНЕТ
# ==========================================

def get_top_symbols(limit=30, log_func=print):
    """Отбор Gate Futures по гибридной формуле"""
    return _get_top_symbols_generic(limit, market='swap', log_func=log_func)


def get_top_spot_symbols(limit=30, log_func=print):
    """Отбор Gate Spot по гибридной формуле"""
    return _get_top_symbols_generic(limit, market='spot', log_func=log_func)


def _get_top_symbols_generic(limit=30, market='swap', log_func=print):
    """Гибридный отбор: Score = 0.5*RVOL + 0.3*NATR + 0.2*|%|"""
    try:
        exchange = GateExchange({
            'enableRateLimit': True,
            'timeout': 15000,
            'options': {'defaultType': market}
        })

        tickers = exchange.fetch_tickers(params={'type': market})
        log_func(f"🔍 Gate {market}: получено {len(tickers)} тикеров")

        # Для swap Gate отдаёт volume в контрактах — пересчитываем в quoteVolume
        if market == 'swap':
            for symbol, data in tickers.items():
                try:
                    info = data.get('info', {})
                    vol_contracts = float(info.get('volume_24h') or 0)
                    last_price = float(data.get('last') or 0)
                    data['quoteVolume'] = vol_contracts * last_price
                except Exception:
                    pass

        # Ярус 1: накапливаем историю объёма для RVOL
        clean_fn = coin_selection.clean_swap if market == 'swap' else coin_selection.clean_spot
        coin_selection.update_volume_history(tickers, clean_fn)

        # Ярус 2: отбор по гибридной формуле
        candidates = coin_selection.select_candidates(
            tickers, clean_fn, limit=limit * 2,
            log_func=log_func
        )

        log_func(f"📊 Gate {market}: отобрано {len(candidates)}")
        return candidates[:limit]

    except Exception as e:
        log_func(f"❌ Ошибка в _get_top_symbols_generic(gate {market}): {e}")
        import traceback
        log_func(traceback.format_exc())
        return []


# ==========================================
# ОБЩИЕ ФУНКЦИИ СИНХРОНИЗАЦИИ В КЭШ
# ==========================================

def _sync_to_cache_generic(symbol, market, log_func=print):
    """Синхронизация стакана в Redis"""
    if market == 'futures':
        books = gate_futures_order_books
        ts_store = gate_futures_density_timestamps
        lock = gate_futures_order_books_lock
        key = f"scalp:futures:gate:{symbol}"
    else:
        books = gate_spot_order_books
        ts_store = gate_spot_density_timestamps
        lock = gate_spot_order_books_lock
        key = f"scalp:spot:gate:{symbol}"

    try:
        with lock:
            book = books.get(symbol, {})
            ts = ts_store.get(symbol, {})
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
                    if now - ts[price] < MIN_AGE_SECONDS:
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
                    'exchange': 'gate'
                })

        cache.set(key, densities, CACHE_TTL)

        with lock:
            ts_store[symbol] = ts

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_to_cache(gate {market} {symbol}): {e}")
        return 0


def _update_order_book_generic(symbol, bids_delta, asks_delta, market, log_func=print):
    """bids_delta/asks_delta — список [{p: '...', s: '...'}] от Gate WS"""
    if market == 'futures':
        books = gate_futures_order_books
        ts_store = gate_futures_density_timestamps
        lock = gate_futures_order_books_lock
        rate_key = f"gate:futures:{symbol}"
        rate_dict = last_sync_time
    else:
        books = gate_spot_order_books
        ts_store = gate_spot_density_timestamps
        lock = gate_spot_order_books_lock
        rate_key = f"gate:spot:{symbol}"
        rate_dict = gate_spot_last_sync_time

    try:
        with lock:
            if symbol not in books:
                return
            book = books[symbol]
            ts = ts_store.get(symbol, {})
            changed = False

            for row in bids_delta:
                try:
                    price = float(row['p'])
                    qty = abs(float(row['s']))
                except Exception:
                    continue
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

            for row in asks_delta:
                try:
                    price = float(row['p'])
                    qty = abs(float(row['s']))
                except Exception:
                    continue
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

        if changed:
            now = time.time()
            if rate_key not in rate_dict or (now - rate_dict[rate_key]) >= 10:
                _sync_to_cache_generic(symbol, market, log_func)
                rate_dict[rate_key] = now

    except Exception as e:
        log_func(f"❌ update_order_book(gate {market} {symbol}): {e}")


def _process_snapshot_generic(symbol, raw_bids, raw_asks, market, log_func=print):
    """Обработка полного snapshot — полная замена стакана"""
    if market == 'futures':
        books = gate_futures_order_books
        ts_store = gate_futures_density_timestamps
        lock = gate_futures_order_books_lock
    else:
        books = gate_spot_order_books
        ts_store = gate_spot_density_timestamps
        lock = gate_spot_order_books_lock

    try:
        new_bids = {}
        new_asks = {}
        for row in raw_bids:
            try:
                p, q = float(row['p']), abs(float(row['s']))
                if p > 0 and q > 0:
                    new_bids[p] = q
            except Exception:
                continue
        for row in raw_asks:
            try:
                p, q = float(row['p']), abs(float(row['s']))
                if p > 0 and q > 0:
                    new_asks[p] = q
            except Exception:
                continue

        with lock:
            old_ts = ts_store.get(symbol, {})
            new_ts = {}
            for p in new_bids:
                if p in old_ts:
                    new_ts[p] = old_ts[p]
            for p in new_asks:
                if p in old_ts:
                    new_ts[p] = old_ts[p]

            books[symbol] = {'bids': new_bids, 'asks': new_asks}
            ts_store[symbol] = new_ts

        _sync_to_cache_generic(symbol, market, log_func)

    except Exception as e:
        log_func(f"❌ process_snapshot(gate {market} {symbol}): {e}")


# ==========================================
# ИНИЦИАЛИЗАЦИЯ СТАКАНОВ ЧЕРЕЗ REST
# ==========================================

def _init_order_book_generic(symbol, market, log_func=print):
    if market == 'futures':
        url = GATE_FUTURES_REST_URL.format(symbol)
        books = gate_futures_order_books
        ts_store = gate_futures_density_timestamps
        lock = gate_futures_order_books_lock
    else:
        url = GATE_SPOT_REST_URL.format(symbol)
        books = gate_spot_order_books
        ts_store = gate_spot_density_timestamps
        lock = gate_spot_order_books_lock

    try:
        res = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        if not res.ok:
            log_func(f"⚠️ gate {market} {symbol}: HTTP {res.status_code}")
            return 0
        try:
            data = res.json()
        except Exception:
            log_func(f"⚠️ gate {market} {symbol}: не JSON: {res.text[:200]}")
            return 0

        # Логируем что пришло для диагностики
        if isinstance(data, list):
            log_func(f"⚠️ gate {market} {symbol}: API вернул список вместо объекта: {str(data)[:200]}")
            return 0

        if not isinstance(data, dict):
            log_func(f"⚠️ gate {market} {symbol}: неожиданный тип ответа: {type(data)}")
            return 0

        # Gate может вернуть ошибку в формате {label, message}
        if 'label' in data or 'message' in data:
            log_func(f"⚠️ gate {market} {symbol}: ошибка API: {data.get('label')} - {data.get('message')}")
            return 0

        if 'asks' not in data and 'bids' not in data:
            log_func(f"⚠️ gate {market} {symbol}: нет asks/bids в ответе: {str(data)[:200]}")
            return 0

        raw_bids = data.get('bids') or []
        raw_asks = data.get('asks') or []

        def parse_levels(levels):
            """Парсит уровни: и массивы [p, s], и объекты {p, s}"""
            result = {}
            for row in levels:
                try:
                    if isinstance(row, dict):
                        # Gate futures: {"p": "...", "s": ...}
                        p = float(row.get('p', 0))
                        q = abs(float(row.get('s', 0)))
                    elif isinstance(row, (list, tuple)):
                        # Gate spot: ["price", "size"]
                        p = float(row[0])
                        q = abs(float(row[1]))
                    else:
                        continue
                    if p > 0 and q > 0:
                        result[p] = q
                except (ValueError, TypeError, KeyError, IndexError):
                    continue
            return result

        bids = parse_levels(raw_bids)
        asks = parse_levels(raw_asks)

        # ↓↓↓ ВОТ ЭТОГО БЛОКА НЕ ХВАТАЛО ↓↓↓
        if not bids and not asks:
            log_func(f"⚠️ gate {market} {symbol}: пустой стакан после парсинга")
            return 0

        with lock:
            books[symbol] = {'bids': bids, 'asks': asks}
            ts_store[symbol] = {}

        saved_count = _sync_to_cache_generic(symbol, market, log_func)
        log_func(
            f"✅ gate {market} Стакан {symbol}: "
            f"{len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}"
        )
        return saved_count

    except requests.exceptions.Timeout:
        log_func(f"❌ init_order_book(gate {market} {symbol}): timeout")
        return 0
    except Exception as e:
        log_func(f"❌ init_order_book(gate {market} {symbol}): {e}")
        return 0


# Обёртки для обратной совместимости
def init_order_book(symbol, log_func=print):
    return _init_order_book_generic(symbol, 'futures', log_func)


def init_spot_order_book(symbol, log_func=print):
    return _init_order_book_generic(symbol, 'spot', log_func)


def sync_to_cache(symbol, log_func=print):
    return _sync_to_cache_generic(symbol, 'futures', log_func)


def sync_spot_to_cache(symbol, log_func=print):
    return _sync_to_cache_generic(symbol, 'spot', log_func)


def update_order_book(symbol, bids_delta, asks_delta, log_func=print):
    return _update_order_book_generic(symbol, bids_delta, asks_delta, 'futures', log_func)


def update_spot_order_book(symbol, bids_delta, asks_delta, log_func=print):
    return _update_order_book_generic(symbol, bids_delta, asks_delta, 'spot', log_func)


# ==========================================
# ОБРАБОТКА ОЧЕРЕДЕЙ WebSocket
# ==========================================

def _process_message_queue_generic(market, log_func=print):
    if market == 'futures':
        message_queue = gate_futures_message_queue
        expected_channel = 'futures.order_book_update'
    else:
        message_queue = gate_spot_message_queue
        expected_channel = 'spot.order_book_update'

    while True:
        try:
            message = message_queue.get(timeout=1)
            data = json.loads(message)

            # Пропускаем heartbeat и служебные сообщения
            if data.get('event') in ('pong', 'connected'):
                continue
            if data.get('channel') in ('futures.ping', 'spot.ping'):
                continue

            if data.get('channel') != expected_channel:
                continue

            result = data.get('result')
            if not result:
                continue

            # Gate WS формат: {s: "BTC_USDT", b: [...], a: [...], full: bool}
            contract = result.get('s')
            if not contract:
                continue
            symbol = contract.replace('_USDT', '')

            is_full = result.get('full', False)
            raw_bids = result.get('b') or []
            raw_asks = result.get('a') or []

            if is_full:
                _process_snapshot_generic(symbol, raw_bids, raw_asks, market, log_func)
            else:
                _update_order_book_generic(symbol, raw_bids, raw_asks, market, log_func)

        except Empty:
            continue
        except Exception as e:
            log_func(f"❌ gate {market} Ошибка обработки сообщения: {e}")


def process_message_queue(log_func=print):
    _process_message_queue_generic('futures', log_func)


def process_spot_message_queue(log_func=print):
    _process_message_queue_generic('spot', log_func)


# ==========================================
# WebSocket — FUTURES
# ==========================================

def on_message(ws, message):
    try:
        gate_futures_message_queue.put_nowait(message)
    except Exception as e:
        print(f"❌ gate on_message: {e}")


def on_open(ws):
    print(f"✅ gate futures WebSocket открыт: {len(ws.symbols)} символов")
    for symbol in ws.symbols:
        subscribe_msg = {
            "time": int(time.time()),
            "channel": "futures.order_book_update",
            "event": "subscribe",
            "payload": [f"{symbol}_USDT", "100ms", "100"]
        }
        ws.send(json.dumps(subscribe_msg))


def start_websocket(symbols_list, log_func=print):
    global gate_futures_ws_stop_event, gate_futures_ws_instance

    while not gate_futures_ws_stop_event.is_set():
        ws = None
        stop_event = threading.Event()

        try:
            ws = websocket.WebSocketApp(
                GATE_FUTURES_WS_URL,
                on_open=lambda ws: on_open(ws),
                on_message=lambda ws, msg: on_message(ws, msg),
                on_error=lambda ws, err: log_func(f"❌ gate futures WS ошибка: {err}"),
                on_close=lambda ws, code, msg: log_func(f"⚠️ gate futures WS закрыт: {code} {msg}")
            )
            ws.symbols = symbols_list
            gate_futures_ws_instance = ws

            def heartbeat():
                while not stop_event.is_set():
                    time.sleep(17)
                    try:
                        if ws and ws.sock and ws.sock.connected:
                            ws.send(json.dumps({
                                "time": int(time.time()),
                                "channel": "futures.ping"
                            }))
                    except Exception as e:
                        log_func(f"❌ gate futures heartbeat: {e}")
                        break

            threading.Thread(target=heartbeat, daemon=True).start()
            ws.run_forever(ping_interval=0)

        except Exception as e:
            log_func(f"❌ Ошибка в start_websocket(gate futures): {e}")
        finally:
            stop_event.set()
            gate_futures_ws_instance = None

        if gate_futures_ws_stop_event.is_set():
            log_func("🛑 Gate futures WebSocket остановлен для обновления списка")
            break

        log_func("🔁 Gate futures WebSocket переподключение через 3 секунды...")
        time.sleep(3)


# ==========================================
# WebSocket — SPOT
# ==========================================

def on_spot_message(ws, message):
    try:
        gate_spot_message_queue.put_nowait(message)
    except Exception as e:
        print(f"❌ gate spot on_message: {e}")


def on_spot_open(ws):
    print(f"✅ gate spot WebSocket открыт: {len(ws.symbols)} символов")
    for symbol in ws.symbols:
        subscribe_msg = {
            "time": int(time.time()),
            "channel": "spot.order_book_update",
            "event": "subscribe",
            "payload": [f"{symbol}_USDT", "100ms"]
        }
        ws.send(json.dumps(subscribe_msg))


def start_spot_websocket(symbols_list, log_func=print):
    global gate_spot_ws_stop_event, gate_spot_ws_instance

    while not gate_spot_ws_stop_event.is_set():
        ws = None
        stop_event = threading.Event()

        try:
            ws = websocket.WebSocketApp(
                GATE_SPOT_WS_URL,
                on_open=lambda ws: on_spot_open(ws),
                on_message=lambda ws, msg: on_spot_message(ws, msg),
                on_error=lambda ws, err: log_func(f"❌ gate spot WS ошибка: {err}"),
                on_close=lambda ws, code, msg: log_func(f"⚠️ gate spot WS закрыт: {code} {msg}")
            )
            ws.symbols = symbols_list
            gate_spot_ws_instance = ws

            def heartbeat():
                while not stop_event.is_set():
                    time.sleep(17)
                    try:
                        if ws and ws.sock and ws.sock.connected:
                            ws.send(json.dumps({
                                "time": int(time.time()),
                                "channel": "spot.ping"
                            }))
                    except Exception as e:
                        log_func(f"❌ gate spot heartbeat: {e}")
                        break

            threading.Thread(target=heartbeat, daemon=True).start()
            ws.run_forever(ping_interval=0)

        except Exception as e:
            log_func(f"❌ Ошибка в start_spot_websocket(gate spot): {e}")
        finally:
            stop_event.set()
            gate_spot_ws_instance = None

        if gate_spot_ws_stop_event.is_set():
            log_func("🛑 Gate spot WebSocket остановлен для обновления списка")
            break

        log_func("🔁 Gate spot WebSocket переподключение через 3 секунды...")
        time.sleep(3)


# ==========================================
# ЗАПУСК МОНИТОРОВ
# ==========================================

def start_gate_monitor(log_func=print):
    """Запуск мониторинга Gate.io Futures"""
    global gate_futures_symbols

    log_func("🚀 Запуск Gate Futures Monitor...")

    threading.Thread(
        target=lambda: process_message_queue(log_func),
        daemon=True
    ).start()

    candidates = get_top_symbols(60, log_func)
    active_symbols = []
    TARGET = 30

    for symbol in candidates:
        if len(active_symbols) >= TARGET:
            break
        saved_count = init_order_book(symbol, log_func)
        if saved_count > 0:
            active_symbols.append(symbol)
            log_func(f"✅ gate futures {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ gate futures {symbol}: пропущен (нет плотностей > $10K)")
        time.sleep(0.05)

    gate_futures_symbols = active_symbols

    if gate_futures_symbols:
        threading.Thread(
            target=lambda: start_websocket(gate_futures_symbols, log_func),
            daemon=True
        ).start()

    log_func(f"✅ Gate Futures Monitor запущен. Активных монет: {len(gate_futures_symbols)}")

    threading.Thread(
        target=lambda: periodic_gate_futures_refresh(log_func),
        daemon=True
    ).start()


def start_gate_spot_monitor(log_func=print):
    """Запуск мониторинга Gate.io Spot"""
    global gate_spot_symbols

    log_func("🚀 Запуск Gate Spot Monitor...")

    threading.Thread(
        target=lambda: process_spot_message_queue(log_func),
        daemon=True
    ).start()

    candidates = get_top_spot_symbols(60, log_func)
    active_symbols = []
    TARGET = 30

    for symbol in candidates:
        if len(active_symbols) >= TARGET:
            break
        saved_count = init_spot_order_book(symbol, log_func)
        if saved_count > 0:
            active_symbols.append(symbol)
            log_func(f"✅ gate spot {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ gate spot {symbol}: пропущен (нет плотностей > $10K)")
        time.sleep(0.05)

    gate_spot_symbols = active_symbols

    if gate_spot_symbols:
        threading.Thread(
            target=lambda: start_spot_websocket(gate_spot_symbols, log_func),
            daemon=True
        ).start()

    log_func(f"✅ Gate Spot Monitor запущен. Активных монет: {len(gate_spot_symbols)}")

    threading.Thread(
        target=lambda: periodic_gate_spot_refresh(log_func),
        daemon=True
    ).start()


# ==========================================
# ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ СПИСКОВ МОНЕТ
# ==========================================

def _refresh_symbols_generic(market, log_func=print):
    global gate_futures_symbols, gate_futures_ws_stop_event, gate_futures_ws_instance
    global gate_spot_symbols, gate_spot_ws_stop_event, gate_spot_ws_instance

    if market == 'futures':
        symbols_list = gate_futures_symbols
        stop_event = gate_futures_ws_stop_event
        ws_instance = gate_futures_ws_instance
        books = gate_futures_order_books
        ts_store = gate_futures_density_timestamps
        lock = gate_futures_order_books_lock
        get_top = get_top_symbols
        init_book = init_order_book
        start_ws = start_websocket
    else:
        symbols_list = gate_spot_symbols
        stop_event = gate_spot_ws_stop_event
        ws_instance = gate_spot_ws_instance
        books = gate_spot_order_books
        ts_store = gate_spot_density_timestamps
        lock = gate_spot_order_books_lock
        get_top = get_top_spot_symbols
        init_book = init_spot_order_book
        start_ws = start_spot_websocket

    log_func(f"🔄 Обновление списка Gate {market}...")

    old_symbols = set(symbols_list)
    candidates = get_top(60, log_func)

    new_active = []
    TARGET = 30

    for symbol in candidates:
        if len(new_active) >= TARGET:
            break
        if symbol in old_symbols:
            new_active.append(symbol)
            continue
        saved_count = init_book(symbol, log_func)
        if saved_count > 0:
            new_active.append(symbol)
            log_func(f"✅ gate {market} {symbol}: добавлен (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ gate {market} {symbol}: пропущен (нет плотностей > $10K)")
        time.sleep(0.05)

    new_symbols = set(new_active)
    removed = old_symbols - new_symbols
    added = new_symbols - old_symbols

    if removed:
        with lock:
            for symbol in removed:
                books.pop(symbol, None)
                ts_store.pop(symbol, None)
        log_func(f"🗑️ gate {market} удалены: {', '.join(sorted(removed))}")

    if market == 'futures':
        gate_futures_symbols = new_active
    else:
        gate_spot_symbols = new_active

    if removed or added:
        log_func(f"🔄 gate {market}: добавлено {len(added)}, удалено {len(removed)}")

        stop_event.set()

        if ws_instance:
            try:
                ws_instance.close()
            except Exception:
                pass

        time.sleep(2)
        stop_event.clear()

        current_symbols = gate_futures_symbols if market == 'futures' else gate_spot_symbols
        if current_symbols:
            threading.Thread(
                target=lambda: start_ws(current_symbols, log_func),
                daemon=True
            ).start()
    else:
        log_func(f"✅ gate {market}: список не изменился")


def refresh_gate_futures_symbols(log_func=print):
    _refresh_symbols_generic('futures', log_func)


def refresh_gate_spot_symbols(log_func=print):
    _refresh_symbols_generic('spot', log_func)


def periodic_gate_futures_refresh(log_func=print):
    REFRESH_INTERVAL = 300
    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            refresh_gate_futures_symbols(log_func)
        except Exception as e:
            log_func(f"❌ Ошибка при обновлении списка Gate Futures: {e}")


def periodic_gate_spot_refresh(log_func=print):
    REFRESH_INTERVAL = 300
    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            refresh_gate_spot_symbols(log_func)
        except Exception as e:
            log_func(f"❌ Ошибка при обновлении списка Gate Spot: {e}")