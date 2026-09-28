"""
OKX Monitor ASYNC — WORKER NODE
Читает список монет из Redis (мастер-اسпписок от Binance)
"""
import asyncio
import json
import time
import aiohttp
import websockets
from django.core.cache import cache
import ccxt

# ==========================================
# ГЛОБАЛЬНЫЕ ЭКЗЕМПЛЯРЫ CCXT (Безопасная оптимизация)
# ==========================================
ccxt_futures_exchange = ccxt.okx({
    'enableRateLimit': True,
    'timeout': 10000,
    'options': {'defaultType': 'swap'}
})
ccxt_spot_exchange = ccxt.okx({
    'enableRateLimit': True,
    'timeout': 10000,
    'options': {'defaultType': 'spot'}
})

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — FUTURES
# ==========================================
okx_futures_order_books = {}
okx_futures_density_timestamps = {}
okx_futures_symbols = []
okx_futures_message_queue = asyncio.Queue(maxsize=10000)
okx_futures_lock = asyncio.Lock()
okx_futures_reconnect_event = asyncio.Event()
okx_futures_volume_stats = {}  # symbol -> {price: {min, max, sum, count}}

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — SPOT
# ==========================================
okx_spot_order_books = {}
okx_spot_density_timestamps = {}
okx_spot_symbols = []
okx_spot_message_queue = asyncio.Queue(maxsize=10000)
okx_spot_lock = asyncio.Lock()
okx_spot_reconnect_event = asyncio.Event()
okx_spot_volume_stats = {}  # symbol -> {price: {min, max, sum, count}}

# URLs — У OKX один и тот же WS URL для futures и spot!
OKX_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
OKX_FUTURES_REST_URL = "https://www.okx.com/api/v5/market/books?instId={}-USDT-SWAP&sz=200"
OKX_SPOT_REST_URL = "https://www.okx.com/api/v5/market/books?instId={}-USDT&sz=200"

# Rate limiting
last_sync_time = {}
MIN_AGE_SECONDS = 180
CACHE_TTL = 30
SYNC_INTERVAL = 3
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
# 🔥 ЧТЕНИЕ МАСТЕР-СПИСКА ОТ BINANCE
# ==========================================
def _fetch_master_symbols_sync(market='futures'):
    """Синхронное чтение мастер-списка из Redis."""
    try:
        key = f'scalp:master:{market}'
        symbols = cache.get(key)
        if isinstance(symbols, list) and len(symbols) > 0:
            return symbols
        return []
    except Exception as e:
        print(f"❌ Ошибка чтения master-списка okx {market}: {e}")
        return []


async def get_master_symbols_async(market='futures'):
    """Асинхронная обёртка для чтения мастер-списка"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_master_symbols_sync, market)


# ==========================================
# ИНИЦИАЛИЗАЦИЯ СТАКАНОВ (async HTTP)
# ==========================================
async def init_order_book_async(symbol, market='futures', log_func=print):
    """Инициализация стакана через async HTTP. OKX: data — массив, берём [0]."""
    try:
        url = (OKX_FUTURES_REST_URL if market == 'futures' else OKX_SPOT_REST_URL).format(symbol)

        client = await get_http_client()
        async with client.get(url) as resp:
            if resp.status != 200:
                log_func(f"⚠️ okx {market} {symbol}: HTTP {resp.status}")
                return 0
            data = await resp.json()

        # OKX возвращает code "0" при успехе. Если монета не поддерживается, code будет другим.
        if data.get('code') != '0':
            log_func(f"⚠️ okx {market} {symbol}: code={data.get('code')} msg={data.get('msg')}")
            return 0

        result_list = data.get('data') or []
        if not result_list:
            log_func(f"⚠️ okx {market} {symbol}: пустой data[]")
            return 0

        result = result_list[0]
        raw_bids = result.get('bids') or []
        raw_asks = result.get('asks') or []

        bids = {}
        asks = {}

        # OKX формат: [price, qty, deprecated, orderCount]
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
            log_func(f"⚠️ okx {market} {symbol}: пустой стакан")
            return 0

        if market == 'futures':
            async with okx_futures_lock:
                okx_futures_order_books[symbol] = {'bids': bids, 'asks': asks}
                okx_futures_density_timestamps[symbol] = {}
        else:
            async with okx_spot_lock:
                okx_spot_order_books[symbol] = {'bids': bids, 'asks': asks}
                okx_spot_density_timestamps[symbol] = {}

        saved_count = await sync_to_cache_async(symbol, market, log_func)
        log_func(f"✅ okx {market} Стакан {symbol}: {len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}")
        return saved_count

    except Exception as e:
        log_func(f"❌ init_order_book_async(okx {market} {symbol}): {e}")
        return 0


# ==========================================
# СИНХРОНИЗАЦИЯ В REDIS (async)
# ==========================================
async def sync_to_cache_async(symbol, market='futures', log_func=print):
    try:
        if market == 'futures':
            async with okx_futures_lock:
                book = okx_futures_order_books.get(symbol, {})
                ts = okx_futures_density_timestamps.get(symbol, {})
                stats = okx_futures_volume_stats.get(symbol, {})
                if not book:
                    return 0
        else:
            async with okx_spot_lock:
                book = okx_spot_order_books.get(symbol, {})
                ts = okx_spot_density_timestamps.get(symbol, {})
                stats = okx_spot_volume_stats.get(symbol, {})
                if not book:
                    return 0

        key = f"scalp:{market}:okx:{symbol}"
        now = time.time()

        densities = []
        is_first_load = len(ts) == 0
        new_stats = {}

        for side, side_name in [('bids', 'buy'), ('asks', 'sell')]:
            for price, qty in book.get(side, {}).items():
                volume = price * qty

                # ГИСТЕРЕЗИС
                is_mature = (price in ts) and ((now - ts[price]) >= MIN_AGE_SECONDS)
                min_volume = 7000 if is_mature else 10000

                if volume < min_volume:
                    continue

                prev_stat = stats.get(price, {'min': volume, 'max': volume, 'sum': 0, 'count': 0})
                new_stat = {
                    'min': min(prev_stat['min'], volume),
                    'max': max(prev_stat['max'], volume),
                    'sum': prev_stat['sum'] + volume,
                    'count': prev_stat['count'] + 1
                }
                new_stats[price] = new_stat

                if price in ts:
                    age = now - ts[price]
                    if age < MIN_AGE_SECONDS:
                        continue

                    if new_stat['count'] >= 3:
                        avg = new_stat['sum'] / new_stat['count']
                        spread = new_stat['max'] - new_stat['min']
                        stability_ratio = spread / avg if avg > 0 else 0

                        if stability_ratio > 0.5:
                            ts[price] = now
                            new_stats[price] = {'min': volume, 'max': volume, 'sum': volume, 'count': 1}
                            continue
                else:
                    if is_first_load:
                        ts[price] = now - MIN_AGE_SECONDS
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

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, cache.set, key, densities, CACHE_TTL)
        except RuntimeError:
            pass

        if market == 'futures':
            async with okx_futures_lock:
                okx_futures_density_timestamps[symbol] = ts
                okx_futures_volume_stats[symbol] = new_stats
        else:
            async with okx_spot_lock:
                okx_spot_density_timestamps[symbol] = ts
                okx_spot_volume_stats[symbol] = new_stats

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_to_cache_async(okx {market} {symbol}): {e}")
        return 0


# ==========================================
# HEARTBEAT (async) — текстовая строка "ping" для OKX
# ==========================================
async def ws_heartbeat(ws, market='futures', log_func=print):
    """OKX требует текстовую строку 'ping' каждые 25 секунд"""
    try:
        while True:
            await asyncio.sleep(25)
            try:
                await ws.send("ping")
            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                log_func(f"⚠️ okx {market} heartbeat завершён: {e}")
                return
    except asyncio.CancelledError:
        return


# ==========================================
# WEBSOCKET LISTENER (async)
# ==========================================
async def ws_listener(market='futures', log_func=print):
    """OKX использует один WS URL для обоих рынков, различие в instId"""
    global okx_futures_symbols, okx_spot_symbols

    reconnect_event = okx_futures_reconnect_event if market == 'futures' else okx_spot_reconnect_event
    inst_id_suffix = "-USDT-SWAP" if market == 'futures' else "-USDT"

    while True:
        try:
            symbols = okx_futures_symbols if market == 'futures' else okx_spot_symbols

            if not symbols:
                await asyncio.sleep(5)
                continue

            log_func(f"🔌 okx {market} WS подключение: {len(symbols)} символов")

            async with websockets.connect(OKX_WS_URL, ping_interval=None, ping_timeout=None) as ws:
                heartbeat_task = asyncio.create_task(ws_heartbeat(ws, market, log_func))

                try:
                    args = [{"channel": "books", "instId": f"{s}{inst_id_suffix}"} for s in symbols]
                    await ws.send(json.dumps({"op": "subscribe", "args": args}))
                    log_func(f"✅ okx {market} WS подписан на {len(symbols)} символов")

                    while True:
                        if reconnect_event.is_set():
                            reconnect_event.clear()
                            log_func(f"🔄 okx {market}: сигнал переподключения получен")
                            break

                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue

                        queue = okx_futures_message_queue if market == 'futures' else okx_spot_message_queue
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
            log_func(f"⚠️ okx {market} WS закрыт, переподключение через 3 сек")
            await asyncio.sleep(3)
        except Exception as e:
            log_func(f"❌ okx {market} WS ошибка: {e}, переподключение через 5 сек")
            await asyncio.sleep(5)


# ==========================================
# ОБРАБОТКА ОЧЕРЕДИ (async)
# ==========================================
async def process_queue(market='futures', log_func=print):
    queue = okx_futures_message_queue if market == 'futures' else okx_spot_message_queue

    while True:
        try:
            message = await queue.get()

            if message == 'pong':
                continue

            data = json.loads(message)

            if 'arg' not in data or 'data' not in data:
                continue

            arg = data.get('arg', {})
            if arg.get('channel') != 'books':
                continue

            inst_id = arg.get('instId', '')

            if market == 'futures':
                if not inst_id.endswith('-USDT-SWAP'):
                    continue
                symbol = inst_id.replace('-USDT-SWAP', '')
            else:
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
                    await handle_snapshot_async(symbol, raw_bids, raw_asks, market, log_func)
                elif action == 'update' and (raw_bids or raw_asks):
                    await handle_update_async(symbol, raw_bids, raw_asks, market, log_func)

        except Exception as e:
            log_func(f"❌ okx {market} process_queue ошибка: {e}")


async def handle_snapshot_async(symbol, raw_bids, raw_asks, market, log_func):
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
        async with okx_futures_lock:
            old_ts = okx_futures_density_timestamps.get(symbol, {})
            old_stats = okx_futures_volume_stats.get(symbol, {})
            new_ts = {p: old_ts[p] for p in new_bids if p in old_ts}
            new_ts.update({p: old_ts[p] for p in new_asks if p in old_ts})
            new_stats = {p: old_stats[p] for p in new_bids if p in old_stats}
            new_stats.update({p: old_stats[p] for p in new_asks if p in old_stats})

            okx_futures_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            okx_futures_density_timestamps[symbol] = new_ts
            okx_futures_volume_stats[symbol] = new_stats
    else:
        async with okx_spot_lock:
            old_ts = okx_spot_density_timestamps.get(symbol, {})
            old_stats = okx_spot_volume_stats.get(symbol, {})
            new_ts = {p: old_ts[p] for p in new_bids if p in old_ts}
            new_ts.update({p: old_ts[p] for p in new_asks if p in old_ts})
            new_stats = {p: old_stats[p] for p in new_bids if p in old_stats}
            new_stats.update({p: old_stats[p] for p in new_asks if p in old_stats})

            okx_spot_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            okx_spot_density_timestamps[symbol] = new_ts
            okx_spot_volume_stats[symbol] = new_stats

    await sync_to_cache_async(symbol, market, log_func)


async def handle_update_async(symbol, bids_delta, asks_delta, market, log_func):
    if market == 'futures':
        async with okx_futures_lock:
            if symbol not in okx_futures_order_books:
                return
            book = okx_futures_order_books[symbol]
            ts = okx_futures_density_timestamps.get(symbol, {})
            changed = False

            for row in bids_delta:
                try:
                    if not isinstance(row, (list, tuple)) or len(row) < 2:
                        continue
                    price, qty = float(row[0]), float(row[1])
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
                    price, qty = float(row[0]), float(row[1])
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
                okx_futures_density_timestamps[symbol] = ts
    else:
        async with okx_spot_lock:
            if symbol not in okx_spot_order_books:
                return
            book = okx_spot_order_books[symbol]
            ts = okx_spot_density_timestamps.get(symbol, {})
            changed = False

            for row in bids_delta:
                try:
                    if not isinstance(row, (list, tuple)) or len(row) < 2:
                        continue
                    price, qty = float(row[0]), float(row[1])
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
                    price, qty = float(row[0]), float(row[1])
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
                okx_spot_density_timestamps[symbol] = ts

    if changed:
        key = f"okx:{market}:{symbol}"
        now = time.time()
        if now - last_sync_time.get(key, 0.0) >= SYNC_INTERVAL:
            await sync_to_cache_async(symbol, market, log_func)
            last_sync_time[key] = now


# ==========================================
# 🔥 ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ (Синхронизация с Binance)
# ==========================================
async def periodic_refresh(market='futures', log_func=print):
    global okx_futures_symbols, okx_spot_symbols

    while True:
        await asyncio.sleep(300)  # Проверка каждые 5 минут

        try:
            master_symbols = await get_master_symbols_async(market)

            if not master_symbols:
                log_func(f"⚠️ okx {market}: мастер-список пуст, пропускаем ротацию (ждём Binance)")
                continue

            old_symbols = set(okx_futures_symbols if market == 'futures' else okx_spot_symbols)
            new_active = []
            added = []
            removed = old_symbols - set(master_symbols)

            for symbol in master_symbols:
                if symbol not in old_symbols:
                    saved_count = await init_order_book_async(symbol, market, log_func)
                    if saved_count > 0:
                        new_active.append(symbol)
                        added.append(symbol)
                        log_func(f"✅ okx {market} {symbol}: добавлен (плотностей: {saved_count})")
                    else:
                        log_func(f"⚠️ okx {market} {symbol}: пропущен (не поддерживается или пустой стакан)")
                else:
                    new_active.append(symbol)

            if market == 'futures':
                okx_futures_symbols = new_active
                if removed:
                    async with okx_futures_lock:
                        for sym in removed:
                            okx_futures_order_books.pop(sym, None)
                            okx_futures_density_timestamps.pop(sym, None)
                            okx_futures_volume_stats.pop(sym, None)
                            last_sync_time.pop(f"okx:futures:{sym}", None)
                    log_func(f"🗑️ okx futures удалены: {', '.join(sorted(removed))}")

                if added or removed:
                    okx_futures_reconnect_event.set()
                    log_func(f"🔄 okx futures: список синхронизирован (+{len(added)} -{len(removed)})")
            else:
                okx_spot_symbols = new_active
                if removed:
                    async with okx_spot_lock:
                        for sym in removed:
                            okx_spot_order_books.pop(sym, None)
                            okx_spot_density_timestamps.pop(sym, None)
                            okx_spot_volume_stats.pop(sym, None)
                            last_sync_time.pop(f"okx:spot:{sym}", None)
                    log_func(f"🗑️ okx spot удалены: {', '.join(sorted(removed))}")

                if added or removed:
                    okx_spot_reconnect_event.set()
                    log_func(f"🔄 okx spot: список синхронизирован (+{len(added)} -{len(removed)})")

        except Exception as e:
            log_func(f"❌ Ошибка в periodic_refresh(okx {market}): {e}")


# ==========================================
# 🔥 ПРИНУДИТЕЛЬНЫЙ SYNC (каждые 30 сек)
# ==========================================
async def periodic_force_sync(log_func=print):
    """Принудительная синхронизация всех монет в Redis каждые 30 секунд."""
    while True:
        await asyncio.sleep(30)
        try:
            for symbol in list(okx_futures_symbols):
                try:
                    await sync_to_cache_async(symbol, 'futures', log_func)
                except Exception as e:
                    log_func(f"⚠️ okx futures force sync {symbol}: {e}")

            for symbol in list(okx_spot_symbols):
                try:
                    await sync_to_cache_async(symbol, 'spot', log_func)
                except Exception as e:
                    log_func(f"⚠️ okx spot force sync {symbol}: {e}")
        except Exception as e:
            log_func(f"❌ Ошибка periodic_force_sync: {e}")


# ==========================================
# 🔥 ГЛАВНАЯ ФУНКЦИЯ (С ожиданием мастер-списка)
# ==========================================
async def main_async(log_func=print):
    global okx_futures_symbols, okx_spot_symbols

    log_func("🚀 Запуск OKX Async Monitor (WORKER NODE)...")

    # 🔥 Ждём, пока Binance опубликует мастер-список (максимум 60 секунд)
    master_f = await get_master_symbols_async('futures')
    master_s = await get_master_symbols_async('spot')

    attempts = 0
    while (not master_f and not master_s) and attempts < 12:
        log_func("⏳ OKX ожидает мастер-список от Binance...")
        await asyncio.sleep(5)
        attempts += 1
        master_f = await get_master_symbols_async('futures')
        master_s = await get_master_symbols_async('spot')

    if not master_f and not master_s:
        log_func("⚠️ Мастер-списки пусты после ожидания. OKX будет ждать обновления.")

    # Инициализируем стаканы. Добавляем в активный список ТОЛЬКО если стакан загрузился успешно.
    active_futures = []
    for symbol in master_f:
        saved_count = await init_order_book_async(symbol, 'futures', log_func)
        if saved_count > 0:
            active_futures.append(symbol)
            log_func(f"✅ okx futures {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ okx futures {symbol}: пропущен (не поддерживается или пустой стакан)")

    active_spot = []
    for symbol in master_s:
        saved_count = await init_order_book_async(symbol, 'spot', log_func)
        if saved_count > 0:
            active_spot.append(symbol)
            log_func(f"✅ okx spot {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ okx spot {symbol}: пропущен (не поддерживается или пустой стакан)")

    okx_futures_symbols = active_futures
    okx_spot_symbols = active_spot

    log_func(f"✅ OKX Async Monitor инициализирован: {len(active_futures)} futures, {len(active_spot)} spot")

    tasks = [
        ws_listener('futures', log_func),
        ws_listener('spot', log_func),
        process_queue('futures', log_func),
        process_queue('spot', log_func),
        periodic_refresh('futures', log_func),
        periodic_refresh('spot', log_func),
        periodic_force_sync(log_func),  # 🔥 НОВАЯ ЗАДАЧА
    ]

    await asyncio.gather(*tasks)


def start_okx_async_monitor(log_func=print):
    """Синхронная обёртка для запуска из Django"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(main_async(log_func))
    except Exception as e:
        log_func(f"❌ OKX Async Monitor упал: {e}")
        import traceback
        log_func(traceback.format_exc())
    finally:
        pass