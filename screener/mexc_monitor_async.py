"""
MEXC Monitor ASYNC — WORKER NODE
Читает список монет из Redis (мастер-список от Binance)
Сохранены все критические исправления MEXC (v3 Protobuf, publicAggreDepths, PING).
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
mexc_futures_order_books = {}
mexc_futures_density_timestamps = {}
mexc_futures_symbols = []
mexc_futures_message_queue = asyncio.Queue(maxsize=10000)
mexc_futures_lock = asyncio.Lock()
mexc_futures_reconnect_event = asyncio.Event()

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — SPOT
# ==========================================
mexc_spot_order_books = {}
mexc_spot_density_timestamps = {}
mexc_spot_symbols = []
mexc_spot_message_queue = asyncio.Queue(maxsize=10000)
mexc_spot_lock = asyncio.Lock()
mexc_spot_reconnect_event = asyncio.Event()

# URLs
MEXC_FUTURES_WS_URL = "wss://contract.mexc.com/edge"
MEXC_SPOT_WS_URL = "wss://wbs-api.mexc.com/ws"
MEXC_FUTURES_REST_URL = "https://contract.mexc.com/api/v1/contract/depth/{}_USDT?limit=100"
MEXC_SPOT_REST_URL = "https://api.mexc.com/api/v3/depth?symbol={}USDT&limit=100"

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
        print(f"❌ Ошибка чтения master-списка mexc {market}: {e}")
        return []


async def get_master_symbols_async(market='futures'):
    """Асинхронная обёртка для чтения мастер-списка"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_master_symbols_sync, market)


# ==========================================
# ИНИЦИАЛИЗАЦИЯ СТАКАНОВ (async HTTP)
# ==========================================
async def init_order_book_async(symbol, market='futures', log_func=print):
    """Инициализация стакана через async HTTP. Возвращает количество плотностей (0 при ошибке)."""
    try:
        url = (MEXC_FUTURES_REST_URL if market == 'futures' else MEXC_SPOT_REST_URL).format(symbol)

        client = await get_http_client()
        async with client.get(url) as resp:
            if resp.status != 200:
                log_func(f"⚠️ mexc {market} {symbol}: HTTP ошибка {resp.status}")
                return 0
            data = await resp.json()

        if market == 'futures':
            if not isinstance(data, dict) or data.get('success') is False:
                log_func(f"⚠️ mexc futures {symbol}: API ошибка: {str(data)[:200]}")
                return 0
            inner = data.get('data') or {}
            raw_bids = inner.get('bids') or []
            raw_asks = inner.get('asks') or []
        else:
            if isinstance(data, list) or not isinstance(data, dict):
                log_func(f"⚠️ mexc spot {symbol}: Неожиданный формат ответа REST")
                return 0
            if 'code' in data and data.get('code') != 0:
                log_func(f"⚠️ mexc spot {symbol}: code={data.get('code')}")
                return 0
            raw_bids = data.get('bids') or []
            raw_asks = data.get('asks') or []

        bids = {}
        asks = {}

        for row in raw_bids:
            try:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    price = float(row[0])
                    qty = abs(float(row[1]))
                    if price > 0 and qty > 0:
                        bids[price] = qty
            except Exception:
                continue

        for row in raw_asks:
            try:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    price = float(row[0])
                    qty = abs(float(row[1]))
                    if price > 0 and qty > 0:
                        asks[price] = qty
            except Exception:
                continue

        if not bids and not asks:
            log_func(f"⚠️ mexc {market} {symbol}: REST вернул пустой стакан")
            return 0

        if market == 'futures':
            async with mexc_futures_lock:
                mexc_futures_order_books[symbol] = {'bids': bids, 'asks': asks}
                mexc_futures_density_timestamps[symbol] = {}
        else:
            async with mexc_spot_lock:
                mexc_spot_order_books[symbol] = {'bids': bids, 'asks': asks}
                mexc_spot_density_timestamps[symbol] = {}

        saved_count = await sync_to_cache_async(symbol, market, log_func)
        log_func(f"✅ mexc {market} Стакан {symbol}: {len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}")
        return saved_count

    except Exception as e:
        log_func(f"❌ init_order_book_async(mexc {market} {symbol}): {e}")
        return 0


# ==========================================
# СИНХРОНИЗАЦИЯ В REDIS
# ==========================================
async def sync_to_cache_async(symbol, market='futures', log_func=print):
    try:
        if market == 'futures':
            async with mexc_futures_lock:
                book = mexc_futures_order_books.get(symbol, {})
                ts = mexc_futures_density_timestamps.get(symbol, {})
                if not book:
                    return 0
        else:
            async with mexc_spot_lock:
                book = mexc_spot_order_books.get(symbol, {})
                ts = mexc_spot_density_timestamps.get(symbol, {})
                if not book:
                    return 0

        key = f"scalp:{market}:mexc:{symbol}"
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

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, cache.set, key, densities, CACHE_TTL)
        except RuntimeError:
            pass

        if market == 'futures':
            async with mexc_futures_lock:
                mexc_futures_density_timestamps[symbol] = ts
        else:
            async with mexc_spot_lock:
                mexc_spot_density_timestamps[symbol] = ts

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_to_cache_async(mexc {market} {symbol}): {e}")
        return 0


# ==========================================
# HEARTBEAT
# ==========================================
async def ws_heartbeat(ws, market='futures', log_func=print):
    try:
        while True:
            await asyncio.sleep(15)
            try:
                ping_msg = {"method": "PING"} if market == 'spot' else {"method": "ping"}
                await ws.send(json.dumps(ping_msg))
            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                log_func(f"⚠️ mexc {market} heartbeat завершён: {e}")
                return
    except asyncio.CancelledError:
        return


# ==========================================
# WEBSOCKET LISTENER
# ==========================================
async def ws_listener(market='futures', log_func=print):
    global mexc_futures_symbols, mexc_spot_symbols

    reconnect_event = mexc_futures_reconnect_event if market == 'futures' else mexc_spot_reconnect_event
    ws_url = MEXC_FUTURES_WS_URL if market == 'futures' else MEXC_SPOT_WS_URL

    while True:
        try:
            symbols = mexc_futures_symbols if market == 'futures' else mexc_spot_symbols

            if market == 'spot' and len(symbols) > 30:
                log_func(f"⚠️ mexc spot: обрезка списка с {len(symbols)} до 30 символов (лимит MEXC)")
                symbols = symbols[:30]
                mexc_spot_symbols = symbols

            if not symbols:
                log_func(f"⚠️ mexc {market}: список символов пуст, ожидание...")
                await asyncio.sleep(5)
                continue

            log_func(f"🔌 mexc {market} WS подключение: {len(symbols)} символов")

            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                heartbeat_task = asyncio.create_task(ws_heartbeat(ws, market, log_func))

                try:
                    if market == 'futures':
                        for symbol in symbols:
                            msg = {
                                "method": "sub.depth",
                                "param": {"symbol": f"{symbol}_USDT"}
                            }
                            await ws.send(json.dumps(msg))
                    else:
                        # ИСПРАВЛЕНИЕ: Официальный формат канала MEXC Spot v3 с 100ms обновлением
                        params = [f"spot@public.aggre.depth.v3.api.pb@100ms@{s.upper()}USDT" for s in symbols]
                        msg = {"method": "SUBSCRIPTION", "params": params}
                        await ws.send(json.dumps(msg))
                        log_func(f"📤 mexc spot отправлена подписка: {params[:2]}...")

                    log_func(f"✅ mexc {market} WS подписан на {len(symbols)} символов")

                    while True:
                        if reconnect_event.is_set():
                            reconnect_event.clear()
                            log_func(f"🔄 mexc {market}: сигнал переподключения получен")
                            break

                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue

                        queue = mexc_futures_message_queue if market == 'futures' else mexc_spot_message_queue
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
            log_func(f"⚠️ mexc {market} WS закрыт, переподключение через 3 сек")
            await asyncio.sleep(3)
        except Exception as e:
            log_func(f"❌ mexc {market} WS ошибка: {e}, переподключение через 5 сек")
            await asyncio.sleep(5)


# ==========================================
# ВСПОМОГАТЕЛЬНЫЙ ПАРСЕР
# ==========================================
def parse_depth_levels(levels):
    result = {}
    for row in levels:
        try:
            if isinstance(row, dict):
                p = float(row.get('price', 0))
                q = abs(float(row.get('quantity', 0)))
            elif isinstance(row, (list, tuple)) and len(row) >= 2:
                p = float(row[0])
                q = abs(float(row[1]))
            else:
                continue

            if p > 0 and q > 0:
                result[p] = q
        except Exception:
            continue
    return result


# ==========================================
# ОБРАБОТКА ОЧЕРЕДИ
# ==========================================
async def process_queue(market='futures', log_func=print):
    queue = mexc_futures_message_queue if market == 'futures' else mexc_spot_message_queue

    while True:
        try:
            message = await queue.get()

            if isinstance(message, bytes):
                continue

            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue

            if market == 'futures':
                channel = data.get('channel', '')
                if channel != 'push.depth':
                    continue

                sym = data.get('symbol', '')
                symbol = sym[:-5] if sym.endswith('_USDT') else sym

                inner = data.get('data') or {}
                raw_bids = inner.get('bids') or []
                raw_asks = inner.get('asks') or []

                if not (raw_bids or raw_asks):
                    continue

                await handle_update_async(symbol, raw_bids, raw_asks, market, log_func)

            else:
                # Spot: ИСПРАВЛЕНИЕ парсинга ответа MEXC Protobuf
                channel = data.get('c') or data.get('channel', '')
                if 'depth' not in channel.lower() or '.pb' not in channel.lower():
                    continue

                sym = (data.get('s') or data.get('symbol', '')).upper()
                symbol = sym[:-4] if sym.endswith('USDT') else sym

                # ИСПРАВЛЕНИЕ: данные в MEXC Spot v3 лежат в publicAggreDepths или publicLimitDepths
                inner = data.get('publicAggreDepths') or data.get('publicLimitDepths') or data.get('d') or data.get('data') or {}
                raw_bids = inner.get('bids') or []
                raw_asks = inner.get('asks') or []

                if not (raw_bids or raw_asks):
                    continue

                await handle_update_async(symbol, raw_bids, raw_asks, market, log_func)

        except Exception as e:
            log_func(f"❌ mexc {market} process_queue ошибка: {e}")


async def handle_update_async(symbol, bids_delta, asks_delta, market, log_func):
    new_bids = parse_depth_levels(bids_delta)
    new_asks = parse_depth_levels(asks_delta)

    if not new_bids and not new_asks:
        return

    if market == 'futures':
        async with mexc_futures_lock:
            if symbol not in mexc_futures_order_books:
                return
            book = mexc_futures_order_books[symbol]
            ts = mexc_futures_density_timestamps.get(symbol, {})
            changed = False

            for price, qty in new_bids.items():
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

            for price, qty in new_asks.items():
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
                mexc_futures_density_timestamps[symbol] = ts
    else:
        async with mexc_spot_lock:
            if symbol not in mexc_spot_order_books:
                return
            book = mexc_spot_order_books[symbol]
            ts = mexc_spot_density_timestamps.get(symbol, {})
            changed = False

            for price, qty in new_bids.items():
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

            for price, qty in new_asks.items():
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
                mexc_spot_density_timestamps[symbol] = ts

    key = f"mexc:{market}:{symbol}"
    now = time.time()
    if key not in last_sync_time or (now - last_sync_time[key]) >= SYNC_INTERVAL:
        await sync_to_cache_async(symbol, market, log_func)
        last_sync_time[key] = now


# ==========================================
# 🔥 ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ (Синхронизация с Binance)
# ==========================================
async def periodic_refresh(market='futures', log_func=print):
    global mexc_futures_symbols, mexc_spot_symbols

    while True:
        await asyncio.sleep(300)  # Проверка каждые 5 минут

        try:
            master_symbols = await get_master_symbols_async(market)

            if not master_symbols:
                log_func(f"⚠️ mexc {market}: мастер-список пуст, пропускаем ротацию (ждём Binance)")
                continue

            old_symbols = set(mexc_futures_symbols if market == 'futures' else mexc_spot_symbols)
            new_active = []
            added = []
            removed = old_symbols - set(master_symbols)

            for symbol in master_symbols:
                if symbol not in old_symbols:
                    saved_count = await init_order_book_async(symbol, market, log_func)
                    if saved_count > 0:
                        new_active.append(symbol)
                        added.append(symbol)
                        log_func(f"✅ mexc {market} {symbol}: добавлен (плотностей: {saved_count})")
                    else:
                        log_func(f"⚠️ mexc {market} {symbol}: пропущен (не поддерживается или пустой стакан)")
                else:
                    new_active.append(symbol)

            if market == 'futures':
                mexc_futures_symbols = new_active
                if removed:
                    async with mexc_futures_lock:
                        for sym in removed:
                            mexc_futures_order_books.pop(sym, None)
                            mexc_futures_density_timestamps.pop(sym, None)
                    log_func(f"🗑️ mexc futures удалены: {', '.join(sorted(removed))}")

                if added or removed:
                    mexc_futures_reconnect_event.set()
                    log_func(f"🔄 mexc futures: список синхронизирован (+{len(added)} -{len(removed)})")
            else:
                mexc_spot_symbols = new_active
                if removed:
                    async with mexc_spot_lock:
                        for sym in removed:
                            mexc_spot_order_books.pop(sym, None)
                            mexc_spot_density_timestamps.pop(sym, None)
                    log_func(f"🗑️ mexc spot удалены: {', '.join(sorted(removed))}")

                if added or removed:
                    mexc_spot_reconnect_event.set()
                    log_func(f"🔄 mexc spot: список синхронизирован (+{len(added)} -{len(removed)})")

        except Exception as e:
            log_func(f"❌ Ошибка в periodic_refresh(mexc {market}): {e}")


# ==========================================
# 🔥 ПРИНУДИТЕЛЬНЫЙ SYNC (каждые 30 сек)
# ==========================================
async def periodic_force_sync(log_func=print):
    """Принудительная синхронизация всех монет в Redis каждые 30 секунд."""
    while True:
        await asyncio.sleep(30)
        try:
            for symbol in list(mexc_futures_symbols):
                try:
                    await sync_to_cache_async(symbol, 'futures', log_func)
                except Exception as e:
                    log_func(f"⚠️ mexc futures force sync {symbol}: {e}")

            for symbol in list(mexc_spot_symbols):
                try:
                    await sync_to_cache_async(symbol, 'spot', log_func)
                except Exception as e:
                    log_func(f"⚠️ mexc spot force sync {symbol}: {e}")
        except Exception as e:
            log_func(f"❌ Ошибка periodic_force_sync: {e}")


# ==========================================
# 🔥 ГЛАВНАЯ ФУНКЦИЯ (С ожиданием мастер-списка)
# ==========================================
async def main_async(log_func=print):
    global mexc_futures_symbols, mexc_spot_symbols

    log_func("🚀 Запуск MEXC Async Monitor (WORKER NODE)...")

    # 🔥 Ждём, пока Binance опубликует мастер-список (максимум 60 секунд)
    master_f = await get_master_symbols_async('futures')
    master_s = await get_master_symbols_async('spot')

    attempts = 0
    while (not master_f and not master_s) and attempts < 12:
        log_func("⏳ MEXC ожидает мастер-список от Binance...")
        await asyncio.sleep(5)
        attempts += 1
        master_f = await get_master_symbols_async('futures')
        master_s = await get_master_symbols_async('spot')

    if not master_f and not master_s:
        log_func("⚠️ Мастер-списки пусты после ожидания. MEXC будет ждать обновления.")

    # Инициализируем стаканы. Добавляем в активный список ТОЛЬКО если стакан загрузился успешно.
    active_futures = []
    for symbol in master_f:
        saved_count = await init_order_book_async(symbol, 'futures', log_func)
        if saved_count > 0:
            active_futures.append(symbol)
            log_func(f"✅ mexc futures {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ mexc futures {symbol}: пропущен (не поддерживается или пустой стакан)")

    active_spot = []
    for symbol in master_s:
        saved_count = await init_order_book_async(symbol, 'spot', log_func)
        if saved_count > 0:
            active_spot.append(symbol)
            log_func(f"✅ mexc spot {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ mexc spot {symbol}: пропущен (не поддерживается или пустой стакан)")

    mexc_futures_symbols = active_futures
    mexc_spot_symbols = active_spot

    log_func(f"✅ MEXC Async Monitor инициализирован: {len(active_futures)} futures, {len(active_spot)} spot")

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


def start_mexc_async_monitor(log_func=print):
    """Синхронная обёртка для запуска из Django"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(main_async(log_func))
    except Exception as e:
        log_func(f"❌ MEXC Async Monitor упал: {e}")
        import traceback
        log_func(traceback.format_exc())
    finally:
        pass