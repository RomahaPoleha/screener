"""
MEXC Monitor ASYNC — асинхронная версия
MEXC специфика:
  - Futures: push.depth (дельта), sub.depth, символы с _USDT
  - Spot: spot@public.depth.v3.api (полная замена каждый раз), символы USDT
  - Heartbeat JSON: {"method": "ping"} каждые 15 сек
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

# URLs — У MEXC РАЗНЫЕ WS/REST для futures и spot
MEXC_FUTURES_WS_URL = "wss://contract.mexc.com/edge"
MEXC_SPOT_WS_URL = "wss://wbs.mexc.com/ws"
MEXC_FUTURES_REST_URL = "https://contract.mexc.com/api/v1/contract/depth/{}_USDT?limit=100"
MEXC_SPOT_REST_URL = "https://api.mexc.com/api/v3/depth?symbol={}USDT&limit=100"

# Rate limiting
last_sync_time = {}

MIN_AGE_SECONDS = 180
CACHE_TTL = 900
SYNC_INTERVAL = 3

_http_client = None


async def get_http_client():
    global _http_client
    if _http_client is None:
        _http_client = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={'User-Agent': 'Mozilla/5.0'}
        )
    return _http_client


# ==========================================
# ТОП МОНЕТ
# ==========================================
def _fetch_top_symbols_sync(market='swap', log_func=print):
    try:
        exchange = ccxt.mexc({
            'enableRateLimit': True,
            'timeout': 15000,
            'options': {'defaultType': market}
        })
        tickers = exchange.fetch_tickers()

        # MEXC futures специфика: пересчёт volume → quoteVolume
        if market == 'swap':
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

        clean_fn = coin_selection.clean_swap if market == 'swap' else coin_selection.clean_spot
        coin_selection.update_volume_history(tickers, clean_fn)

        candidates = coin_selection.select_candidates(
            tickers, clean_fn, limit=60,
            log_func=log_func
        )
        return candidates[:30]
    except Exception as e:
        log_func(f"❌ Ошибка fetch_top_symbols(mexc {market}): {e}")
        return []


async def get_top_symbols_async(market='swap', log_func=print):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_top_symbols_sync, market, log_func)


# ==========================================
# ИНИЦИАЛИЗАЦИЯ СТАКАНОВ (async HTTP)
# ==========================================
async def init_order_book_async(symbol, market='futures', log_func=print):
    try:
        url = (MEXC_FUTURES_REST_URL if market == 'futures' else MEXC_SPOT_REST_URL).format(symbol)

        client = await get_http_client()
        async with client.get(url) as resp:
            if resp.status != 200:
                log_func(f"⚠️ mexc {market} {symbol}: HTTP {resp.status}")
                return 0
            data = await resp.json()

        if market == 'futures':
            # Futures формат: {success: true, data: {bids, asks}}
            if not isinstance(data, dict) or data.get('success') is False:
                log_func(f"⚠️ mexc futures {symbol}: API ошибка: {str(data)[:200]}")
                return 0
            inner = data.get('data') or {}
            raw_bids = inner.get('bids') or []
            raw_asks = inner.get('asks') or []
        else:
            # Spot формат: {bids, asks} или {code: 0, data: ...}
            if isinstance(data, list):
                return 0
            if not isinstance(data, dict):
                return 0
            if 'code' in data and data.get('code') != 0:
                log_func(f"⚠️ mexc spot {symbol}: code={data.get('code')}")
                return 0
            raw_bids = data.get('bids') or []
            raw_asks = data.get('asks') or []

        bids = {}
        asks = {}

        # Формат: [price, qty, orderCount?] — берём первые два элемента
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
            log_func(f"⚠️ mexc {market} {symbol}: пустой стакан")
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
# HEARTBEAT — JSON {"method":"ping"} для MEXC
# ==========================================
async def ws_heartbeat(ws, market='futures', log_func=print):
    try:
        while True:
            await asyncio.sleep(15)
            try:
                if market == 'futures':
                    await ws.send(json.dumps({"method": "ping"}))
                else:
                    # Spot ожидает просто строку "ping", не JSON!
                    await ws.send("ping")
            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                log_func(f"⚠️ mexc {market} heartbeat завершён: {e}")
                return
    except asyncio.CancelledError:
        return


# ==========================================
# WEBSOCKET LISTENER (разные URL для futures/spot)
# ==========================================
async def ws_listener(market='futures', log_func=print):
    global mexc_futures_symbols, mexc_spot_symbols

    reconnect_event = mexc_futures_reconnect_event if market == 'futures' else mexc_spot_reconnect_event
    ws_url = MEXC_FUTURES_WS_URL if market == 'futures' else MEXC_SPOT_WS_URL

    while True:
        try:
            symbols = mexc_futures_symbols if market == 'futures' else mexc_spot_symbols

            if not symbols:
                await asyncio.sleep(5)
                continue

            log_func(f"🔌 mexc {market} WS подключение: {len(symbols)} символов")

            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                heartbeat_task = asyncio.create_task(ws_heartbeat(ws, market, log_func))

                try:
                    # Разная подписка для futures и spot
                    if market == 'futures':
                        # Futures: каждая монета отдельным сообщением
                        for symbol in symbols:
                            msg = {
                                "method": "sub.depth",
                                "param": {"symbol": f"{symbol}_USDT"}
                            }
                            await ws.send(json.dumps(msg))
                    else:
                        # Spot: один SUBSCRIPTION со списком параметров
                        params = [f"spot@public.depth.v3.api@{s}USDT" for s in symbols]
                        msg = {"method": "SUBSCRIPTION", "params": params}
                        await ws.send(json.dumps(msg))

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
# ОБРАБОТКА ОЧЕРЕДИ
# ==========================================
async def process_queue(market='futures', log_func=print):
    """Обработка очереди. MEXC имеет РАЗНЫЕ форматы для futures и spot!"""
    queue = mexc_futures_message_queue if market == 'futures' else mexc_spot_message_queue

    while True:
        try:
            message = await queue.get()
            data = json.loads(message)

            if market == 'futures':
                # Futures: push.depth с дельтой
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
                # Spot: spot@public.depth — полная замена каждый раз
                channel = data.get('c', '')
                if not channel.startswith('spot@public.depth'):
                    continue

                sym = data.get('s', '')
                symbol = sym[:-4] if sym.endswith('USDT') else sym

                inner = data.get('d') or {}
                raw_bids = inner.get('bids') or []
                raw_asks = inner.get('asks') or []

                if not (raw_bids or raw_asks):
                    continue

                # Spot ВСЕГДА снапшот — полная замена
                await handle_snapshot_async(symbol, raw_bids, raw_asks, market, log_func)

        except Exception as e:
            log_func(f"❌ mexc {market} process_queue ошибка: {e}")


async def handle_snapshot_async(symbol, raw_bids, raw_asks, market, log_func):
    """Обработка снапшота — полная замена стакана"""
    new_bids = {}
    new_asks = {}

    for row in raw_bids:
        try:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                p, q = float(row[0]), abs(float(row[1]))
                if p > 0 and q > 0:
                    new_bids[p] = q
        except Exception:
            continue

    for row in raw_asks:
        try:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                p, q = float(row[0]), abs(float(row[1]))
                if p > 0 and q > 0:
                    new_asks[p] = q
        except Exception:
            continue

    if market == 'futures':
        async with mexc_futures_lock:
            old_ts = mexc_futures_density_timestamps.get(symbol, {})
            new_ts = {}
            for p in new_bids:
                if p in old_ts:
                    new_ts[p] = old_ts[p]
                else:
                    new_ts[p] = time.time()  # ← ДОБАВЛЕНО
            for p in new_asks:
                if p in old_ts:
                    new_ts[p] = old_ts[p]
                else:
                    new_ts[p] = time.time()  # ← ДОБАВЛЕНО

            mexc_futures_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            mexc_futures_density_timestamps[symbol] = new_ts
    else:
        async with mexc_spot_lock:
            old_ts = mexc_spot_density_timestamps.get(symbol, {})
            new_ts = {}
            for p in new_bids:
                if p in old_ts:
                    new_ts[p] = old_ts[p]
                else:
                    new_ts[p] = time.time()  # ← ДОБАВЛЕНО
            for p in new_asks:
                if p in old_ts:
                        new_ts[p] = old_ts[p]
                else:
                    new_ts[p] = time.time()  # ← ДОБАВЛЕНО

            mexc_spot_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            mexc_spot_density_timestamps[symbol] = new_ts

            mexc_spot_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            mexc_spot_density_timestamps[symbol] = new_ts

    await sync_to_cache_async(symbol, market, log_func)


async def handle_update_async(symbol, bids_delta, asks_delta, market, log_func):
    """Обработка дельты — обновление стакана"""
    if market == 'futures':
        async with mexc_futures_lock:
            if symbol not in mexc_futures_order_books:
                return
            book = mexc_futures_order_books[symbol]
            ts = mexc_futures_density_timestamps.get(symbol, {})
            changed = False

            for row in bids_delta:
                try:
                    if not isinstance(row, (list, tuple)) or len(row) < 2:
                        continue
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
                except Exception:
                    continue

            for row in asks_delta:
                try:
                    if not isinstance(row, (list, tuple)) or len(row) < 2:
                        continue
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
                except Exception:
                    continue

            if changed:
                mexc_futures_density_timestamps[symbol] = ts
    else:
        async with mexc_spot_lock:
            if symbol not in mexc_spot_order_books:
                return
            book = mexc_spot_order_books[symbol]
            ts = mexc_spot_density_timestamps.get(symbol, {})
            changed = False

            for row in bids_delta:
                try:
                    if not isinstance(row, (list, tuple)) or len(row) < 2:
                        continue
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
                except Exception:
                    continue

            for row in asks_delta:
                try:
                    if not isinstance(row, (list, tuple)) or len(row) < 2:
                        continue
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
                except Exception:
                    continue

            if changed:
                mexc_spot_density_timestamps[symbol] = ts

    # Rate limit: 3 сек
    key = f"mexc:{market}:{symbol}"
    now = time.time()
    if key not in last_sync_time or (now - last_sync_time[key]) >= SYNC_INTERVAL:
        await sync_to_cache_async(symbol, market, log_func)
        last_sync_time[key] = now


# ==========================================
# ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ СПИСКОВ
# ==========================================
async def periodic_refresh(market='futures', log_func=print):
    global mexc_futures_symbols, mexc_spot_symbols

    while True:
        await asyncio.sleep(300)

        try:
            market_type = 'swap' if market == 'futures' else 'spot'
            candidates = await get_top_symbols_async(market_type, log_func)

            old_symbols = set(mexc_futures_symbols if market == 'futures' else mexc_spot_symbols)

            new_active = []
            TARGET = 30

            # Сохраняем старые
            for symbol in old_symbols:
                if len(new_active) >= TARGET:
                    break
                new_active.append(symbol)

            # Добавляем новых
            for symbol in candidates:
                if len(new_active) >= TARGET:
                    break
                if symbol in new_active:
                    continue
                saved_count = await init_order_book_async(symbol, market, log_func)
                if saved_count > 0:
                    new_active.append(symbol)
                    log_func(f"✅ mexc {market} {symbol}: добавлен (плотностей: {saved_count})")
                else:
                    log_func(f"⚠️ mexc {market} {symbol}: пропущен")

            if market == 'futures':
                removed = old_symbols - set(new_active)
                added = set(new_active) - old_symbols
                mexc_futures_symbols = new_active
                if removed:
                    async with mexc_futures_lock:
                        for sym in removed:
                            mexc_futures_order_books.pop(sym, None)
                            mexc_futures_density_timestamps.pop(sym, None)
                    log_func(f"🗑️ mexc futures удалены: {', '.join(sorted(removed))}")

                if removed or added:
                    mexc_futures_reconnect_event.set()
                    log_func(f"🔄 mexc futures: список изменился (+{len(added)} -{len(removed)}), переподключение")
                else:
                    log_func(f"✅ mexc futures: список не изменился ({len(new_active)} монет)")
            else:
                removed = old_symbols - set(new_active)
                added = set(new_active) - old_symbols
                mexc_spot_symbols = new_active
                if removed:
                    async with mexc_spot_lock:
                        for sym in removed:
                            mexc_spot_order_books.pop(sym, None)
                            mexc_spot_density_timestamps.pop(sym, None)
                    log_func(f"🗑️ mexc spot удалены: {', '.join(sorted(removed))}")

                if removed or added:
                    mexc_spot_reconnect_event.set()
                    log_func(f"🔄 mexc spot: список изменился (+{len(added)} -{len(removed)}), переподключение")
                else:
                    log_func(f"✅ mexc spot: список не изменился ({len(new_active)} монет)")

        except Exception as e:
            log_func(f"❌ Ошибка в periodic_refresh(mexc {market}): {e}")


# ==========================================
# ГЛАВНАЯ ФУНКЦИЯ
# ==========================================
async def main_async(log_func=print):
    global mexc_futures_symbols, mexc_spot_symbols

    log_func("🚀 Запуск MEXC Async Monitor...")

    futures_candidates = await get_top_symbols_async('swap', log_func)
    spot_candidates = await get_top_symbols_async('spot', log_func)

    active_futures = []
    active_spot = []

    for symbol in futures_candidates[:30]:
        saved_count = await init_order_book_async(symbol, 'futures', log_func)
        if saved_count > 0:
            active_futures.append(symbol)
            log_func(f"✅ mexc futures {symbol}: принят (плотностей: {saved_count})")

    for symbol in spot_candidates[:30]:
        saved_count = await init_order_book_async(symbol, 'spot', log_func)
        if saved_count > 0:
            active_spot.append(symbol)
            log_func(f"✅ mexc spot {symbol}: принят (плотностей: {saved_count})")

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
    ]

    await asyncio.gather(*tasks)


def start_mexc_async_monitor(log_func=print):
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