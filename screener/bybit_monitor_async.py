"""
Bybit Monitor ASYNC — WORKER NODE
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
bybit_futures_order_books = {}
bybit_futures_density_timestamps = {}
bybit_futures_symbols = []
bybit_futures_message_queue = asyncio.Queue(maxsize=10000)
bybit_futures_lock = asyncio.Lock()
bybit_futures_reconnect_event = asyncio.Event()
bybit_futures_volume_stats = {}

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — SPOT
# ==========================================
bybit_spot_order_books = {}
bybit_spot_density_timestamps = {}
bybit_spot_symbols = []
bybit_spot_message_queue = asyncio.Queue(maxsize=10000)
bybit_spot_lock = asyncio.Lock()
bybit_spot_reconnect_event = asyncio.Event()
bybit_spot_volume_stats = {}

# URLs
BYBIT_FUTURES_WS_URL = "wss://stream.bybit.com/v5/public/linear"
BYBIT_SPOT_WS_URL = "wss://stream.bybit.com/v5/public/spot"
BYBIT_FUTURES_REST_URL = "https://api.bybit.com/v5/market/orderbook?category=linear&symbol={}USDT&limit=200"
BYBIT_SPOT_REST_URL = "https://api.bybit.com/v5/market/orderbook?category=spot&symbol={}USDT&limit=200"

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
    """Синхронное чтение мастер-списка из Redis"""
    try:
        key = f'scalp:master:{market}'
        symbols = cache.get(key)
        if isinstance(symbols, list) and len(symbols) > 0:
            # Фильтруем невалидные символы (иероглифы, мусор)
            return [s for s in symbols if s.isascii() and s.replace('_', '').isalnum() and len(s) <= 15]
        return []
    except Exception as e:
        print(f"❌ Ошибка чтения master-списка bybit {market}: {e}")
        return []


async def get_master_symbols_async(market='futures'):
    """Асинхронная обёртка для чтения мастер-списка"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_master_symbols_sync, market)


# ==========================================
# ИНИЦИАЛИЗАЦИЯ СТАКАНОВ (async HTTP)
# ==========================================
async def init_order_book_async(symbol, market='futures', log_func=print):
    """Инициализация стакана через async HTTP"""
    try:
        # Защита от мусорных символов
        if not symbol.isascii() or not symbol.replace('_', '').isalnum():
            return 0

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
                stats = bybit_futures_volume_stats.get(symbol, {})
                if not book:
                    return 0
        else:
            async with bybit_spot_lock:
                book = bybit_spot_order_books.get(symbol, {})
                ts = bybit_spot_density_timestamps.get(symbol, {})
                stats = bybit_spot_volume_stats.get(symbol, {})
                if not book:
                    return 0

        key = f"scalp:{market}:bybit:{symbol}"

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
                bybit_futures_volume_stats[symbol] = new_stats
        else:
            async with bybit_spot_lock:
                bybit_spot_density_timestamps[symbol] = ts
                bybit_spot_volume_stats[symbol] = new_stats

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
            old_stats = bybit_futures_volume_stats.get(symbol, {})
            new_ts = {p: old_ts[p] for p in new_bids if p in old_ts}
            new_ts.update({p: old_ts[p] for p in new_asks if p in old_ts})

            new_stats = {p: old_stats[p] for p in new_bids if p in old_stats}
            new_stats.update({p: old_stats[p] for p in new_asks if p in old_stats})

            bybit_futures_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            bybit_futures_density_timestamps[symbol] = new_ts
            bybit_futures_volume_stats[symbol] = new_stats
    else:
        async with bybit_spot_lock:
            old_ts = bybit_spot_density_timestamps.get(symbol, {})
            old_stats = bybit_spot_volume_stats.get(symbol, {})
            new_ts = {p: old_ts[p] for p in new_bids if p in old_ts}
            new_ts.update({p: old_ts[p] for p in new_asks if p in old_ts})

            new_stats = {p: old_stats[p] for p in new_bids if p in old_stats}
            new_stats.update({p: old_stats[p] for p in new_asks if p in old_stats})

            bybit_spot_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            bybit_spot_density_timestamps[symbol] = new_ts
            bybit_spot_volume_stats[symbol] = new_stats

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
                bybit_spot_density_timestamps[symbol] = ts

    if changed:
        key = f"bybit:{market}:{symbol}"
        now = time.time()
        if now - last_sync_time.get(key, 0.0) >= SYNC_INTERVAL:
            await sync_to_cache_async(symbol, market, log_func)
            last_sync_time[key] = now


# ==========================================
# 🔥 ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ (Синхронизация с Binance)
# ==========================================
async def periodic_refresh(market='futures', log_func=print):
    """Периодическая синхронизация с мастер-списком Binance"""
    global bybit_futures_symbols, bybit_spot_symbols

    while True:
        await asyncio.sleep(300)  # Проверка каждые 5 минут

        try:
            master_symbols = await get_master_symbols_async(market)

            if not master_symbols:
                log_func(f"⚠️ bybit {market}: мастер-список пуст, пропускаем ротацию (ждём Binance)")
                continue

            old_symbols = set(bybit_futures_symbols if market == 'futures' else bybit_spot_symbols)
            new_active = []
            added = []
            removed = old_symbols - set(master_symbols)

            for symbol in master_symbols:
                new_active.append(symbol)
                if symbol not in old_symbols:
                    saved_count = await init_order_book_async(symbol, market, log_func)
                    added.append(symbol)
                    if saved_count > 0:
                        log_func(f"✅ bybit {market} {symbol}: добавлен (плотностей: {saved_count})")
                    else:
                        log_func(f"⚠️ bybit {market} {symbol}: добавлен без плотностей")

            if market == 'futures':
                bybit_futures_symbols = new_active
                if removed:
                    async with bybit_futures_lock:
                        for sym in removed:
                            bybit_futures_order_books.pop(sym, None)
                            bybit_futures_density_timestamps.pop(sym, None)
                            bybit_futures_volume_stats.pop(sym, None)
                            last_sync_time.pop(f"bybit:futures:{sym}", None)
                    log_func(f"🗑️ bybit futures удалены: {', '.join(sorted(removed))}")

                if added or removed:
                    bybit_futures_reconnect_event.set()
                    log_func(f"🔄 bybit futures: список синхронизирован (+{len(added)} -{len(removed)})")
            else:
                bybit_spot_symbols = new_active
                if removed:
                    async with bybit_spot_lock:
                        for sym in removed:
                            bybit_spot_order_books.pop(sym, None)
                            bybit_spot_density_timestamps.pop(sym, None)
                            bybit_spot_volume_stats.pop(sym, None)
                            last_sync_time.pop(f"bybit:spot:{sym}", None)
                    log_func(f"🗑️ bybit spot удалены: {', '.join(sorted(removed))}")

                if added or removed:
                    bybit_spot_reconnect_event.set()
                    log_func(f"🔄 bybit spot: список синхронизирован (+{len(added)} -{len(removed)})")

        except Exception as e:
            log_func(f"❌ Ошибка в periodic_refresh(bybit {market}): {e}")


# ==========================================
# 🔥 ПРИНУДИТЕЛЬНЫЙ SYNC (каждые 30 сек)
# ==========================================
async def periodic_force_sync(log_func=print):
    """Принудительная синхронизация всех монет в Redis каждые 30 секунд.
    Гарантирует, что плотности запишутся даже если Redis был недоступен при старте."""
    while True:
        await asyncio.sleep(30)
        try:
            for symbol in list(bybit_futures_symbols):
                try:
                    await sync_to_cache_async(symbol, 'futures', log_func)
                except Exception as e:
                    log_func(f"⚠️ bybit futures force sync {symbol}: {e}")

            for symbol in list(bybit_spot_symbols):
                try:
                    await sync_to_cache_async(symbol, 'spot', log_func)
                except Exception as e:
                    log_func(f"⚠️ bybit spot force sync {symbol}: {e}")
        except Exception as e:
            log_func(f"❌ Ошибка periodic_force_sync: {e}")


# ==========================================
# 🔥 ГЛАВНАЯ ФУНКЦИЯ (С ожиданием мастер-списка)
# ==========================================
async def main_async(log_func=print):
    """Главная асинхронная функция — запускает все задачи"""
    global bybit_futures_symbols, bybit_spot_symbols

    log_func("🚀 Запуск Bybit Async Monitor (WORKER NODE)...")

    # 🔥 Ждём, пока Binance опубликует мастер-список (максимум 60 секунд)
    master_f = await get_master_symbols_async('futures')
    master_s = await get_master_symbols_async('spot')

    attempts = 0
    while (not master_f and not master_s) and attempts < 12:
        log_func("⏳ Bybit ожидает мастер-список от Binance...")
        await asyncio.sleep(5)
        attempts += 1
        master_f = await get_master_symbols_async('futures')
        master_s = await get_master_symbols_async('spot')

    if not master_f and not master_s:
        log_func("⚠️ Мастер-списки пусты после ожидания. Bybit будет ждать обновления.")

    # Инициализируем стаканы
    active_futures = []
    for symbol in master_f:
        saved_count = await init_order_book_async(symbol, 'futures', log_func)
        active_futures.append(symbol)
        if saved_count > 0:
            log_func(f"✅ bybit futures {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ bybit futures {symbol}: принят без плотностей")

    active_spot = []
    for symbol in master_s:
        saved_count = await init_order_book_async(symbol, 'spot', log_func)
        active_spot.append(symbol)
        if saved_count > 0:
            log_func(f"✅ bybit spot {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ bybit spot {symbol}: принят без плотностей")

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
        periodic_force_sync(log_func),  # 🔥 НОВАЯ ЗАДАЧА
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