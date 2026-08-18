"""
OKX Monitor — мониторинг плотностей OKX Futures
(Spot отключён для теста, чтобы не грузить сервер)
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
okx_futures_order_books = {}
okx_futures_density_timestamps = {}
okx_futures_order_books_lock = threading.Lock()
okx_futures_symbols = []
okx_futures_message_queue = Queue(maxsize=50000)

# Глобальное состояние Spot
okx_spot_order_books = {}
okx_spot_density_timestamps = {}
okx_spot_order_books_lock = threading.Lock()
okx_spot_symbols = []
okx_spot_message_queue = Queue(maxsize=50000)

# URLs OKX
OKX_FUTURES_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
OKX_FUTURES_REST_URL = "https://www.okx.com/api/v5/market/books?instId={}-USDT-SWAP&sz=200"
OKX_SPOT_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
OKX_SPOT_REST_URL = "https://www.okx.com/api/v5/market/books?instId={}-USDT&sz=200"

# Управление WebSocket
okx_futures_ws_stop_event = threading.Event()
okx_futures_ws_instance = None
okx_spot_ws_stop_event = threading.Event()
okx_spot_ws_instance = None
okx_spot_last_sync_time = {}

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
    """Отбор: объём 24ч > $100K (пересчитан в USDT), NATR >= 0.3"""
    try:
        exchange = ccxt.okx({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': 'swap'}
        })
        tickers = exchange.fetch_tickers()
        print(f"🔍 OKX: получено {len(tickers)} тикеров")

        candidates = []
        stablecoins = {'USDT', 'USDC', 'FDUSD', 'DAI', 'TUSD', 'BUSD', 'USDP', 'EURC'}
        MIN_VOLUME_24H = 100_000
        MIN_NATR = 0.3

        passed_volume = 0
        has_natr = 0

        for symbol, data in tickers.items():
            if not symbol.endswith('/USDT:USDT'):
                continue
            clean_symbol = symbol.replace('/USDT:USDT', '')

            if clean_symbol in stablecoins:
                continue
            if not is_valid_symbol(clean_symbol):
                continue

            # OKX swap: volCcy24h в базовой валюте → умножаем на цену = USDT
            info = data.get('info', {}) or {}
            last = float(data.get('last') or 0)
            vol_ccy = float(info.get('volCcy24h') or 0)
            volume = vol_ccy * last

            if volume < MIN_VOLUME_24H:
                passed_volume += 1
                continue

            natr_data = cache.get(f"natr_{clean_symbol}_future") or {}
            natr = natr_data.get('natr_5m14') or 0
            if natr > 0:
                has_natr += 1
            if natr < MIN_NATR:
                continue

            candidates.append((clean_symbol, natr))

        print(f"📊 OKX: отсеяно по объёму {passed_volume}, имеют NATR {has_natr}, кандидатов {len(candidates)}")

        candidates.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in candidates[:limit]]

    except Exception as e:
        print(f"❌ Ошибка в get_top_symbols(okx): {e}")
        return []


def init_order_book(symbol, log_func=print):
    """Инициализация стакана OKX Futures через REST"""
    try:
        url = OKX_FUTURES_REST_URL.format(symbol)
        res = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})

        if not res.ok:
            log_func(f"⚠️ okx {symbol}: HTTP {res.status_code}")
            return 0

        try:
            data = res.json()
        except Exception:
            log_func(f"⚠️ okx {symbol}: не JSON: {res.text[:200]}")
            return 0

        if data.get('code') != '0':
            log_func(f"⚠️ okx {symbol}: code={data.get('code')} msg={data.get('msg')}")
            return 0

        result_list = data.get('data') or []
        if not result_list:
            log_func(f"⚠️ okx {symbol}: пустой data[]")
            return 0

        result = result_list[0]
        raw_bids = result.get('bids') or []
        raw_asks = result.get('asks') or []

        bids = {}
        asks = {}

        # OKX формат: ["price", "qty", "deprecated", "orderCount"]
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
            log_func(f"⚠️ okx {symbol}: пустой стакан")
            return 0

        with okx_futures_order_books_lock:
            okx_futures_order_books[symbol] = {'bids': bids, 'asks': asks}
            okx_futures_density_timestamps[symbol] = {}

        saved_count = sync_to_cache(symbol, log_func)

        log_func(
            f"✅ okx futures Стакан {symbol}: "
            f"{len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}"
        )
        return saved_count

    except requests.exceptions.Timeout:
        log_func(f"❌ init_order_book(okx {symbol}): timeout")
        return 0
    except Exception as e:
        log_func(f"❌ init_order_book(okx {symbol}): {e}")
        return 0


def sync_to_cache(symbol, log_func=print):
    try:
        with okx_futures_order_books_lock:
            book = okx_futures_order_books.get(symbol, {})
            ts = okx_futures_density_timestamps.get(symbol, {})
            if not book:
                return 0

        key = f"scalp:futures:okx:{symbol}"
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
                    'exchange': 'okx'
                })

        cache.set(key, densities, CACHE_TTL)

        with okx_futures_order_books_lock:
            okx_futures_density_timestamps[symbol] = ts

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_to_cache(okx {symbol}): {e}")
        return 0


def update_order_book(symbol, bids_delta, asks_delta, log_func=print):
    global last_sync_time

    try:
        with okx_futures_order_books_lock:
            if symbol not in okx_futures_order_books:
                return

            book = okx_futures_order_books[symbol]
            ts = okx_futures_density_timestamps.get(symbol, {})
            changed = False

            for row in bids_delta:
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

            for row in asks_delta:
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

        if changed:
            now = time.time()
            key = f"okx:futures:{symbol}"
            if key not in last_sync_time or (now - last_sync_time[key]) >= 10:
                sync_to_cache(symbol, log_func)
                last_sync_time[key] = now

    except Exception as e:
        log_func(f"❌ update_order_book(okx {symbol}): {e}")


def process_message_queue(log_func=print):
    message_queue = okx_futures_message_queue

    while True:
        try:
            message = message_queue.get(timeout=1)

            # OKX шлёт "pong" строкой, не JSON
            if message == 'pong':
                continue

            data = json.loads(message)

            if 'arg' not in data or 'data' not in data:
                continue

            arg = data.get('arg', {})
            if arg.get('channel') != 'books':
                continue

            inst_id = arg.get('instId', '')
            if not inst_id.endswith('-USDT-SWAP'):
                continue
            symbol = inst_id.replace('-USDT-SWAP', '')

            action = data.get('action', '')

            for entry in data.get('data', []):
                raw_bids = entry.get('bids', [])
                raw_asks = entry.get('asks', [])

                if action == 'snapshot':
                    # Полный стакан — заменяем целиком
                    new_bids = {}
                    new_asks = {}
                    for row in raw_bids:
                        try:
                            p, q = float(row[0]), float(row[1])
                            if p > 0 and q > 0:
                                new_bids[p] = q
                        except:
                            continue
                    for row in raw_asks:
                        try:
                            p, q = float(row[0]), float(row[1])
                            if p > 0 and q > 0:
                                new_asks[p] = q
                        except:
                            continue

                    with okx_futures_order_books_lock:
                        old_ts = okx_futures_density_timestamps.get(symbol, {})
                        new_ts = {}
                        for p in new_bids:
                            if p in old_ts:
                                new_ts[p] = old_ts[p]
                        for p in new_asks:
                            if p in old_ts:
                                new_ts[p] = old_ts[p]

                        okx_futures_order_books[symbol] = {
                            'bids': new_bids, 'asks': new_asks
                        }
                        okx_futures_density_timestamps[symbol] = new_ts

                    sync_to_cache(symbol, log_func)

                elif action == 'update':
                    update_order_book(symbol, raw_bids, raw_asks, log_func)

        except Empty:
            continue
        except Exception as e:
            log_func(f"❌ okx futures Ошибка обработки: {e}")


def on_message(ws, message):
    try:
        okx_futures_message_queue.put_nowait(message)
    except Exception as e:
        print(f"❌ okx on_message: {e}")


def on_open(ws):
    print(f"✅ okx futures WebSocket открыт: {len(ws.symbols)} символов")
    args = [{"channel": "books", "instId": f"{s}-USDT-SWAP"} for s in ws.symbols]
    subscribe_msg = {"op": "subscribe", "args": args}
    ws.send(json.dumps(subscribe_msg))


def start_websocket(symbols_list, log_func=print):
    global okx_futures_ws_stop_event, okx_futures_ws_instance

    while not okx_futures_ws_stop_event.is_set():
        ws = None
        stop_event = threading.Event()

        try:
            ws = websocket.WebSocketApp(
                OKX_FUTURES_WS_URL,
                on_open=lambda ws: on_open(ws),
                on_message=lambda ws, msg: on_message(ws, msg),
                on_error=lambda ws, err: log_func(f"❌ okx futures WebSocket ошибка: {err}"),
                on_close=lambda ws, code, msg: log_func(f"⚠️ okx futures WebSocket закрыт: {code} {msg}")
            )

            ws.symbols = symbols_list
            okx_futures_ws_instance = ws

            # OKX требует "ping" каждые 25 сек
            def heartbeat():
                while not stop_event.is_set():
                    time.sleep(25)
                    try:
                        if ws and ws.sock and ws.sock.connected:
                            ws.send("ping")
                    except Exception as e:
                        log_func(f"❌ okx heartbeat: {e}")
                        break

            threading.Thread(target=heartbeat, daemon=True).start()

            ws.run_forever(ping_interval=0)  # свой ping через heartbeat

        except Exception as e:
            log_func(f"❌ Ошибка в start_websocket(okx futures): {e}")

        finally:
            stop_event.set()
            okx_futures_ws_instance = None

        if okx_futures_ws_stop_event.is_set():
            log_func("🛑 OKX futures WebSocket остановлен для обновления списка")
            break

        log_func("🔁 OKX futures WebSocket переподключение через 3 секунды...")
        time.sleep(3)


def start_okx_monitor(log_func=print):
    """Запуск мониторинга OKX Futures"""
    global okx_futures_symbols

    log_func("🚀 Запуск OKX Futures Monitor...")

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
            log_func(f"✅ okx futures {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ okx futures {symbol}: пропущен (нет плотностей > $10K)")

        time.sleep(0.05)

    okx_futures_symbols = active_symbols

    if okx_futures_symbols:
        threading.Thread(
            target=lambda: start_websocket(okx_futures_symbols, log_func),
            daemon=True
        ).start()

    log_func(f"✅ OKX Monitor запущен. Активных монет: {len(okx_futures_symbols)}")

    threading.Thread(
        target=lambda: periodic_okx_futures_refresh(log_func),
        daemon=True
    ).start()


def refresh_okx_futures_symbols(log_func=print):
    global okx_futures_symbols, okx_futures_ws_stop_event, okx_futures_ws_instance

    log_func("🔄 Обновление списка OKX Futures...")

    old_symbols = set(okx_futures_symbols)
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
            log_func(f"✅ okx futures {symbol}: добавлен (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ okx futures {symbol}: пропущен (нет плотностей > $10K)")

        time.sleep(0.05)

    new_symbols = set(new_active)
    removed = old_symbols - new_symbols
    added = new_symbols - old_symbols

    if removed:
        with okx_futures_order_books_lock:
            for symbol in removed:
                okx_futures_order_books.pop(symbol, None)
                okx_futures_density_timestamps.pop(symbol, None)
        log_func(f"🗑️ okx futures удалены: {', '.join(sorted(removed))}")

    okx_futures_symbols = new_active

    if removed or added:
        log_func(f"🔄 okx futures: добавлено {len(added)}, удалено {len(removed)}")

        okx_futures_ws_stop_event.set()

        if okx_futures_ws_instance:
            try:
                okx_futures_ws_instance.close()
            except Exception:
                pass

        time.sleep(2)
        okx_futures_ws_stop_event.clear()

        if okx_futures_symbols:
            threading.Thread(
                target=lambda: start_websocket(okx_futures_symbols, log_func),
                daemon=True
            ).start()
    else:
        log_func("✅ okx futures: список не изменился")


def periodic_okx_futures_refresh(log_func=print):
    REFRESH_INTERVAL = 1800
    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            refresh_okx_futures_symbols(log_func)
        except Exception as e:
            log_func(f"❌ Ошибка при обновлении списка OKX Futures: {e}")

# ==========================================
# OKX SPOT MONITOR
# ==========================================

def get_top_spot_symbols(limit=30):
    """Отбор spot монет: объём 24ч > $100K, по объёму"""
    try:
        exchange = ccxt.okx({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': 'spot'}
        })
        tickers = exchange.fetch_tickers()
        print(f"🔍 OKX Spot: получено {len(tickers)} тикеров")

        candidates = []
        stablecoins = {'USDT', 'USDC', 'FDUSD', 'DAI', 'TUSD', 'BUSD', 'USDP', 'EURC'}
        MIN_VOLUME_24H = 100_000

        passed_volume = 0

        for symbol, data in tickers.items():
            if not symbol.endswith('/USDT'):
                continue
            clean_symbol = symbol.replace('/USDT', '')

            if clean_symbol in stablecoins:
                continue
            if not is_valid_symbol(clean_symbol):
                continue

            volume = data.get('quoteVolume') or 0
            if volume < MIN_VOLUME_24H:
                passed_volume += 1
                continue

            candidates.append((clean_symbol, volume))

        print(f"📊 OKX Spot: отсеяно по объёму {passed_volume}, кандидатов {len(candidates)}")

        candidates.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in candidates[:limit]]

    except Exception as e:
        print(f"❌ Ошибка в get_top_spot_symbols(okx): {e}")
        return []


def init_spot_order_book(symbol, log_func=print):
    """Инициализация стакана OKX Spot через REST"""
    try:
        url = OKX_SPOT_REST_URL.format(symbol)
        res = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})

        if not res.ok:
            log_func(f"⚠️ okx spot {symbol}: HTTP {res.status_code}")
            return 0

        try:
            data = res.json()
        except Exception:
            log_func(f"⚠️ okx spot {symbol}: не JSON: {res.text[:200]}")
            return 0

        if data.get('code') != '0':
            log_func(f"⚠️ okx spot {symbol}: code={data.get('code')} msg={data.get('msg')}")
            return 0

        result_list = data.get('data') or []
        if not result_list:
            log_func(f"⚠️ okx spot {symbol}: пустой data[]")
            return 0

        result = result_list[0]
        raw_bids = result.get('bids') or []
        raw_asks = result.get('asks') or []

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
            log_func(f"⚠️ okx spot {symbol}: пустой стакан")
            return 0

        with okx_spot_order_books_lock:
            okx_spot_order_books[symbol] = {'bids': bids, 'asks': asks}
            okx_spot_density_timestamps[symbol] = {}

        saved_count = sync_spot_to_cache(symbol, log_func)

        log_func(
            f"✅ okx spot Стакан {symbol}: "
            f"{len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}"
        )
        return saved_count

    except requests.exceptions.Timeout:
        log_func(f"❌ init_spot_order_book(okx spot {symbol}): timeout")
        return 0
    except Exception as e:
        log_func(f"❌ init_spot_order_book(okx spot {symbol}): {e}")
        return 0


def sync_spot_to_cache(symbol, log_func=print):
    try:
        with okx_spot_order_books_lock:
            book = okx_spot_order_books.get(symbol, {})
            ts = okx_spot_density_timestamps.get(symbol, {})
            if not book:
                return 0

        key = f"scalp:spot:okx:{symbol}"
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
                    'exchange': 'okx'
                })

        cache.set(key, densities, CACHE_TTL)

        with okx_spot_order_books_lock:
            okx_spot_density_timestamps[symbol] = ts

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_spot_to_cache(okx spot {symbol}): {e}")
        return 0


def update_spot_order_book(symbol, bids_delta, asks_delta, log_func=print):
    global okx_spot_last_sync_time

    try:
        with okx_spot_order_books_lock:
            if symbol not in okx_spot_order_books:
                return

            book = okx_spot_order_books[symbol]
            ts = okx_spot_density_timestamps.get(symbol, {})
            changed = False

            for row in bids_delta:
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

            for row in asks_delta:
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

        if changed:
            now = time.time()
            key = f"okx:spot:{symbol}"
            if key not in okx_spot_last_sync_time or (now - okx_spot_last_sync_time[key]) >= 10:
                sync_spot_to_cache(symbol, log_func)
                okx_spot_last_sync_time[key] = now

    except Exception as e:
        log_func(f"❌ update_spot_order_book(okx spot {symbol}): {e}")


def process_spot_message_queue(log_func=print):
    message_queue = okx_spot_message_queue

    while True:
        try:
            message = message_queue.get(timeout=1)

            if message == 'pong':
                continue

            data = json.loads(message)

            if 'arg' not in data or 'data' not in data:
                continue

            arg = data.get('arg', {})
            if arg.get('channel') != 'books':
                continue

            inst_id = arg.get('instId', '')
            # Spot не имеет суффикса -SWAP
            if inst_id.endswith('-USDT-SWAP'):
                continue
            if not inst_id.endswith('-USDT'):
                continue
            symbol = inst_id.replace('-USDT', '')

            action = data.get('action', '')

            for entry in data.get('data', []):
                raw_bids = entry.get('bids', [])
                raw_asks = entry.get('asks', [])

                if action == 'snapshot':
                    new_bids = {}
                    new_asks = {}
                    for row in raw_bids:
                        try:
                            p, q = float(row[0]), float(row[1])
                            if p > 0 and q > 0:
                                new_bids[p] = q
                        except:
                            continue
                    for row in raw_asks:
                        try:
                            p, q = float(row[0]), float(row[1])
                            if p > 0 and q > 0:
                                new_asks[p] = q
                        except:
                            continue

                    with okx_spot_order_books_lock:
                        old_ts = okx_spot_density_timestamps.get(symbol, {})
                        new_ts = {}
                        for p in new_bids:
                            if p in old_ts:
                                new_ts[p] = old_ts[p]
                        for p in new_asks:
                            if p in old_ts:
                                new_ts[p] = old_ts[p]

                        okx_spot_order_books[symbol] = {
                            'bids': new_bids, 'asks': new_asks
                        }
                        okx_spot_density_timestamps[symbol] = new_ts

                    sync_spot_to_cache(symbol, log_func)

                elif action == 'update':
                    update_spot_order_book(symbol, raw_bids, raw_asks, log_func)

        except Empty:
            continue
        except Exception as e:
            log_func(f"❌ okx spot Ошибка обработки: {e}")


def on_spot_message(ws, message):
    try:
        okx_spot_message_queue.put_nowait(message)
    except Exception as e:
        print(f"❌ okx spot on_message: {e}")


def on_spot_open(ws):
    print(f"✅ okx spot WebSocket открыт: {len(ws.symbols)} символов")
    args = [{"channel": "books", "instId": f"{s}-USDT"} for s in ws.symbols]
    subscribe_msg = {"op": "subscribe", "args": args}
    ws.send(json.dumps(subscribe_msg))


def start_spot_websocket(symbols_list, log_func=print):
    global okx_spot_ws_stop_event, okx_spot_ws_instance

    while not okx_spot_ws_stop_event.is_set():
        ws = None
        stop_event = threading.Event()

        try:
            ws = websocket.WebSocketApp(
                OKX_SPOT_WS_URL,
                on_open=lambda ws: on_spot_open(ws),
                on_message=lambda ws, msg: on_spot_message(ws, msg),
                on_error=lambda ws, err: log_func(f"❌ okx spot WebSocket ошибка: {err}"),
                on_close=lambda ws, code, msg: log_func(f"⚠️ okx spot WebSocket закрыт: {code} {msg}")
            )

            ws.symbols = symbols_list
            okx_spot_ws_instance = ws

            def heartbeat():
                while not stop_event.is_set():
                    time.sleep(25)
                    try:
                        if ws and ws.sock and ws.sock.connected:
                            ws.send("ping")
                    except Exception as e:
                        log_func(f"❌ okx spot heartbeat: {e}")
                        break

            threading.Thread(target=heartbeat, daemon=True).start()

            ws.run_forever(ping_interval=0)

        except Exception as e:
            log_func(f"❌ Ошибка в start_spot_websocket(okx spot): {e}")

        finally:
            stop_event.set()
            okx_spot_ws_instance = None

        if okx_spot_ws_stop_event.is_set():
            log_func("🛑 OKX spot WebSocket остановлен для обновления списка")
            break

        log_func("🔁 OKX spot WebSocket переподключение через 3 секунды...")
        time.sleep(3)


def start_okx_spot_monitor(log_func=print):
    """Запуск мониторинга OKX Spot"""
    global okx_spot_symbols

    log_func("🚀 Запуск OKX Spot Monitor...")

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
            log_func(f"✅ okx spot {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ okx spot {symbol}: пропущен (нет плотностей > $10K)")

        time.sleep(0.05)

    okx_spot_symbols = active_symbols

    if okx_spot_symbols:
        threading.Thread(
            target=lambda: start_spot_websocket(okx_spot_symbols, log_func),
            daemon=True
        ).start()

    log_func(f"✅ OKX Spot Monitor запущен. Активных монет: {len(okx_spot_symbols)}")

    threading.Thread(
        target=lambda: periodic_okx_spot_refresh(log_func),
        daemon=True
    ).start()


def refresh_okx_spot_symbols(log_func=print):
    global okx_spot_symbols, okx_spot_ws_stop_event, okx_spot_ws_instance

    log_func("🔄 Обновление списка OKX Spot...")

    old_symbols = set(okx_spot_symbols)
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
            log_func(f"✅ okx spot {symbol}: добавлен (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ okx spot {symbol}: пропущен (нет плотностей > $10K)")

        time.sleep(0.05)

    new_symbols = set(new_active)
    removed = old_symbols - new_symbols
    added = new_symbols - old_symbols

    if removed:
        with okx_spot_order_books_lock:
            for symbol in removed:
                okx_spot_order_books.pop(symbol, None)
                okx_spot_density_timestamps.pop(symbol, None)
        log_func(f"🗑️ okx spot удалены: {', '.join(sorted(removed))}")

    okx_spot_symbols = new_active

    if removed or added:
        log_func(f"🔄 okx spot: добавлено {len(added)}, удалено {len(removed)}")

        okx_spot_ws_stop_event.set()

        if okx_spot_ws_instance:
            try:
                okx_spot_ws_instance.close()
            except Exception:
                pass

        time.sleep(2)
        okx_spot_ws_stop_event.clear()

        if okx_spot_symbols:
            threading.Thread(
                target=lambda: start_spot_websocket(okx_spot_symbols, log_func),
                daemon=True
            ).start()
    else:
        log_func("✅ okx spot: список не изменился")


def periodic_okx_spot_refresh(log_func=print):
    REFRESH_INTERVAL = 1800
    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            refresh_okx_spot_symbols(log_func)
        except Exception as e:
            log_func(f"❌ Ошибка при обновлении списка OKX Spot: {e}")