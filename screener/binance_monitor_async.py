"""
Binance Monitor ASYNC — ОПТИМИЗИРОВАННАЯ ВЕРСИЯ
Изменения: SYNC_INTERVAL=10, TARGET=15, cache.set вне lock.
"""
import asyncio
import json
import time
import websockets
from django.core.cache import cache
import ccxt
from . import coin_selection

# ==========================================
# ГЛОБАЛЬНЫЕ ЭКЗЕМПЛЯРЫ CCXT (ОДИН на всё)
# ==========================================
ccxt_futures_exchange = ccxt.binance({
    'enableRateLimit': True,
    'timeout': 10000,
    'options': {'defaultType': 'future'}
})
ccxt_spot_exchange = ccxt.binance({
    'enableRateLimit': True,
    'timeout': 10000,
    'options': {'defaultType': 'spot'}
})

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — FUTURES
# ==========================================
binance_futures_order_books = {}
binance_futures_density_timestamps = {}
futures_symbols = []
binance_futures_message_queue = asyncio.Queue(maxsize=10000)
binance_futures_lock = asyncio.Lock()
binance_futures_reconnect_event = asyncio.Event()

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — SPOT
# ==========================================
binance_spot_order_books = {}
binance_spot_density_timestamps = {}
spot_symbols = []
binance_spot_message_queue = asyncio.Queue(maxsize=10000)
binance_spot_lock = asyncio.Lock()
binance_spot_reconnect_event = asyncio.Event()

FUTURES_WS_URL = "wss://fstream.binance.com/ws"
SPOT_WS_URL = "wss://stream.binance.com:9443/ws"

last_sync_time = {}

MIN_AGE_SECONDS = 180
CACHE_TTL = 30
# ✅ ОПТИМИЗАЦИЯ: SYNC_INTERVAL увеличен с 3 до 10
SYNC_INTERVAL = 10

binance_futures_volume_stats = {}
binance_spot_volume_stats = {}


# ==========================================
# ТОП МОНЕТ
# ==========================================
# ✅ ОПТИМИЗАЦИЯ: TARGET уменьшен с 30 до 15
TARGET_SYMBOLS = 15

def _fetch_top_symbols_sync(market='futures'):
    try:
        exchange = ccxt_futures_exchange if market == 'futures' else ccxt_spot_exchange
        tickers = exchange.fetch_tickers()
        clean_fn = coin_selection.clean_swap if market == 'futures' else coin_selection.clean_spot
        coin_selection.update_volume_history(tickers, clean_fn)
        candidates = coin_selection.select_candidates(
            tickers, clean_fn, limit=60,
            log_func=lambda msg: print(msg)
        )
        return candidates[:30]
    except Exception as e:
        print(f"❌ Ошибка fetch_top_symbols(binance {market}): {e}")
        return []


async def get_top_symbols_async(market='futures'):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_top_symbols_sync, market)


# ==========================================
# БЕЛЫЙ СПИСОК
# ==========================================
STABLE_COINS_LIMIT = 10
stable_futures_symbols = []
stable_spot_symbols = []


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
            if '-' in clean_symbol:
                continue
            if len(clean_symbol) < 2 or len(clean_symbol) > 15:
                continue
            if not clean_symbol.replace('_', '').isalnum():
                continue
            coins_with_volume.append((clean_symbol, volume))
        coins_with_volume.sort(key=lambda x: x[1], reverse=True)
        return [s for s, v in coins_with_volume[:limit]]
    except Exception as e:
        print(f"❌ Ошибка _fetch_stable_coins({market}): {e}")
        return []


async def get_stable_coins_async(market='futures', limit=10):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_stable_coins_sync, market, limit)


# ==========================================
# ИНИЦИАЛИЗАЦИЯ СТАКАНА
# ==========================================
def _init_order_book_sync(symbol, market):
    try:
        exchange = ccxt_futures_exchange if market == 'futures' else ccxt_spot_exchange
        if market == 'futures':
            ccxt_symbol = f"{symbol}/USDT:USDT"
        else:
            ccxt_symbol = f"{symbol}/USDT"
        # ✅ ОРИГИНАЛ: limit=500
        ob = exchange.fetch_order_book(ccxt_symbol, limit=500)
        bids = {}
        for price, qty in ob.get('bids', []):
            if price > 0 and qty > 0:
                bids[price] = qty
        asks = {}
        for price, qty in ob.get('asks', []):
            if price > 0 and qty > 0:
                asks[price] = qty
        return bids, asks
    except Exception as e:
        print(f"❌ _init_order_book_sync({symbol}): {e}")
        return {}, {}


async def init_order_book_async(symbol, market='futures', log_func=print):
    try:
        loop = asyncio.get_running_loop()
        bids, asks = await loop.run_in_executor(None, _init_order_book_sync, symbol, market)
        if not bids and not asks:
            log_func(f"⚠️ binance {market} {symbol}: пустой стакан")
            return 0
        if market == 'futures':
            async with binance_futures_lock:
                binance_futures_order_books[symbol] = {'bids': bids, 'asks': asks}
                binance_futures_density_timestamps[symbol] = {}
        else:
            async with binance_spot_lock:
                binance_spot_order_books[symbol] = {'bids': bids, 'asks': asks}
                binance_spot_density_timestamps[symbol] = {}
        saved_count = await sync_to_cache_async(symbol, market, log_func)
        log_func(f"✅ binance {market} Стакан {symbol}: {len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}")
        return saved_count
    except Exception as e:
        log_func(f"❌ init_order_book_async(binance {market} {symbol}): {e}")
        return 0


# ==========================================
# СИНХРОНИЗАЦИЯ В REDIS
# ✅ ОПТИМИЗАЦИЯ: cache.set вынесен за пределы lock
# ==========================================
async def sync_to_cache_async(symbol, market='futures', log_func=print):
    try:
        # ШАГ 1: Быстро собрать данные под lock
        if market == 'futures':
            async with binance_futures_lock:
                book = binance_futures_order_books.get(symbol, {})
                ts = dict(binance_futures_density_timestamps.get(symbol, {}))
                stats = dict(binance_futures_volume_stats.get(symbol, {}))
                if not book:
                    return 0
                key = f"scalp:futures:binance:{symbol}"
        else:
            async with binance_spot_lock:
                book = binance_spot_order_books.get(symbol, {})
                ts = dict(binance_spot_density_timestamps.get(symbol, {}))
                stats = dict(binance_spot_volume_stats.get(symbol, {}))
                if not book:
                    return 0
                key = f"scalp:spot:binance:{symbol}"

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
                    'exchange': 'binance'
                })

        # ШАГ 2: Lock уже отпущен — записываем в Redis
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, cache.set, key, densities, CACHE_TTL)
        except RuntimeError:
            pass

        # ШАГ 3: Короткий lock для обновления timestamps
        if market == 'futures':
            async with binance_futures_lock:
                binance_futures_density_timestamps[symbol] = ts
                binance_futures_volume_stats[symbol] = new_stats
        else:
            async with binance_spot_lock:
                binance_spot_density_timestamps[symbol] = ts
                binance_spot_volume_stats[symbol] = new_stats

        return len(densities)
    except Exception as e:
        log_func(f"❌ sync_to_cache_async(binance {market} {symbol}): {e}")
        return 0


# ==========================================
# WEBSOCKET LISTENER
# ==========================================
async def ws_listener(market='futures', log_func=print):
    global futures_symbols, spot_symbols
    reconnect_event = binance_futures_reconnect_event if market == 'futures' else binance_spot_reconnect_event
    ws_url = FUTURES_WS_URL if market == 'futures' else SPOT_WS_URL

    while True:
        try:
            symbols = futures_symbols if market == 'futures' else spot_symbols
            if not symbols:
                await asyncio.sleep(5)
                continue
            log_func(f"🔌 binance {market} WS подключение: {len(symbols)} символов")
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                try:
                    # ✅ ОРИГИНАЛ: одна строка для futures И spot
                    streams = [f"{s.lower()}usdt@depth@100ms" for s in symbols]
                    subscribe_msg = {"method": "SUBSCRIBE", "params": streams, "id": 1}
                    await ws.send(json.dumps(subscribe_msg))
                    log_func(f"✅ binance {market} WS подписан на {len(symbols)} символов")
                    while True:
                        if reconnect_event.is_set():
                            reconnect_event.clear()
                            log_func(f"🔄 binance {market}: сигнал переподключения получен")
                            break
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        queue = binance_futures_message_queue if market == 'futures' else binance_spot_message_queue
                        try:
                            queue.put_nowait(message)
                        except asyncio.QueueFull:
                            pass
                except websockets.exceptions.ConnectionClosed:
                    raise
                except Exception as e:
                    log_func(f"❌ binance {market} WS внутренняя ошибка: {e}")
                    raise
        except websockets.exceptions.ConnectionClosed:
            log_func(f"⚠️ binance {market} WS закрыт, переподключение через 3 сек")
            await asyncio.sleep(3)
        except Exception as e:
            log_func(f"❌ binance {market} WS ошибка: {e}, переподключение через 5 сек")
            await asyncio.sleep(5)


# ==========================================
# ОБРАБОТКА ОЧЕРЕДИ
# ==========================================
async def process_queue(market='futures', log_func=print):
    queue = binance_futures_message_queue if market == 'futures' else binance_spot_message_queue
    while True:
        try:
            message = await queue.get()
            data = json.loads(message)
            if 'data' in data:
                stream_data = data['data']
                symbol = stream_data.get('s', '')
                if symbol.endswith('USDT'):
                    symbol = symbol[:-4]
                bids = stream_data.get('b', [])
                asks = stream_data.get('a', [])
            elif 's' in data:
                symbol = data.get('s', '')
                if symbol.endswith('USDT'):
                    symbol = symbol[:-4]
                bids = data.get('b', [])
                asks = data.get('a', [])
            else:
                continue
            if bids or asks:
                await handle_update_async(symbol, bids, asks, market, log_func)
        except Exception as e:
            log_func(f"❌ binance {market} process_queue ошибка: {e}")


async def handle_update_async(symbol, bids_delta, asks_delta, market, log_func):
    if market == 'futures':
        async with binance_futures_lock:
            if symbol not in binance_futures_order_books:
                return
            book = binance_futures_order_books[symbol]
            ts = binance_futures_density_timestamps.get(symbol, {})
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
                binance_futures_density_timestamps[symbol] = ts
    else:
        async with binance_spot_lock:
            if symbol not in binance_spot_order_books:
                return
            book = binance_spot_order_books[symbol]
            ts = binance_spot_density_timestamps.get(symbol, {})
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
                binance_spot_density_timestamps[symbol] = ts

    if changed:
        key = f"binance:{market}:{symbol}"
        now = time.time()
        if now - last_sync_time.get(key, 0.0) >= SYNC_INTERVAL:
            await sync_to_cache_async(symbol, market, log_func)
            last_sync_time[key] = now


# ==========================================
# ПЕРИОДИЧЕСКАЯ РОТАЦИЯ
# ==========================================
async def periodic_refresh(log_func=print):
    global futures_symbols, spot_symbols
    while True:
        await asyncio.sleep(300)
        try:
            # --- Futures ротация ---
            candidates_f = await get_top_symbols_async('futures')
            old_symbols = set(futures_symbols)
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
            added = set(new_active) - old_symbols
            futures_symbols = new_active

            if removed:
                async with binance_futures_lock:
                    for sym in removed:
                        binance_futures_order_books.pop(sym, None)
                        binance_futures_density_timestamps.pop(sym, None)
                        binance_futures_volume_stats.pop(sym, None)
                        last_sync_time.pop(f"binance:futures:{sym}", None)
                log_func(f"🗑️ binance futures удалены: {', '.join(sorted(removed))}")
            if removed or added:
                binance_futures_reconnect_event.set()

            # --- Spot ротация ---
            candidates_s = await get_top_symbols_async('spot')
            old_symbols = set(spot_symbols)
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
            added = set(new_active) - old_symbols
            spot_symbols = new_active

            if removed:
                async with binance_spot_lock:
                    for sym in removed:
                        binance_spot_order_books.pop(sym, None)
                        binance_spot_density_timestamps.pop(sym, None)
                        binance_spot_volume_stats.pop(sym, None)
                        last_sync_time.pop(f"binance:spot:{sym}", None)
                log_func(f"🗑️ binance spot удалены: {', '.join(sorted(removed))}")
            if removed or added:
                binance_spot_reconnect_event.set()

            log_func(f"✅ binance ротация: futures={len(futures_symbols)}, spot={len(spot_symbols)}")
        except Exception as e:
            log_func(f"❌ Ошибка в periodic_refresh(binance): {e}")


# ==========================================
# ГЛАВНАЯ ФУНКЦИЯ
# ==========================================
async def main_async(log_func=print):
    global futures_symbols, spot_symbols, stable_futures_symbols, stable_spot_symbols
    log_func("🚀 Запуск Binance Async Monitor...")

    stable_f = await get_stable_coins_async('futures', STABLE_COINS_LIMIT)
    stable_s = await get_stable_coins_async('spot', STABLE_COINS_LIMIT)
    stable_futures_symbols = stable_f
    stable_spot_symbols = stable_s
    log_func(f"🔒 Белый список futures: {stable_f}")
    log_func(f"🔒 Белый список spot: {stable_s}")

    futures_candidates = await get_top_symbols_async('futures')
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

    futures_symbols = active_futures
    spot_symbols = active_spot
    log_func(f"✅ Binance Monitor: {len(active_futures)} futures, {len(active_spot)} spot")

    tasks = [
        ws_listener('futures', log_func),
        ws_listener('spot', log_func),
        process_queue('futures', log_func),
        process_queue('spot', log_func),
        periodic_refresh(log_func),
    ]
    await asyncio.gather(*tasks)


def start_binance_async_monitor(log_func=print):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main_async(log_func))
    except Exception as e:
        log_func(f"❌ Binance Async Monitor упал: {e}")
        import traceback
        log_func(traceback.format_exc())
