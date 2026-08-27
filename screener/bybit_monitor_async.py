"""
Bybit Monitor ASYNC — асинхронная версия
Адаптация по образцу bitget_monitor_async.py
"""
import asyncio
import json
import time
import aiohttp
import websockets
from django.core.cache import cache
import ccxt
from . import coin_selection

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — FUTURES
# ==========================================
bybit_futures_order_books = {}
bybit_futures_density_timestamps = {}
bybit_futures_symbols = []
bybit_futures_message_queue = asyncio.Queue(maxsize=10000)
bybit_futures_lock = asyncio.Lock()
bybit_futures_reconnect_event = asyncio.Event()

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — SPOT
# ==========================================
bybit_spot_order_books = {}
bybit_spot_density_timestamps = {}
bybit_spot_symbols = []
bybit_spot_message_queue = asyncio.Queue(maxsize=10000)
bybit_spot_lock = asyncio.Lock()
bybit_spot_reconnect_event = asyncio.Event()

# URLs
BYBIT_FUTURES_WS_URL = "wss://stream.bybit.com/v5/public/linear"
BYBIT_SPOT_WS_URL = "wss://stream.bybit.com/v5/public/spot"
BYBIT_FUTURES_REST_URL = "https://api.bybit.com/v5/market/orderbook?category=linear&symbol={}USDT&limit=200"
BYBIT_SPOT_REST_URL = "https://api.bybit.com/v5/market/orderbook?category=spot&symbol={}USDT&limit=200"

# Rate limiting
last_sync_time = {}

MIN_AGE_SECONDS = 180
CACHE_TTL = 900

_http_client = None


async def get_http_client():
    """Ленивая инициализация aiohttp клиента"""
    global _http_client
    if _http_client is None:
        _http_client = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={'User-Agent': 'Mozilla/5.0'}
        )
    return _http_client


# ==========================================
# ТОП МОНЕТ (синхронный ccxt, запускаем в потоке)
# ==========================================
def _fetch_top_symbols_sync(market_type='linear'):
    """Синхронная функция для ccxt (запускается в отдельном потоке)"""
    try:
        exchange = ccxt.bybit({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': market_type}
        })
        tickers = exchange.fetch_tickers()

        clean_fn = coin_selection.clean_swap if market_type == 'linear' else coin_selection.clean_spot
        coin_selection.update_volume_history(tickers, clean_fn)

        candidates = coin_selection.select_candidates(
            tickers, clean_fn, limit=60,
            log_func=lambda msg: print(msg)
        )
        return candidates[:30]
    except Exception as e:
        print(f"❌ Ошибка fetch_top_symbols(bybit {market_type}): {e}")
        return []


async def get_top_symbols_async(market_type='linear'):
    """Асинхронная обёртка — запускает ccxt в отдельном потоке"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_top_symbols_sync, market_type)


# ==========================================
# ИНИЦИАЛИЗАЦИЯ СТАКАНОВ (async HTTP)
# ==========================================
async def init_order_book_async(symbol, market='futures', log_func=print):
    """Инициализация стакана через async HTTP"""
    try:
        url = (BYBIT_FUTURES_REST_URL if market == 'futures' else BYBIT_SPOT_REST_URL).format(symbol)

        client = await get_http_client()
        async with client.get(url) as resp:
            if resp.status != 200:
                log_func(f"⚠️ bybit {market} {symbol}: HTTP {resp.status}")
                return 0
            data = await resp.json()

        if data.get('retCode') != 0:
            log_func(f"⚠️ bybit {market} {symbol}: retCode={data.get('retCode')} msg={data.get('retMsg')}")
            return 0

        result = data.get('result') or {}
        raw_bids = result.get('b') or []
        raw_asks = result.get('a') or []

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
            log_func(f"⚠️ bybit {market} {symbol}: пустой стакан")
            return 0

        if market == 'futures':
            async with bybit_futures_lock:
                bybit_futures_order_books[symbol] = {'bids': bids, 'asks': asks}
                bybit_futures_density_timestamps[symbol] = {}
        else:
            async with bybit_spot_lock:
                bybit_spot_order_books[symbol] = {'bids': bids, 'asks': asks}
                bybit_spot_density_timestamps[symbol] = {}

        saved_count = await sync_to_cache_async(symbol, market, log_func)
        log_func(f"✅ bybit {market} Стакан {symbol}: {len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}")
        return saved_count

    except Exception as e:
        log_func(f"❌ init_order_book_async(bybit {market} {symbol}): {e}")
        return 0


# ==========================================
# СИНХРОНИЗАЦИЯ В REDIS (async)
# ==========================================
async def sync_to_cache_async(symbol, market='futures', log_func=print):
    try:
        if market == 'futures':
            async with bybit_futures_lock:
                book = bybit_futures_order_books.get(symbol, {})
                ts = bybit_futures_density_timestamps.get(symbol, {})
                if not book:
                    return 0
        else:
            async with bybit_spot_lock:
                book = bybit_spot_order_books.get(symbol, {})
                ts = bybit_spot_density_timestamps.get(symbol, {})
                if not book:
                    return 0

        key = f"scalp:{market}:bybit:{symbol}"
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

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, cache.set, key, densities, CACHE_TTL)
        except RuntimeError:
            pass

        if market == 'futures':
            async with bybit_futures_lock:
                bybit_futures_density_timestamps[symbol] = ts
        else:
            async with bybit_spot_lock:
                bybit_spot_density_timestamps[symbol] = ts

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_to_cache_async(bybit {market} {symbol}): {e}")
        return 0


# ==========================================
# HEARTBEAT (async) — JSON ping для Bybit
# ==========================================
async def ws_heartbeat(ws, market='futures', log_func=print):
    """Отправка JSON {"op":"ping"} каждые 20 секунд для Bybit"""
    try:
        while True:
            await asyncio.sleep(20)
            try:
                await ws.send(json.dumps({"op": "ping"}))
            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                log_func(f"⚠️ bybit {market} heartbeat завершён: {e}")
                return
    except asyncio.CancelledError:
        return


# ==========================================
# WEBSOCKET LISTENER (async)
# ==========================================
async def ws_listener(market='futures', log_func=print):
    """Бесконечный цикл подключения к WebSocket с поддержкой переподключения"""
    global bybit_futures_symbols, bybit_spot_symbols

    reconnect_event = bybit_futures_reconnect_event if market == 'futures' else bybit_spot_reconnect_event
    ws_url = BYBIT_FUTURES_WS_URL if market == 'futures' else BYBIT_SPOT_WS_URL

    while True:
        try:
            symbols = bybit_futures_symbols if market == 'futures' else bybit_spot_symbols

            if not symbols:
                await asyncio.sleep(5)
                continue

            log_func(f"🔌 bybit {market} WS подключение: {len(symbols)} символов")

            async with websockets.connect(ws_url, ping_interval=None, ping_timeout=None) as ws:
                heartbeat_task = asyncio.create_task(ws_heartbeat(ws, market, log_func))

                try:
                    # Подписка Bybit: {"op": "subscribe", "args": ["orderbook.200.BTCUSDT", ...]}
                    args = [f"orderbook.200.{s}USDT" for s in symbols]
                    await ws.send(json.dumps({"op": "subscribe", "args": args}))
                    log_func(f"✅ bybit {market} WS подписан на {len(symbols)} символов")

                    while True:
                        if reconnect_event.is_set():
                            reconnect_event.clear()
                            log_func(f"🔄 bybit {market}: сигнал переподключения получен")
                            break

                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue

                        queue = bybit_futures_message_queue if market == 'futures' else bybit_spot_message_queue
                        try:
                            queue.put_nowait(message)
                        except asyncio.QueueFull:
                            pass

                finally:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        pass

        except websockets.exceptions.ConnectionClosed:
            log_func(f"⚠️ bybit {market} WS закрыт, переподключение через 3 сек")
            await asyncio.sleep(3)
        except Exception as e:
            log_func(f"❌ bybit {market} WS ошибка: {e}, переподключение через 5 сек")
            await asyncio.sleep(5)


# ==========================================
# ОБРАБОТКА ОЧЕРЕДИ (async)
# ==========================================
async def process_queue(market='futures', log_func=print):
    """Обработка очереди сообщений Bybit"""
    queue = bybit_futures_message_queue if market == 'futures' else bybit_spot_message_queue

    while True:
        try:
            message = await queue.get()

            data = json.loads(message)

            # Bybit присылает подтверждение подписки с "op": "subscribe" и "success": true
            # Пропускаем их
            if data.get('op') == 'subscribe' or data.get('op') == 'pong':
                continue

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
            raw_bids = d.get('b', [])
            raw_asks = d.get('a', [])

            if msg_type == 'snapshot':
                await handle_snapshot_async(symbol, raw_bids, raw_asks, market, log_func)
            elif msg_type == 'delta' and (raw_bids or raw_asks):
                await handle_update_async(symbol, raw_bids, raw_asks, market, log_func)

        except Exception as e:
            log_func(f"❌ bybit {market} process_queue ошибка: {e}")


async def handle_snapshot_async(symbol, raw_bids, raw_asks, market, log_func):
    """Обработка снапшота — полная замена стакана"""
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

    if market == 'futures':
        async with bybit_futures_lock:
            old_ts = bybit_futures_density_timestamps.get(symbol, {})
            new_ts = {}
            for p in new_bids:
                if p in old_ts:
                    new_ts[p] = old_ts[p]
            for p in new_asks:
                if p in old_ts:
                    new_ts[p] = old_ts[p]

            bybit_futures_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            bybit_futures_density_timestamps[symbol] = new_ts
    else:
        async with bybit_spot_lock:
            old_ts = bybit_spot_density_timestamps.get(symbol, {})
            new_ts = {}
            for p in new_bids:
                if p in old_ts:
                    new_ts[p] = old_ts[p]
            for p in new_asks:
                if p in old_ts:
                    new_ts[p] = old_ts[p]

            bybit_spot_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            bybit_spot_density_timestamps[symbol] = new_ts

    await sync_to_cache_async(symbol, market, log_func)


async def handle_update_async(symbol, bids_delta, asks_delta, market, log_func):
    """Обработка дельты — обновление стакана"""
    if market == 'futures':
        async with bybit_futures_lock:
            if symbol not in bybit_futures_order_books:
                return
            book = bybit_futures_order_books[symbol]
            ts = bybit_futures_density_timestamps.get(symbol, {})
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
                bybit_futures_density_timestamps[symbol] = ts
    else:
        async with bybit_spot_lock:
            if symbol not in bybit_spot_order_books:
                return
            book = bybit_spot_order_books[symbol]
            ts = bybit_spot_density_timestamps.get(symbol, {})
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
                bybit_spot_density_timestamps[symbol] = ts

    # Rate limit: sync раз в 3 секунды
    key = f"bybit:{market}:{symbol}"
    now = time.time()
    if key not in last_sync_time or (now - last_sync_time[key]) >= 3:
        await sync_to_cache_async(symbol, market, log_func)
        last_sync_time[key] = now


# ==========================================
# ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ СПИСКОВ
# ==========================================
async def periodic_refresh(market='futures', log_func=print):
    """Периодическое обновление списка монет каждые 5 минут"""
    global bybit_futures_symbols, bybit_spot_symbols

    while True:
        await asyncio.sleep(300)  # 5 минут

        try:
            market_type = 'linear' if market == 'futures' else 'spot'
            candidates = await get_top_symbols_async(market_type)

            old_symbols = set(bybit_futures_symbols if market == 'futures' else bybit_spot_symbols)

            new_active = []
            TARGET = 30

            # ШАГ 1: Сохраняем ВСЕ старые монеты (стабильность)
            for symbol in old_symbols:
                if len(new_active) >= TARGET:
                    break
                new_active.append(symbol)

            # ШАГ 2: Добавляем новых кандидатов если есть место
            for symbol in candidates:
                if len(new_active) >= TARGET:
                    break
                if symbol in new_active:
                    continue

                saved_count = await init_order_book_async(symbol, market, log_func)
                if saved_count > 0:
                    new_active.append(symbol)
                    log_func(f"✅ bybit {market} {symbol}: добавлен (плотностей: {saved_count})")
                else:
                    log_func(f"⚠️ bybit {market} {symbol}: пропущен (нет плотностей)")

            if market == 'futures':
                removed = old_symbols - set(new_active)
                bybit_futures_symbols = new_active
                if removed:
                    async with bybit_futures_lock:
                        for sym in removed:
                            bybit_futures_order_books.pop(sym, None)
                            bybit_futures_density_timestamps.pop(sym, None)
                    log_func(f"🗑️ bybit futures удалены: {', '.join(sorted(removed))}")

                bybit_futures_reconnect_event.set()
            else:
                removed = old_symbols - set(new_active)
                bybit_spot_symbols = new_active
                if removed:
                    async with bybit_spot_lock:
                        for sym in removed:
                            bybit_spot_order_books.pop(sym, None)
                            bybit_spot_density_timestamps.pop(sym, None)
                    log_func(f"🗑️ bybit spot удалены: {', '.join(sorted(removed))}")

                bybit_spot_reconnect_event.set()

            log_func(f"🔄 bybit {market}: ротация завершена, активных {len(new_active)}")

        except Exception as e:
            log_func(f"❌ Ошибка в periodic_refresh(bybit {market}): {e}")


# ==========================================
# ГЛАВНАЯ ФУНКЦИЯ
# ==========================================
async def main_async(log_func=print):
    """Главная асинхронная функция — запускает все задачи"""
    global bybit_futures_symbols, bybit_spot_symbols

    log_func("🚀 Запуск Bybit Async Monitor...")

    futures_candidates = await get_top_symbols_async('linear')
    spot_candidates = await get_top_symbols_async('spot')

    active_futures = []
    active_spot = []

    for symbol in futures_candidates[:30]:
        saved_count = await init_order_book_async(symbol, 'futures', log_func)
        if saved_count > 0:
            active_futures.append(symbol)
            log_func(f"✅ bybit futures {symbol}: принят (плотностей: {saved_count})")

    for symbol in spot_candidates[:30]:
        saved_count = await init_order_book_async(symbol, 'spot', log_func)
        if saved_count > 0:
            active_spot.append(symbol)
            log_func(f"✅ bybit spot {symbol}: принят (плотностей: {saved_count})")

    bybit_futures_symbols = active_futures
    bybit_spot_symbols = active_spot

    log_func(f"✅ Bybit Async Monitor инициализирован: {len(active_futures)} futures, {len(active_spot)} spot")

    tasks = [
        ws_listener('futures', log_func),
        ws_listener('spot', log_func),
        process_queue('futures', log_func),
        process_queue('spot', log_func),
        periodic_refresh('futures', log_func),
        periodic_refresh('spot', log_func),
    ]

    await asyncio.gather(*tasks)


def start_bybit_async_monitor(log_func=print):
    """Синхронная обёртка для запуска из Django"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(main_async(log_func))
    except Exception as e:
        log_func(f"❌ Bybit Async Monitor упал: {e}")
        import traceback
        log_func(traceback.format_exc())
    finally:
        pass