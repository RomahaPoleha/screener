"""
Binance Alpha Monitor ASYNC — WORKER NODE
Читает список монет из Redis (мастер-список от Binance) и мониторит стакан Alpha Trading
"""
import asyncio
import json
import time
import aiohttp
import websockets
from django.core.cache import cache
from .density_config import DENSITY_MIN_VOLUME_ALPHA

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — BINANCE ALPHA
# ==========================================
alpha_order_books = {}
alpha_density_timestamps = {}
alpha_symbols = []
alpha_message_queue = asyncio.Queue(maxsize=10000)
alpha_lock = asyncio.Lock()
alpha_reconnect_event = asyncio.Event()
alpha_volume_stats = {}

# Защита от мгновенного созревания при очистке стакана спуфером
alpha_first_load_done = set()

alpha_removed = {}
alpha_mid_history = {}

# URLs Binance Alpha
ALPHA_WS_URL = "wss://nbstream.binance.com/w3w/wsa/stream"
ALPHA_REST_URL = "https://www.binance.com/bapi/defi/v1/public/alpha-trade/fullDepth?symbol={}&limit=500"

# Rate limiting и кэш
last_sync_time = {}
MIN_AGE_SECONDS = 180
CACHE_TTL = 30
SYNC_INTERVAL = 3
_http_client = None

# Ключ кэша для Alpha
ALPHA_CACHE_MARKET = 'spot'
ALPHA_CACHE_EXCHANGE = 'binance_alpha'

# ==========================================
# MEMORY LEAK PREVENTION CONSTANTS
# ==========================================
MAX_STATS_PER_SYMBOL = 200      # макс. ценовых уровней на символ
MAX_STATS_AGE_SEC = 7200        # 2 часа — макс. возраст записи в volume_stats
MAX_TS_AGE_SEC = 3600           # 1 час — макс. возраст записи в density_timestamps

MOVING_REL_EPSILON = 0.001
MOVING_MID_THRESHOLD = 0.002
MOVING_WINDOW_SEC = 60
RECENT_WINDOW = 60
TOUCH_PULL_DISTANCE = 0.005

# ==========================================


async def get_http_client():
    """Ленивая инициализация aiohttp клиента"""
    global _http_client
    if _http_client is None or _http_client.closed:
        _http_client = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(connect=5, sock_read=10, total=30),
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
    return _http_client


async def close_http_client():
    """Явное закрытие сессии (для graceful shutdown)"""
    global _http_client
    if _http_client and not _http_client.closed:
        await _http_client.close()
        _http_client = None


# ==========================================
# 🔥 ЧТЕНИЕ МАСТЕР-СПИСКА (пересечение Binance master + alpha_map)
# ==========================================
async def _get_alpha_map_async():
    """Асинхронное чтение alpha_map из Redis."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, cache.get, 'exchange:alpha:map')


async def _get_master_symbols_sync():
    """Синхронное чтение мастер-листов SPOT + FUTURES из Redis."""
    try:
        spot_symbols = cache.get('scalp:master:spot') or []
        futures_symbols = cache.get('scalp:master:futures') or []

        if not isinstance(spot_symbols, list):
            spot_symbols = []
        if not isinstance(futures_symbols, list):
            futures_symbols = []

        all_symbols = list(set(spot_symbols) | set(futures_symbols))
        return all_symbols
    except Exception as e:
        print(f"❌ Ошибка чтения master-списка для alpha: {e}")
        import traceback
        print(traceback.format_exc())
        return []


async def get_master_symbols_async():
    """Асинхронная обёртка для чтения мастер-списка."""
    return await _get_master_symbols_sync()


async def build_alpha_symbol_list():
    """
    Строит итоговый список символов для мониторинга:
    пересечение Binance мастер-листа и alpha_map.
    Возвращает список базовых символов (BTCUSDT, ETHUSDT, ...).
    """
    master_symbols = await _get_master_symbols_sync()
    if not master_symbols:
        print("⚠️ alpha: оба мастер-листа пусты")
        return []

    alpha_map = await _get_alpha_map_async()
    if not alpha_map:
        print("⚠️ alpha: exchange:alpha:map пуст")
        return []

    filtered = []
    for symbol in master_symbols:
        base = symbol.upper().replace('USDT', '')
        if base in alpha_map:
            filtered.append(symbol)

    print(f"🔍 alpha: master={len(master_symbols)}, alpha_map={len(alpha_map)}, после пересечения={len(filtered)}: {filtered[:10]}")
    return filtered


# ==========================================
# ИНИЦИАЛИЗАЦИЯ СТАКАНОВ (async HTTP)
# ==========================================
async def init_order_book_async(symbol, alpha_map, log_func=print):
    """Инициализация стакана Alpha через async HTTP.
    symbol — БАЗОВЫЙ символ (BTCUSDT, ETHUSDT, ...).
    alpha_map — актуальный маппинг base->ALPHA_XXXUSDT.
    """
    try:
        base_symbol = symbol.upper().replace('USDT', '')

        if not alpha_map or base_symbol not in alpha_map:
            return False, 0

        alpha_symbol = alpha_map[base_symbol]
        sym_rest = alpha_symbol.upper()
        url = ALPHA_REST_URL.format(sym_rest)

        client = await get_http_client()
        async with client.get(url) as resp:
            if resp.status != 200:
                log_func(f"⚠️ alpha {symbol}: HTTP {resp.status}")
                return False, 0
            data = await resp.json()

        if data.get('code') != "000000":
            log_func(f"⚠️ alpha {symbol}: code={data.get('code')} msg={data.get('message')}")
            return False, 0

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

        async with alpha_lock:
            alpha_order_books[symbol] = {'bids': bids, 'asks': asks}
            alpha_density_timestamps[symbol] = {}

        if not bids and not asks:
            pass

        saved_count = await sync_to_cache_async(symbol, log_func)
        return True, saved_count

    except Exception as e:
        log_func(f"❌ init_order_book_async(alpha {symbol}): {e}")
        import traceback
        log_func(traceback.format_exc())
        return False, 0


# ==========================================
# СИНХРОНИЗАЦИЯ В REDIS (async)
# ==========================================
# ==========================================
# АНТИ-СПУФИНГ ФИЛЬТРЫ (как в MEXC/Gate)
# ==========================================
SYMMETRY_EPSILON = 0.01
MAX_SYMMETRY_DISTANCE = 0.02
CLUSTER_MIN_LEVELS = 4
CLUSTER_MIN_STEP = 0.005
CLUSTER_MAX_STEP = 0.02
CLUSTER_STEP_TOL = 0.003
CLUSTER_MIN_VOLUME = 1000
LAYERING_MIN_LEVELS = 5
LAYERING_CV_THRESHOLD = 0.15
LAYERING_MAX_DISTANCE = 0.02


def _filter_symmetric_spoofs(densities, mid, log_func=print, symbol=None):
    if not densities or not mid:
        return densities
    tagged = [(i, d, (d['price'] - mid) / mid) for i, d in enumerate(densities)]
    buys = [t for t in tagged if t[1]['side'] == 'buy']
    sells = [t for t in tagged if t[1]['side'] == 'sell']
    drop = set()
    for bi, bd, brel in buys:
        if bi in drop:
            continue
        if abs(brel) > MAX_SYMMETRY_DISTANCE:
            continue
        cand = [(si, sd, srel) for si, sd, srel in sells
                if si not in drop and abs(srel) <= MAX_SYMMETRY_DISTANCE]
        if not cand:
            continue
        best = min(cand, key=lambda x: abs(abs(x[2]) - abs(brel)))
        si, sd, srel = best
        if abs(abs(srel) - abs(brel)) <= SYMMETRY_EPSILON:
            drop.add(bi)
            drop.add(si)
    return [d for i, d in enumerate(densities) if i not in drop]


def _detect_volume_clustering(densities, mid, log_func=print, symbol=None):
    if not densities or not mid or len(densities) < CLUSTER_MIN_LEVELS:
        return densities
    from collections import defaultdict
    groups = defaultdict(list)
    for idx, d in enumerate(densities):
        if d.get('volume', 0) < CLUSTER_MIN_VOLUME:
            continue
        key = (round(d['volume'], -3), d.get('side'))
        groups[key].append((idx, d))
    drop = set()
    for key, items in groups.items():
        if len(items) < CLUSTER_MIN_LEVELS:
            continue
        ordered = sorted(items, key=lambda x: x[1]['price'])
        run = [ordered[0]]
        steps = []
        for k in range(1, len(ordered)):
            prev_price = ordered[k - 1][1]['price']
            curr_price = ordered[k][1]['price']
            if not prev_price:
                if len(run) >= CLUSTER_MIN_LEVELS:
                    for idx, _ in run:
                        drop.add(idx)
                run = [ordered[k]]
                steps = []
                continue
            step = abs((curr_price - prev_price) / prev_price)
            if CLUSTER_MIN_STEP <= step <= CLUSTER_MAX_STEP and (not steps or abs(step - steps[-1]) <= max(CLUSTER_STEP_TOL, steps[-1] * 0.25)):
                run.append(ordered[k])
                steps.append(step)
            else:
                if len(run) >= CLUSTER_MIN_LEVELS:
                    for idx, _ in run:
                        drop.add(idx)
                run = [ordered[k]]
                steps = []
        if len(run) >= CLUSTER_MIN_LEVELS:
            for idx, _ in run:
                drop.add(idx)
    return [d for i, d in enumerate(densities) if i not in drop]


def _detect_layering(book, mid, log_func=print, symbol=None, market='futures'):
    suspicious = set()
    if not book or not mid:
        return suspicious
    from .density_config import DENSITY_MIN_VOLUME_ALPHA
    min_volume = DENSITY_MIN_VOLUME_ALPHA
    for side_key in ('bids', 'asks'):
        levels = sorted(book.get(side_key, {}).items(), key=lambda x: x[0])
        if len(levels) < LAYERING_MIN_LEVELS:
            continue
        prices = [p for p, _ in levels]
        volumes = [p * q for p, q in levels]
        n = len(levels)
        for i in range(n - LAYERING_MIN_LEVELS + 1):
            window_prices = prices[i:i + LAYERING_MIN_LEVELS]
            window_volumes = volumes[i:i + LAYERING_MIN_LEVELS]
            if any(v < min_volume for v in window_volumes):
                continue
            if any(abs((p - mid) / mid) > LAYERING_MAX_DISTANCE for p in window_prices):
                continue
            mean = sum(window_volumes) / len(window_volumes)
            if mean == 0:
                continue
            variance = sum((v - mean) ** 2 for v in window_volumes) / len(window_volumes)
            std = variance ** 0.5
            cv = std / mean
            if cv < LAYERING_CV_THRESHOLD:
                for p in window_prices:
                    suspicious.add(p)
    return suspicious


async def sync_to_cache_async(symbol, log_func=print):
    try:
        async with alpha_lock:
            book = alpha_order_books.get(symbol, {})
            ts = alpha_density_timestamps.get(symbol, {})
            stats = alpha_volume_stats.get(symbol, {})
            if not book:
                return 0

        key = f"scalp:{ALPHA_CACHE_MARKET}:{ALPHA_CACHE_EXCHANGE}:{symbol}"
        now = time.time()
        densities = []

        is_first_load = symbol not in alpha_first_load_done
        if is_first_load:
            alpha_first_load_done.add(symbol)

        new_stats = {}
        current_prices = set(book.get('bids', {}).keys()) | set(book.get('asks', {}).keys())

        # Текущая mid-цена
        bids_keys = list(book.get('bids', {}).keys())
        asks_keys = list(book.get('asks', {}).keys())
        mid = None
        if bids_keys and asks_keys:
            mid = (max(bids_keys) + min(asks_keys)) / 2

        mh = alpha_mid_history
        mhl = mh.setdefault(symbol, [])
        mhl.append((now, mid))
        if len(mhl) > 20:
            mhl.pop(0)

        old_mid = None
        for t, m in reversed(mhl):
            if now - t >= MOVING_WINDOW_SEC and m is not None:
                old_mid = m
                break
        mid_changed = (old_mid is not None and old_mid != 0 and mid is not None and mid != 0
                       and abs((mid - old_mid) / old_mid) >= MOVING_MID_THRESHOLD)

        removed_store = alpha_removed
        removed_list = [x for x in removed_store.get(symbol, []) if (now - x[1]) < (RECENT_WINDOW * 2)]
        removed_store[symbol] = removed_list
        recently_removed = list(removed_list)

        for side, side_name in [('bids', 'buy'), ('asks', 'sell')]:
            for price, qty in book.get(side, {}).items():
                volume = price * qty

                # Детекция гуляющей плотности
                is_moving = False
                rel = None
                if price not in stats and mid is not None and mid != 0 and mid_changed:
                    rel = (price - mid) / mid
                    for rp, rt, rrel in recently_removed:
                        if rrel is not None and abs(rrel - rel) < MOVING_REL_EPSILON:
                            is_moving = True
                            break
                if is_moving:
                    if price in ts:
                        ts.pop(price, None)
                    continue

                if rel is None and mid is not None and mid != 0:
                    rel = (price - mid) / mid

                # Touch-and-pull
                if any(rp == price and abs(rrel) <= TOUCH_PULL_DISTANCE for rp, rt, rrel in recently_removed):
                    if price in ts:
                        ts.pop(price, None)
                    continue

                if volume < DENSITY_MIN_VOLUME_ALPHA:
                    if price in ts:
                        ts.pop(price, None)
                    continue

                is_mature = (price in ts) and ((now - ts[price]) >= MIN_AGE_SECONDS)

                if not is_mature:
                    ts[price] = now
                    continue

                prev_stat = stats.get(price, {'min': volume, 'max': volume, 'sum': 0, 'count': 0})
                new_stat = {
                    'min': min(prev_stat['min'], volume),
                    'max': max(prev_stat['max'], volume),
                    'sum': prev_stat['sum'] + volume,
                    'count': prev_stat['count'] + 1
                }
                new_stats[price] = new_stat

                if new_stat['count'] >= 3:
                    avg = new_stat['sum'] / new_stat['count']
                    spread = new_stat['max'] - new_stat['min']
                    stability_ratio = spread / avg if avg > 0 else 0

                    if stability_ratio > 0.5:
                        ts[price] = now
                        new_stats[price] = {'min': volume, 'max': volume, 'sum': volume, 'count': 1}
                        continue

                age_seconds = int(now - ts[price])

                densities.append({
                    'price': price,
                    'volume': volume,
                    'side': side_name,
                    'timestamp': ts[price],
                    'age_seconds': age_seconds,
                    'exchange': 'binance_alpha'
                })

        # Детекция повторяющихся объёмов (volume clustering)
        densities = _detect_volume_clustering(densities, mid, log_func, symbol)

        # Детекция layering (анализ стакана)
        layering_prices = _detect_layering(book, mid, log_func, symbol)
        if layering_prices:
            before = len(densities)
            densities = [d for d in densities if d['price'] not in layering_prices]

        # Анти-спуф: отсекаем симметричные пары
        densities = _filter_symmetric_spoofs(densities, mid, log_func, symbol)

        prices_to_remove = [p for p in list(ts.keys()) if p not in current_prices]
        for p in prices_to_remove:
            ts.pop(p, None)
            new_stats.pop(p, None)

        # 🔥 MEMORY LEAK PREVENTION: Очистка старых записей volume_stats и density_timestamps
        # Ограничение volume_stats по количеству записей на символ
        if len(new_stats) > MAX_STATS_PER_SYMBOL:
            # Удаляем самые старые (по timestamp в ts)
            sorted_prices = sorted(new_stats.keys(), key=lambda p: ts.get(p, 0))
            for p in sorted_prices[:len(new_stats) - MAX_STATS_PER_SYMBOL]:
                new_stats.pop(p, None)

        # Ограничение volume_stats и density_timestamps по возрасту
        now = time.time()
        old_prices = [p for p, t in ts.items() if now - t > MAX_TS_AGE_SEC]
        for p in old_prices:
            ts.pop(p, None)
            new_stats.pop(p, None)

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, cache.set, key, densities, CACHE_TTL)
        except RuntimeError:
            pass

        async with alpha_lock:
            alpha_density_timestamps[symbol] = ts
            alpha_volume_stats[symbol] = new_stats

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_to_cache_async(alpha {symbol}): {e}")
        import traceback
        log_func(traceback.format_exc())
        return 0


# ==========================================
# HEARTBEAT (async)
# ==========================================
async def ws_heartbeat(ws, log_func=print):
    """Отправка PING каждые 20 секунд для Binance WS"""
    try:
        while True:
            await asyncio.sleep(20)
            try:
                await ws.send(json.dumps({"method": "PING", "id": int(time.time() * 1000)}))
            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                return
    except asyncio.CancelledError:
        return


# ==========================================
# WEBSOCKET LISTENER (async)
# ==========================================
async def ws_listener(alpha_map, log_func=print):
    """Бесконечный цикл подключения к WebSocket Binance Alpha"""
    global alpha_symbols

    while True:
        try:
            symbols = alpha_symbols
            if not symbols:
                await asyncio.sleep(5)
                continue

            if not alpha_map:
                await asyncio.sleep(5)
                continue

            async with websockets.connect(ALPHA_WS_URL, ping_interval=30, ping_timeout=10) as ws:
                heartbeat_task = asyncio.create_task(ws_heartbeat(ws, log_func))

                try:
                    args = []
                    for s in symbols:
                        base = s.upper().replace('USDT', '')
                        alpha_sym = alpha_map.get(base)
                        if alpha_sym:
                            args.append(f"{alpha_sym.lower()}@fulldepth@100ms")

                    if not args:
                        await asyncio.sleep(30)
                        continue

                    subscribe_msg = {
                        "method": "SUBSCRIBE",
                        "params": args,
                        "id": int(time.time() * 1000)
                    }
                    await ws.send(json.dumps(subscribe_msg))

                    while True:
                        if alpha_reconnect_event.is_set():
                            alpha_reconnect_event.clear()
                            break

                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue

                        try:
                            alpha_message_queue.put_nowait(message)
                        except asyncio.QueueFull:
                            pass

                finally:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        pass

        except websockets.exceptions.ConnectionClosed:
            await asyncio.sleep(3)
        except Exception as e:
            log_func(f"❌ alpha WS ошибка: {e}, переподключение через 5 сек")
            await asyncio.sleep(5)


# ==========================================
# ОБРАБОТКА ОЧЕРЕДИ (async)
# ==========================================
async def process_queue(alpha_map, log_func=print):
    """Обработка очереди сообщений Binance Alpha"""
    reverse_map = {v.upper(): k.upper() + 'USDT' for k, v in alpha_map.items()}

    while True:
        try:
            message = await alpha_message_queue.get()
            data = json.loads(message)

            if data.get('result') is not None or data.get('id') is not None:
                continue

            stream = data.get('stream', '')
            if not stream or '@fulldepth@' not in stream:
                continue

            sym_ws = stream.split('@')[0]
            alpha_symbol = sym_ws.upper()

            symbol = reverse_map.get(alpha_symbol)
            if not symbol:
                continue

            payload_data = data.get('data', {})
            raw_bids = payload_data.get('bids', [])
            raw_asks = payload_data.get('asks', [])

            await handle_snapshot_async(symbol, raw_bids, raw_asks, log_func)

        except Exception as e:
            log_func(f"❌ alpha process_queue ошибка: {e}")


async def handle_snapshot_async(symbol, raw_bids, raw_asks, log_func):
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

    async with alpha_lock:
        if symbol not in alpha_order_books:
            return

        old_ts = alpha_density_timestamps.get(symbol, {})
        old_stats = alpha_volume_stats.get(symbol, {})
        old_book = alpha_order_books.get(symbol, {})
        _ob = list(old_book.get('bids', {}).keys())
        _oa = list(old_book.get('asks', {}).keys())
        _omid = (max(_ob) + min(_oa)) / 2 if _ob and _oa else None

        new_ts = {p: old_ts[p] for p in new_bids if p in old_ts}
        new_ts.update({p: old_ts[p] for p in new_asks if p in old_ts})

        new_stats = {p: old_stats[p] for p in new_bids if p in old_stats}
        new_stats.update({p: old_stats[p] for p in new_asks if p in old_stats})

        if _omid is not None:
            for p in (set(old_ts) - set(new_ts)):
                alpha_removed.setdefault(symbol, []).append((p, time.time(), (p - _omid) / _omid))

        alpha_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
        alpha_density_timestamps[symbol] = new_ts
        alpha_volume_stats[symbol] = new_stats

    key = f"scalp:{ALPHA_CACHE_MARKET}:{ALPHA_CACHE_EXCHANGE}:{symbol}"
    now = time.time()
    if now - last_sync_time.get(key, 0.0) >= SYNC_INTERVAL:
        await sync_to_cache_async(symbol, log_func)
        last_sync_time[key] = now


# ==========================================
# 🔥 ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ
# ==========================================
async def periodic_refresh(log_func=print):
    """Периодическая синхронизация с мастер-списком Binance."""
    global alpha_symbols

    while True:
        await asyncio.sleep(300)

        try:
            master_symbols = await _get_master_symbols_sync()
            alpha_map = await _get_alpha_map_async()

            if not master_symbols or not alpha_map:
                log_func("⚠️ alpha: мастер-список или alpha_map пуст, пропускаем ротацию")
                continue

            new_filtered = []
            for symbol in master_symbols:
                base = symbol.upper().replace('USDT', '')
                if base in alpha_map:
                    new_filtered.append(symbol)

            old_symbols = set(alpha_symbols)
            new_active = []
            added = []
            removed = old_symbols - set(new_filtered)

            for symbol in new_filtered:
                if symbol not in old_symbols:
                    success, saved_count = await init_order_book_async(symbol, alpha_map, log_func)
                    if success:
                        new_active.append(symbol)
                        added.append(symbol)
                    else:
                        new_active.append(symbol)

            alpha_symbols = new_active

            if removed:
                async with alpha_lock:
                    for sym in removed:
                        alpha_order_books.pop(sym, None)
                        alpha_density_timestamps.pop(sym, None)
                        alpha_volume_stats.pop(sym, None)
                        last_sync_time.pop(f"scalp:spot:binance_alpha:{sym}", None)
                        alpha_first_load_done.discard(sym)

            if added or removed:
                alpha_reconnect_event.set()

        except Exception as e:
            log_func(f"❌ Ошибка в periodic_refresh(alpha): {e}")
            import traceback
            log_func(traceback.format_exc())


# ==========================================
# 🔥 ПРИНУДИТЕЛЬНЫЙ SYNC
# ==========================================
async def periodic_force_sync(log_func=print):
    """Принудительная синхронизация всех монет в Redis каждые 30 секунд."""
    while True:
        await asyncio.sleep(30)
        try:
            for symbol in list(alpha_symbols):
                try:
                    await sync_to_cache_async(symbol, log_func)
                except Exception:
                    pass
        except Exception as e:
            log_func(f"❌ Ошибка periodic_force_sync: {e}")


# ==========================================
# 🔥 ГЛАВНАЯ ФУНКЦИЯ
# ==========================================
async def main_async(log_func=print):
    """Главная асинхронная функция — запускает все задачи"""
    global alpha_symbols

    log_func("🚀 Запуск Binance Alpha Async Monitor (WORKER NODE)...")

    # Заполняем alpha_map кэш
    try:
        from screener.views import get_alpha_symbol_map
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, get_alpha_symbol_map)
    except Exception as e:
        log_func(f"⚠️ Не удалось заполнить alpha map кэш: {e}")

    # Строим пересечение Binance master + alpha_map
    initial_symbols = await build_alpha_symbol_list()

    attempts = 0
    while not initial_symbols and attempts < 12:
        await asyncio.sleep(5)
        attempts += 1
        initial_symbols = await build_alpha_symbol_list()

    if not initial_symbols:
        pass

    alpha_map = await _get_alpha_map_async() or {}
    # Параллельная инициализация стаканов (семафор 5)
    sem = asyncio.Semaphore(5)

    async def init_with_sem(symbol):
        async with sem:
            return await init_order_book_async(symbol, alpha_map, log_func)

    results = await asyncio.gather(*[init_with_sem(s) for s in initial_symbols])

    active_symbols = []
    for symbol, (success, saved_count) in zip(initial_symbols, results):
        if success:
            active_symbols.append(symbol)
        else:
            pass

    alpha_symbols = active_symbols

    tasks = [
        ws_listener(alpha_map, log_func),
        process_queue(alpha_map, log_func),
        periodic_refresh(log_func),
        periodic_force_sync(log_func),
    ]

    await asyncio.gather(*tasks)


def start_alpha_async_monitor(log_func=print):
    """Синхронная обёртка для запуска из Django"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(main_async(log_func))
    except Exception as e:
        log_func(f"❌ Binance Alpha Async Monitor упал: {e}")
        import traceback
        log_func(traceback.format_exc())
    finally:
        loop.run_until_complete(close_http_client())
        loop.close()
