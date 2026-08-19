"""
Gate Monitor — мониторинг плотностей Gate.io Futures
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
gate_futures_order_books = {}
gate_futures_density_timestamps = {}
gate_futures_order_books_lock = threading.Lock()
gate_futures_symbols = []
gate_futures_message_queue = Queue(maxsize=50000)

# URLs Gate.io
GATE_FUTURES_WS_URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
GATE_FUTURES_REST_URL = "https://api.gateio.ws/api/v4/futures/usdt/order_book?contract={}_USDT&limit=100"

# Управление WebSocket
gate_futures_ws_stop_event = threading.Event()
gate_futures_ws_instance = None

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
    """Отбор: объём 24ч > $100K, по объёму"""
    try:
        exchange = ccxt.gateio({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': 'swap'}
        })
        tickers = exchange.fetch_tickers()

        print(f"🔍 Gate: получено {len(tickers)} тикеров")

        candidates = []
        stablecoins = {'USDT', 'USDC', 'FDUSD', 'DAI', 'TUSD', 'BUSD', 'USDP', 'EURC'}
        MIN_VOLUME_24H = 100_000

        passed_volume = 0
        passed_valid = 0
        passed_stable = 0

        for symbol, data in tickers.items():
            if not symbol.endswith('/USDT:USDT'):
                continue
            clean_symbol = symbol.replace('/USDT:USDT', '')

            if clean_symbol in stablecoins:
                passed_stable += 1
                continue
            if not is_valid_symbol(clean_symbol):
                passed_valid += 1
                continue

            volume = data.get('quoteVolume') or 0
            if volume < MIN_VOLUME_24H:
                passed_volume += 1
                continue

            candidates.append((clean_symbol, volume))

        print(f"📊 Gate статистика: stable={passed_stable}, invalid={passed_valid}, "
              f"low_vol={passed_volume}, candidates={len(candidates)}")

        candidates.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in candidates[:limit]]

    except Exception as e:
        print(f"❌ Ошибка в get_top_symbols(gate): {e}")
        import traceback
        print(traceback.format_exc())
        return []


def init_order_book(symbol, log_func=print):
    """Инициализация стакана Gate.io Futures через REST"""
    try:
        url = GATE_FUTURES_REST_URL.format(symbol)
        res = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})

        if not res.ok:
            log_func(f"⚠️ gate {symbol}: HTTP {res.status_code}")
            return 0

        try:
            data = res.json()
        except Exception:
            log_func(f"⚠️ gate {symbol}: не JSON: {res.text[:200]}")
            return 0

        # Gate возвращает напрямую {asks:[], bids:[]}
        if not isinstance(data, dict) or ('asks' not in data and 'bids' not in data):
            log_func(f"⚠️ gate {symbol}: неожиданный формат ответа")
            return 0

        raw_bids = data.get('bids') or []
        raw_asks = data.get('asks') or []

        bids = {}
        asks = {}

        # Gate futures формат: ["price", "size"] (size в контрактах, может быть отрицательным для asks)
        for row in raw_bids:
            try:
                price = float(row[0])
                qty = abs(float(row[1]))
                if price > 0 and qty > 0:
                    bids[price] = qty
            except Exception:
                continue

        for row in raw_asks:
            try:
                price = float(row[0])
                qty = abs(float(row[1]))
                if price > 0 and qty > 0:
                    asks[price] = qty
            except Exception:
                continue

        if not bids and not asks:
            log_func(f"⚠️ gate {symbol}: пустой стакан")
            return 0

        with gate_futures_order_books_lock:
            gate_futures_order_books[symbol] = {'bids': bids, 'asks': asks}
            gate_futures_density_timestamps[symbol] = {}

        saved_count = sync_to_cache(symbol, log_func)

        log_func(
            f"✅ gate futures Стакан {symbol}: "
            f"{len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}"
        )
        return saved_count

    except requests.exceptions.Timeout:
        log_func(f"❌ init_order_book(gate {symbol}): timeout")
        return 0
    except Exception as e:
        log_func(f"❌ init_order_book(gate {symbol}): {e}")
        return 0


def sync_to_cache(symbol, log_func=print):
    try:
        with gate_futures_order_books_lock:
            book = gate_futures_order_books.get(symbol, {})
            ts = gate_futures_density_timestamps.get(symbol, {})
            if not book:
                return 0

        key = f"scalp:futures:gate:{symbol}"
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
                    'exchange': 'gate'
                })

        cache.set(key, densities, CACHE_TTL)

        with gate_futures_order_books_lock:
            gate_futures_density_timestamps[symbol] = ts

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_to_cache(gate {symbol}): {e}")
        return 0


def update_order_book(symbol, bids_delta, asks_delta, log_func=print):
    global last_sync_time

    try:
        with gate_futures_order_books_lock:
            if symbol not in gate_futures_order_books:
                return

            book = gate_futures_order_books[symbol]
            ts = gate_futures_density_timestamps.get(symbol, {})
            changed = False

            for row in bids_delta:
                price = float(row[0])
                qty = abs(float(row[1]))
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
                price = float(row[0])
                qty = abs(float(row[1]))
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
            key = f"gate:futures:{symbol}"
            if key not in last_sync_time or (now - last_sync_time[key]) >= 10:
                sync_to_cache(symbol, log_func)
                last_sync_time[key] = now

    except Exception as e:
        log_func(f"❌ update_order_book(gate {symbol}): {e}")


def process_message_queue(log_func=print):
    message_queue = gate_futures_message_queue

    while True:
        try:
            message = message_queue.get(timeout=1)
            data = json.loads(message)

            # Gate может присылать pong/heartbeat
            if data.get('event') in ('pong', 'connected'):
                continue
            if data.get('channel') == 'futures.ping':
                continue

            if data.get('channel') != 'futures.order_book_update':
                continue

            result = data.get('result')
            if not result:
                continue

            contract = result.get('c') or result.get('contract')
            if not contract:
                continue
            symbol = contract.replace('_USDT', '')

            event = data.get('event', '')
            raw_bids = result.get('bids') or []
            raw_asks = result.get('asks') or []

            if event == 'all':
                # Snapshot — полная замена стакана
                new_bids = {}
                new_asks = {}
                for row in raw_bids:
                    try:
                        p, q = float(row[0]), abs(float(row[1]))
                        if p > 0 and q > 0:
                            new_bids[p] = q
                    except:
                        continue
                for row in raw_asks:
                    try:
                        p, q = float(row[0]), abs(float(row[1]))
                        if p > 0 and q > 0:
                            new_asks[p] = q
                    except:
                        continue

                with gate_futures_order_books_lock:
                    old_ts = gate_futures_density_timestamps.get(symbol, {})
                    new_ts = {}
                    for p in new_bids:
                        if p in old_ts:
                            new_ts[p] = old_ts[p]
                    for p in new_asks:
                        if p in old_ts:
                            new_ts[p] = old_ts[p]

                    gate_futures_order_books[symbol] = {
                        'bids': new_bids, 'asks': new_asks
                    }
                    gate_futures_density_timestamps[symbol] = new_ts

                sync_to_cache(symbol, log_func)

            elif event == 'update':
                update_order_book(symbol, raw_bids, raw_asks, log_func)

        except Empty:
            continue
        except Exception as e:
            log_func(f"❌ gate futures Ошибка обработки: {e}")


def on_message(ws, message):
    try:
        gate_futures_message_queue.put_nowait(message)
    except Exception as e:
        print(f"❌ gate on_message: {e}")


def on_open(ws):
    print(f"✅ gate futures WebSocket открыт: {len(ws.symbols)} символов")
    contracts = [f"{s}_USDT" for s in ws.symbols]
    subscribe_msg = {
        "time": int(time.time()),
        "channel": "futures.order_book_update",
        "event": "subscribe",
        "payload": contracts
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
                on_error=lambda ws, err: log_func(f"❌ gate futures WebSocket ошибка: {err}"),
                on_close=lambda ws, code, msg: log_func(f"⚠️ gate futures WebSocket закрыт: {code} {msg}")
            )

            ws.symbols = symbols_list
            gate_futures_ws_instance = ws

            # Gate требует пинг каждые 20 секунд
            def heartbeat():
                while not stop_event.is_set():
                    time.sleep(17)
                    try:
                        if ws and ws.sock and ws.sock.connected:
                            ping_msg = {
                                "time": int(time.time()),
                                "channel": "futures.ping"
                            }
                            ws.send(json.dumps(ping_msg))
                    except Exception as e:
                        log_func(f"❌ gate heartbeat: {e}")
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


def start_gate_monitor(log_func=print):
    """Запуск мониторинга Gate.io Futures"""
    global gate_futures_symbols

    log_func("🚀 Запуск Gate Futures Monitor...")

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

    log_func(f"✅ Gate Monitor запущен. Активных монет: {len(gate_futures_symbols)}")

    threading.Thread(
        target=lambda: periodic_gate_futures_refresh(log_func),
        daemon=True
    ).start()


def refresh_gate_futures_symbols(log_func=print):
    global gate_futures_symbols, gate_futures_ws_stop_event, gate_futures_ws_instance

    log_func("🔄 Обновление списка Gate Futures...")

    old_symbols = set(gate_futures_symbols)
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
            log_func(f"✅ gate futures {symbol}: добавлен (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ gate futures {symbol}: пропущен (нет плотностей > $10K)")

        time.sleep(0.05)

    new_symbols = set(new_active)
    removed = old_symbols - new_symbols
    added = new_symbols - old_symbols

    if removed:
        with gate_futures_order_books_lock:
            for symbol in removed:
                gate_futures_order_books.pop(symbol, None)
                gate_futures_density_timestamps.pop(symbol, None)
        log_func(f"🗑️ gate futures удалены: {', '.join(sorted(removed))}")

    gate_futures_symbols = new_active

    if removed or added:
        log_func(f"🔄 gate futures: добавлено {len(added)}, удалено {len(removed)}")

        gate_futures_ws_stop_event.set()

        if gate_futures_ws_instance:
            try:
                gate_futures_ws_instance.close()
            except Exception:
                pass

        time.sleep(2)
        gate_futures_ws_stop_event.clear()

        if gate_futures_symbols:
            threading.Thread(
                target=lambda: start_websocket(gate_futures_symbols, log_func),
                daemon=True
            ).start()
    else:
        log_func("✅ gate futures: список не изменился")


def periodic_gate_futures_refresh(log_func=print):
    REFRESH_INTERVAL = 1800
    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            refresh_gate_futures_symbols(log_func)
        except Exception as e:
            log_func(f"❌ Ошибка при обновлении списка Gate Futures: {e}")