"""
Bitget Monitor ASYNC — ОПТИМИЗИРОВАННАЯ ВЕРСИЯ
Изменения: SYNC_INTERVAL=10, TARGET=15, cache.set вне lock.
"""
import asyncio
import json
import time
import aiohttp
import websockets
from django.core.cache import cache
import ccxt
from . import coin_selection

ccxt_futures_exchange = ccxt.bitget({
    'enableRateLimit': True, 'timeout': 10000,
    'options': {'defaultType': 'swap'}
})
ccxt_spot_exchange = ccxt.bitget({
    'enableRateLimit': True, 'timeout': 10000,
    'options': {'defaultType': 'spot'}
})

bitget_futures_order_books = {}
bitget_futures_density_timestamps = {}
bitget_futures_symbols = []
bitget_futures_message_queue = asyncio.Queue(maxsize=10000)
bitget_futures_lock = asyncio.Lock()
bitget_futures_reconnect_event = asyncio.Event()
bitget_futures_volume_stats = {}

bitget_spot_order_books = {}
bitget_spot_density_timestamps = {}
bitget_spot_symbols = []
bitget_spot_message_queue = asyncio.Queue(maxsize=10000)
bitget_spot_lock = asyncio.Lock()
bitget_spot_reconnect_event = asyncio.Event()
bitget_spot_volume_stats = {}

BITGET_WS_URL = "wss://ws.bitget.com/v2/ws/public"
BITGET_FUTURES_REST_URL = "https://api.bitget.com/api/v2/mix/market/merge-depth?symbol={}USDT&productType=USDT-FUTURES&limit=100"
BITGET_SPOT_REST_URL = "https://api.bitget.com/api/v2/spot/market/merge-depth?symbol={}USDT&limit=100"

last_sync_time = {}
MIN_AGE_SECONDS = 180
CACHE_TTL = 30
SYNC_INTERVAL = 10
TARGET_SYMBOLS = 15
STABLE_COINS_LIMIT = 10
stable_futures_symbols = []
stable_spot_symbols = []

_http_client = None

async def get_http_client():
    global _http_client
    if _http_client is None:
        _http_client = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={'User-Agent': 'Mozilla/5.0'}
        )
    return _http_client

def _fetch_top_symbols_sync(market_type='swap'):
    try:
        exchange = ccxt_futures_exchange if market_type == 'swap' else ccxt_spot_exchange
        tickers = exchange.fetch_tickers()
        clean_fn = coin_selection.clean_swap if market_type == 'swap' else coin_selection.clean_spot
        coin_selection.update_volume_history(tickers, clean_fn)
        candidates = coin_selection.select_candidates(tickers, clean_fn, limit=60, log_func=lambda msg: print(msg))
        return candidates[:30]
    except Exception as e:
        print(f"❌ Ошибка fetch_top_symbols(bitget {market_type}): {e}")
        return []

async def get_top_symbols_async(market_type='swap'):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_top_symbols_sync, market_type)

def _fetch_stable_coins_sync(market='futures', limit=10):
    try:
        exchange = ccxt_futures_exchange if market == 'futures' else ccxt_spot_exchange
        tickers = exchange.fetch_tickers()
        coins_with_volume = []
        for symbol, data in tickers.items():
            if market == 'futures':
                if ':USDT' not in symbol:
                    continue
            else:
                if '/USDT' not in symbol:
                    continue
            volume = data.get('quoteVolume') or 0
            if volume < 100000:
                continue
            clean_symbol = symbol.replace('/USDT', '').replace(':USDT', '')
            if '-' in clean_symbol or len(clean_symbol) < 2 or len(clean_symbol) > 15:
                continue
            if not clean_symbol.replace('_', '').isalnum():
                continue
            coins_with_volume.append((clean_symbol, volume))
        coins_with_volume.sort(key=lambda x: x[1], reverse=True)
        return [s for s, v in coins_with_volume[:limit]]
    except Exception as e:
        print(f"❌ Ошибка _fetch_stable_coins(bitget {market}): {e}")
        return []

async def get_stable_coins_async(market='futures', limit=10):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_stable_coins_sync, market, limit)

async def init_order_book_async(symbol, market='futures', log_func=print):
    try:
        url = (BITGET_FUTURES_REST_URL if market == 'futures' else BITGET_SPOT_REST_URL).format(symbol)
        client = await get_http_client()
        async with client.get(url) as resp:
            if resp.status != 200:
                return 0
            data = await resp.json()
        if data.get('code') != '00000':
            return 0
        result = data.get('data') or {}
        raw_bids = result.get('bids') or []
        raw_asks = result.get('asks') or []
        bids, asks = {}, {}
        for row in raw_bids:
            try:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    p, q = float(row[0]), float(row[1])
                    if p > 0 and q > 0:
                        bids[p] = q
            except Exception:
                continue
        for row in raw_asks:
            try:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    p, q = float(row[0]), float(row[1])
                    if p > 0 and q > 0:
                        asks[p] = q
            except Exception:
                continue
        if not bids and not asks:
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
        log_func(f"✅ bitget {market} {symbol}: {len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}")
        return saved_count
    except Exception as e:
        log_func(f"❌ init_order_book_async(bitget {market} {symbol}): {e}")
        return 0

async def sync_to_cache_async(symbol, market='futures', log_func=print):
    try:
        if market == 'futures':
            async with bitget_futures_lock:
                book = bitget_futures_order_books.get(symbol, {})
                ts = dict(bitget_futures_density_timestamps.get(symbol, {}))
                stats = dict(bitget_futures_volume_stats.get(symbol, {}))
                if not book:
                    return 0
        else:
            async with bitget_spot_lock:
                book = bitget_spot_order_books.get(symbol, {})
                ts = dict(bitget_spot_density_timestamps.get(symbol, {}))
                stats = dict(bitget_spot_volume_stats.get(symbol, {}))
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
                    'price': price, 'volume': volume, 'side': side_name,
                    'timestamp': ts[price], 'exchange': 'bitget'
                })
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

async def ws_heartbeat(ws, market='futures', log_func=print):
    try:
        while True:
            await asyncio.sleep(25)
            try:
                await ws.send("ping")
            except Exception:
                return
    except asyncio.CancelledError:
        return

async def ws_listener(market='futures', log_func=print):
    global bitget_futures_symbols, bitget_spot_symbols
    reconnect_event = bitget_futures_reconnect_event if market == 'futures' else bitget_spot_reconnect_event
    while True:
        try:
            symbols = bitget_futures_symbols if market == 'futures' else bitget_spot_symbols
            if not symbols:
                await asyncio.sleep(5)
                continue
            log_func(f"🔌 bitget {market} WS: {len(symbols)} символов")
            async with websockets.connect(BITGET_WS_URL, ping_interval=None, ping_timeout=None) as ws:
                heartbeat_task = asyncio.create_task(ws_heartbeat(ws, market, log_func))
                try:
                    inst_type = "USDT-FUTURES" if market == 'futures' else "SPOT"
                    args = [{"instId": f"{s}USDT", "channel": "books15", "instType": inst_type} for s in symbols]
                    await ws.send(json.dumps({"op": "subscribe", "args": args}))
                    log_func(f"✅ bitget {market} WS подписан на {len(symbols)} символов")
                    while True:
                        if reconnect_event.is_set():
                            reconnect_event.clear()
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
            log_func(f"⚠️ bitget {market} WS закрыт, reconnect 3 сек")
            await asyncio.sleep(3)
        except Exception as e:
            log_func(f"❌ bitget {market} WS ошибка: {e}, reconnect 5 сек")
            await asyncio.sleep(5)

async def process_queue(market='futures', log_func=print):
    queue = bitget_futures_message_queue if market == 'futures' else bitget_spot_message_queue
    while True:
        try:
            message = await queue.get()
            data = json.loads(message)
            if data.get('arg', {}).get('channel') != 'books15':
                continue
            arg = data.get('arg', {})
            inst_id = arg.get('instId', '')
            symbol = inst_id.replace('USDT', '') if inst_id.endswith('USDT') else inst_id
            action = data.get('action', '')
            raw_data = data.get('data', [])
            if not raw_data:
                continue
            for book_data in raw_data:
                raw_bids = book_data.get('bids', [])
                raw_asks = book_data.get('asks', [])
                if action == 'snapshot':
                    await handle_snapshot_async(symbol, raw_bids, raw_asks, market, log_func)
                elif (raw_bids or raw_asks):
                    await handle_update_async(symbol, raw_bids, raw_asks, market, log_func)
        except Exception as e:
            log_func(f"❌ bitget {market} process_queue ошибка: {e}")

async def handle_snapshot_async(symbol, raw_bids, raw_asks, market, log_func):
    now = time.time()
    new_bids, new_asks = {}, {}
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
            new_ts, new_stats = {}, {}
            for p in list(new_bids.keys()) + list(new_asks.keys()):
                if p in old_ts:
                    new_ts[p] = old_ts[p]
                else:
                    new_ts[p] = now - MIN_AGE_SECONDS
                if p in old_stats:
                    new_stats[p] = old_stats[p]
            bitget_futures_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            bitget_futures_density_timestamps[symbol] = new_ts
            bitget_futures_volume_stats[symbol] = new_stats
    else:
        async with bitget_spot_lock:
            old_ts = bitget_spot_density_timestamps.get(symbol, {})
            old_stats = bitget_spot_volume_stats.get(symbol, {})
            new_ts, new_stats = {}, {}
            for p in list(new_bids.keys()) + list(new_asks.keys()):
                if p in old_ts:
                    new_ts[p] = old_ts[p]
                else:
                    new_ts[p] = now - MIN_AGE_SECONDS
                if p in old_stats:
                    new_stats[p] = old_stats[p]
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
                bitget_spot_density_timestamps[symbol] = ts
    if changed:
        key = f"bitget:{market}:{symbol}"
        now = time.time()
        if now - last_sync_time.get(key, 0.0) >= SYNC_INTERVAL:
            await sync_to_cache_async(symbol, market, log_func)
            last_sync_time[key] = now

async def periodic_refresh(log_func=print):
    global bitget_futures_symbols, bitget_spot_symbols
    while True:
        await asyncio.sleep(300)
        try:
            candidates_f = await get_top_symbols_async('swap')
            old_symbols = set(bitget_futures_symbols)
            new_active = []
            for symbol in stable_futures_symbols:
                if symbol in old_symbols:
                    new_active.append(symbol)
                else:
                    saved_count = await init_order_book_async(symbol, 'futures', log_func)
                    if saved_count > 0:
                        new_active.append(symbol)
            for symbol in candidates_f:
                if len(new_active) >= TARGET_SYMBOLS:
                    break
                if symbol in new_active:
                    continue
                if symbol in old_symbols:
                    new_active.append(symbol)
                    continue
                saved_count = await init_order_book_async(symbol, 'futures', log_func)
                if saved_count > 0:
                    new_active.append(symbol)
            removed = old_symbols - set(new_active)
            bitget_futures_symbols = new_active
            if removed:
                async with bitget_futures_lock:
                    for sym in removed:
                        bitget_futures_order_books.pop(sym, None)
                        bitget_futures_density_timestamps.pop(sym, None)
                        bitget_futures_volume_stats.pop(sym, None)
                        last_sync_time.pop(f"bitget:futures:{sym}", None)
            if removed:
                bitget_futures_reconnect_event.set()

            candidates_s = await get_top_symbols_async('spot')
            old_symbols = set(bitget_spot_symbols)
            new_active = []
            for symbol in stable_spot_symbols:
                if symbol in old_symbols:
                    new_active.append(symbol)
                else:
                    saved_count = await init_order_book_async(symbol, 'spot', log_func)
                    if saved_count > 0:
                        new_active.append(symbol)
            for symbol in candidates_s:
                if len(new_active) >= TARGET_SYMBOLS:
                    break
                if symbol in new_active:
                    continue
                if symbol in old_symbols:
                    new_active.append(symbol)
                    continue
                saved_count = await init_order_book_async(symbol, 'spot', log_func)
                if saved_count > 0:
                    new_active.append(symbol)
            removed = old_symbols - set(new_active)
            bitget_spot_symbols = new_active
            if removed:
                async with bitget_spot_lock:
                    for sym in removed:
                        bitget_spot_order_books.pop(sym, None)
                        bitget_spot_density_timestamps.pop(sym, None)
                        bitget_spot_volume_stats.pop(sym, None)
                        last_sync_time.pop(f"bitget:spot:{sym}", None)
            if removed:
                bitget_spot_reconnect_event.set()
            log_func(f"✅ bitget ротация: futures={len(bitget_futures_symbols)}, spot={len(bitget_spot_symbols)}")
        except Exception as e:
            log_func(f"❌ Ошибка periodic_refresh(bitget): {e}")

async def main_async(log_func=print):
    global bitget_futures_symbols, bitget_spot_symbols, stable_futures_symbols, stable_spot_symbols
    log_func("🚀 Запуск Bitget Async Monitor...")
    stable_f = await get_stable_coins_async('futures', STABLE_COINS_LIMIT)
    stable_s = await get_stable_coins_async('spot', STABLE_COINS_LIMIT)
    stable_futures_symbols = stable_f
    stable_spot_symbols = stable_s
    futures_candidates = await get_top_symbols_async('swap')
    spot_candidates = await get_top_symbols_async('spot')
    active_futures = []
    for symbol in stable_f:
        saved_count = await init_order_book_async(symbol, 'futures', log_func)
        if saved_count > 0:
            active_futures.append(symbol)
    for symbol in futures_candidates[:TARGET_SYMBOLS]:
        if symbol in active_futures:
            continue
        saved_count = await init_order_book_async(symbol, 'futures', log_func)
        if saved_count > 0:
            active_futures.append(symbol)
    active_spot = []
    for symbol in stable_s:
        saved_count = await init_order_book_async(symbol, 'spot', log_func)
        if saved_count > 0:
            active_spot.append(symbol)
    for symbol in spot_candidates[:TARGET_SYMBOLS]:
        if symbol in active_spot:
            continue
        saved_count = await init_order_book_async(symbol, 'spot', log_func)
        if saved_count > 0:
            active_spot.append(symbol)
    bitget_futures_symbols = active_futures
    bitget_spot_symbols = active_spot
    log_func(f"✅ Bitget Monitor: {len(active_futures)} futures, {len(active_spot)} spot")
    tasks = [
        ws_listener('futures', log_func),
        ws_listener('spot', log_func),
        process_queue('futures', log_func),
        process_queue('spot', log_func),
        periodic_refresh(log_func),
    ]
    await asyncio.gather(*tasks)

def start_bitget_async_monitor(log_func=print):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main_async(log_func))
    except Exception as e:
        log_func(f"❌ Bitget Async Monitor упал: {e}")
        import traceback
        log_func(traceback.format_exc())
