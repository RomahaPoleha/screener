"""
Bitget Monitor ASYNC — WORKER NODE
Читает список монет из Redis (мастер-список от Binance)
"""
import asyncio
import json
import time
import aiohttp
import websockets
from django.core.cache import cache

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — FUTURES
# ==========================================
bitget_futures_order_books = {}
bitget_futures_density_timestamps = {}
bitget_futures_symbols = []
bitget_futures_message_queue = asyncio.Queue(maxsize=50000)
bitget_futures_lock = asyncio.Lock()
bitget_futures_reconnect_event = asyncio.Event()
bitget_futures_volume_stats = {}  # symbol -> {price: {min, max, sum, count}}

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — SPOT
# ==========================================
bitget_spot_order_books = {}
bitget_spot_density_timestamps = {}
bitget_spot_symbols = []
bitget_spot_message_queue = asyncio.Queue(maxsize=50000)
bitget_spot_lock = asyncio.Lock()
bitget_spot_reconnect_event = asyncio.Event()
bitget_spot_volume_stats = {}  # symbol -> {price: {min, max, sum, count}}

# URLs
BITGET_WS_URL = "wss://ws.bitget.com/v2/ws/public"
BITGET_FUTURES_REST_URL = "https://api.bitget.com/api/v2/mix/market/merge-depth?symbol={}USDT&productType=USDT-FUTURES&limit=100"
BITGET_SPOT_REST_URL = "https://api.bitget.com/api/v2/spot/market/merge-depth?symbol={}USDT&limit=100"

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
        print(f"❌ Ошибка чтения master-списка bitget {market}: {e}")
        return []


async def get_master_symbols_async(market='futures'):
    """Асинхронная обёртка для чтения мастер-списка"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_master_symbols_sync, market)


# ==========================================
# ИНИЦИАЛИЗАЦИЯ СТАКАНОВ
# ==========================================
async def init_order_book_async(symbol, market='futures', log_func=print):
    try:
        url = (BITGET_FUTURES_REST_URL if market == 'futures' else BITGET_SPOT_REST_URL).format(symbol)

        client = await get_http_client()
        async with client.get(url) as resp:
            if resp.status != 200:
                log_func(f"⚠️ bitget {market} {symbol}: HTTP {resp.status}")
                return 0
            data = await resp.json()

        # Bitget V2 API возвращает code "00000" при успехе
        if data.get('code') != '00000':
            log_func(f"⚠️ bitget {market} {symbol}: code={data.get('code')} msg={data.get('msg')}")
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
            log_func(f"⚠️ bitget {market} {symbol}: пустой стакан")
            return 0

        if market == 'futures':
            async with bitget_futures_lock:
                bitget_futures_order_books[symbol] = {'bids': bids, 'asks': asks}
                bitget_futures_density_timestamps[symbol] = {}
        else:
            async with bitget_spot_lock:
                bitget_spot_order_books[symbol] = {'bids': bids, 'asks': asks}
                bitget_spot_density_timestamps[symbol] = {}

        saved_count = await sync_to_cache_async(symbol, market, log_func)
        log_func(f"✅ bitget {market} Стакан {symbol}: {len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}")
        return saved_count

    except Exception as e:
        log_func(f"❌ init_order_book_async(bitget {market} {symbol}): {e}")
        return 0


# ==========================================
# СИНХРОНИЗАЦИЯ В REDIS (С оптимизациями Bitget)
# ==========================================
async def sync_to_cache_async(symbol, market='futures', log_func=print):
    try:
        if market == 'futures':
            async with bitget_futures_lock:
                book = bitget_futures_order_books.get(symbol, {})
                ts = bitget_futures_density_timestamps.get(symbol, {})
                stats = bitget_futures_volume_stats.get(symbol, {})
                if not book:
                    return 0
        else:
            async with bitget_spot_lock:
                book = bitget_spot_order_books.get(symbol, {})
                ts = bitget_spot_density_timestamps.get(symbol, {})
                stats = bitget_spot_volume_stats.get(symbol, {})
                if not book:
                    return 0

        key = f"scalp:{market}:bitget:{symbol}"
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

                    # Проверка стабильности (порог 1.0 оптимален для шумных снапшотов Bitget)
                    if new_stat['count'] >= 3:
                        avg = new_stat['sum'] / new_stat['count']
                        spread = new_stat['max'] - new_stat['min']
                        stability_ratio = spread / avg if avg > 0 else 0

                        if stability_ratio > 1.0:
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
                    'exchange': 'bitget'
                })

        if densities:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, cache.set, key, densities, CACHE_TTL)
            except RuntimeError:
                pass

        if market == 'futures':
            async with bitget_futures_lock:
                bitget_futures_density_timestamps[symbol] = ts
                bitget_futures_volume_stats[symbol] = new_stats
        else:
            async with bitget_spot_lock:
                bitget_spot_density_timestamps[symbol] = ts
                bitget_spot_volume_stats[symbol] = new_stats

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_to_cache_async(bitget {market} {symbol}): {e}")
        return 0


# ==========================================
# WEBSOCKET LISTENER
# ==========================================
async def ws_heartbeat(ws, market='futures', log_func=print):
    try:
        while True:
            await asyncio.sleep(25)
            try:
                await ws.send("ping")
            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                log_func(f"⚠️ bitget {market} heartbeat завершён: {e}")
                return
    except asyncio.CancelledError:
        return


async def ws_listener(market='futures', log_func=print):
    global bitget_futures_symbols, bitget_spot_symbols

    reconnect_event = bitget_futures_reconnect_event if market == 'futures' else bitget_spot_reconnect_event
    inst_type = "USDT-FUTURES" if market == 'futures' else "SPOT"

    while True:
        try:
            symbols = bitget_futures_symbols if market == 'futures' else bitget_spot_symbols

            if not symbols:
                await asyncio.sleep(5)
                continue

            log_func(f"🔌 bitget {market} WS подключение: {len(symbols)} символов")

            async with websockets.connect(BITGET_WS_URL, ping_interval=None, ping_timeout=None) as ws:
                heartbeat_task = asyncio.create_task(ws_heartbeat(ws, market, log_func))

                try:
                    args = [
                        {
                            "instId": f"{s}USDT",
                            "channel": "books15",
                            "instType": inst_type
                        }
                        for s in symbols
                    ]
                    await ws.send(json.dumps({"op": "subscribe", "args": args}))
                    log_func(f"✅ bitget {market} WS подписан на {len(symbols)} символов")

                    while True:
                        if reconnect_event.is_set():
                            reconnect_event.clear()
                            log_func(f"🔄 bitget {market}: сигнал переподключения получен")
                            break

                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue

                        if message == 'pong':
                            continue

                        queue = bitget_futures_message_queue if market == 'futures' else bitget_spot_message_queue
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
            log_func(f"⚠️ bitget {market} WS закрыт, переподключение через 3 сек")
            await asyncio.sleep(3)
        except Exception as e:
            log_func(f"❌ bitget {market} WS ошибка: {e}, переподключение через 5 сек")
            await asyncio.sleep(5)


# ==========================================
# ОБРАБОТКА ОЧЕРЕДИ
# ==========================================
async def process_queue(market='futures', log_func=print):
    queue = bitget_futures_message_queue if market == 'futures' else bitget_spot_message_queue
    expected_inst_type = "USDT-FUTURES" if market == 'futures' else "SPOT"

    while True:
        try:
            message = await queue.get()

            if message == 'pong':
                continue

            data = json.loads(message)

            action = data.get('action', '')
            if action not in ('snapshot', 'update'):
                continue

            arg = data.get('arg', {})
            if arg.get('channel') not in ('books', 'books15'):
                continue
            if arg.get('instType') != expected_inst_type:
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
                    await handle_snapshot_async(symbol, raw_bids, raw_asks, market, log_func)
                elif action == 'update' and (raw_bids or raw_asks):
                    await handle_update_async(symbol, raw_bids, raw_asks, market, log_func)

        except Exception as e:
            log_func(f"❌ bitget {market} process_queue ошибка: {e}")


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
        async with bitget_futures_lock:
            old_ts = bitget_futures_density_timestamps.get(symbol, {})
            old_stats = bitget_futures_volume_stats.get(symbol, {})
            new_ts = {p: old_ts[p] for p in new_bids if p in old_ts}
            new_ts.update({p: old_ts[p] for p in new_asks if p in old_ts})
            new_stats = {p: old_stats[p] for p in new_bids if p in old_stats}
            new_stats.update({p: old_stats[p] for p in new_asks if p in old_stats})

            bitget_futures_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            bitget_futures_density_timestamps[symbol] = new_ts
            bitget_futures_volume_stats[symbol] = new_stats
    else:
        async with bitget_spot_lock:
            old_ts = bitget_spot_density_timestamps.get(symbol, {})
            old_stats = bitget_spot_volume_stats.get(symbol, {})
            new_ts = {p: old_ts[p] for p in new_bids if p in old_ts}
            new_ts.update({p: old_ts[p] for p in new_asks if p in old_ts})
            new_stats = {p: old_stats[p] for p in new_bids if p in old_stats}
            new_stats.update({p: old_stats[p] for p in new_asks if p in old_stats})

            bitget_spot_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            bitget_spot_density_timestamps[symbol] = new_ts
            bitget_spot_volume_stats[symbol] = new_stats

    await sync_to_cache_async(symbol, market, log_func)


async def handle_update_async(symbol, bids_delta, asks_delta, market, log_func):
    if market == 'futures':
        async with bitget_futures_lock:
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
                bitget_futures_density_timestamps[symbol] = ts
    else:
        async with bitget_spot_lock:
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
                bitget_spot_density_timestamps[symbol] = ts

    if changed:
        key = f"bitget:{market}:{symbol}"
        now = time.time()
        if key not in last_sync_time or (now - last_sync_time[key]) >= SYNC_INTERVAL:
            await sync_to_cache_async(symbol, market, log_func)
            last_sync_time[key] = now


# ==========================================
# 🔥 ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ (Синхронизация с Binance)
# ==========================================
async def periodic_refresh(market='futures', log_func=print):
    global bitget_futures_symbols, bitget_spot_symbols

    while True:
        await asyncio.sleep(300)  # Проверка каждые 5 минут

        try:
            master_symbols = await get_master_symbols_async(market)

            if not master_symbols:
                log_func(f"⚠️ bitget {market}: мастер-список пуст, пропускаем ротацию (ждём Binance)")
                continue

            old_symbols = set(bitget_futures_symbols if market == 'futures' else bitget_spot_symbols)
            new_active = []
            added = []
            removed = old_symbols - set(master_symbols)

            for symbol in master_symbols:
                if symbol not in old_symbols:
                    saved_count = await init_order_book_async(symbol, market, log_func)
                    if saved_count > 0:
                        new_active.append(symbol)
                        added.append(symbol)
                        log_func(f"✅ bitget {market} {symbol}: добавлен (плотностей: {saved_count})")
                    else:
                        log_func(f"⚠️ bitget {market} {symbol}: пропущен (не поддерживается или пустой стакан)")
                else:
                    new_active.append(symbol)

            if market == 'futures':
                bitget_futures_symbols = new_active
                if removed:
                    async with bitget_futures_lock:
                        for sym in removed:
                            bitget_futures_order_books.pop(sym, None)
                            bitget_futures_density_timestamps.pop(sym, None)
                            bitget_futures_volume_stats.pop(sym, None)
                            last_sync_time.pop(f"bitget:futures:{sym}", None)
                    log_func(f"🗑️ bitget futures удалены: {', '.join(sorted(removed))}")

                if added or removed:
                    bitget_futures_reconnect_event.set()
                    log_func(f"🔄 bitget futures: список синхронизирован (+{len(added)} -{len(removed)})")
            else:
                bitget_spot_symbols = new_active
                if removed:
                    async with bitget_spot_lock:
                        for sym in removed:
                            bitget_spot_order_books.pop(sym, None)
                            bitget_spot_density_timestamps.pop(sym, None)
                            bitget_spot_volume_stats.pop(sym, None)
                            last_sync_time.pop(f"bitget:spot:{sym}", None)
                    log_func(f"🗑️ bitget spot удалены: {', '.join(sorted(removed))}")

                if added or removed:
                    bitget_spot_reconnect_event.set()
                    log_func(f"🔄 bitget spot: список синхронизирован (+{len(added)} -{len(removed)})")

        except Exception as e:
            log_func(f"❌ Ошибка в periodic_refresh(bitget {market}): {e}")


# ==========================================
# 🔥 ПРИНУДИТЕЛЬНЫЙ SYNC (каждые 30 сек)
# ==========================================
async def periodic_force_sync(log_func=print):
    """Принудительная синхронизация всех монет в Redis каждые 30 секунд."""
    while True:
        await asyncio.sleep(30)
        try:
            for symbol in list(bitget_futures_symbols):
                try:
                    await sync_to_cache_async(symbol, 'futures', log_func)
                except Exception as e:
                    log_func(f"⚠️ bitget futures force sync {symbol}: {e}")

            for symbol in list(bitget_spot_symbols):
                try:
                    await sync_to_cache_async(symbol, 'spot', log_func)
                except Exception as e:
                    log_func(f"⚠️ bitget spot force sync {symbol}: {e}")
        except Exception as e:
            log_func(f"❌ Ошибка periodic_force_sync: {e}")


# ==========================================
# 🔥 ГЛАВНАЯ ФУНКЦИЯ (С ожиданием мастер-списка)
# ==========================================
async def main_async(log_func=print):
    global bitget_futures_symbols, bitget_spot_symbols

    log_func("🚀 Запуск Bitget Async Monitor (WORKER NODE)...")

    # 🔥 Ждём, пока Binance опубликует мастер-список (максимум 60 секунд)
    master_f = await get_master_symbols_async('futures')
    master_s = await get_master_symbols_async('spot')

    attempts = 0
    while (not master_f and not master_s) and attempts < 12:
        log_func("⏳ Bitget ожидает мастер-список от Binance...")
        await asyncio.sleep(5)
        attempts += 1
        master_f = await get_master_symbols_async('futures')
        master_s = await get_master_symbols_async('spot')

    if not master_f and not master_s:
        log_func("⚠️ Мастер-списки пусты после ожидания. Bitget будет ждать обновления.")

    # Инициализируем стаканы. Добавляем в активный список ТОЛЬКО если стакан загрузился успешно.
    active_futures = []
    for symbol in master_f:
        saved_count = await init_order_book_async(symbol, 'futures', log_func)
        if saved_count > 0:
            active_futures.append(symbol)
            log_func(f"✅ bitget futures {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ bitget futures {symbol}: пропущен (не поддерживается или пустой стакан)")

    active_spot = []
    for symbol in master_s:
        saved_count = await init_order_book_async(symbol, 'spot', log_func)
        if saved_count > 0:
            active_spot.append(symbol)
            log_func(f"✅ bitget spot {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ bitget spot {symbol}: пропущен (не поддерживается или пустой стакан)")

    bitget_futures_symbols = active_futures
    bitget_spot_symbols = active_spot

    log_func(f"✅ Bitget Async Monitor инициализирован: {len(active_futures)} futures, {len(active_spot)} spot")

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


def start_bitget_async_monitor(log_func=print):
    """Синхронная обёртка для запуска из Django"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(main_async(log_func))
    except Exception as e:
        log_func(f"❌ Bitget Async Monitor упал: {e}")
        import traceback
        log_func(traceback.format_exc())
    finally:
        pass