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
from .density_config import DENSITY_MIN_VOLUME_FUTURES, DENSITY_MIN_VOLUME_SPOT, DENSITY_IMMATURE_FACTOR

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

# Защита от мгновенного созревания при очистке стакана спуфером
okx_futures_first_load_done = set()
okx_spot_first_load_done = set()

okx_futures_removed = {}
okx_spot_removed = {}
okx_futures_mid_history = {}
okx_spot_mid_history = {}

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
    """Ленивая инициализация aiohttp клиента с проверкой"""
    global _http_client
    if _http_client is None or _http_client.closed:
        _http_client = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={'User-Agent': 'Mozilla/5.0'}
        )
    return _http_client


async def close_http_client():
    """Явное закрытие сессии (для graceful shutdown)"""
    global _http_client
    if _http_client and not _http_client.closed:
        await _http_client.close()
        _http_client = None


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
        
        return saved_count

    except Exception as e:
        log_func(f"❌ init_order_book_async(okx {market} {symbol}): {e}")
        return 0



# ==========================================
# СИНХРОНИЗАЦИЯ В REDIS (async) — ИСПРАВЛЕНО (пороги + зачистка призраков)
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
    from .density_config import DENSITY_MIN_VOLUME_FUTURES, DENSITY_MIN_VOLUME_SPOT
    min_volume = DENSITY_MIN_VOLUME_FUTURES if market == 'futures' else DENSITY_MIN_VOLUME_SPOT
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

        first_load_set = okx_futures_first_load_done if market == 'futures' else okx_spot_first_load_done
        is_first_load = symbol not in first_load_set

        if is_first_load:
            first_load_set.add(symbol)

        new_stats = {}

        # Текущая mid-цена
        bids_keys = list(book.get('bids', {}).keys())
        asks_keys = list(book.get('asks', {}).keys())
        mid = None
        if bids_keys and asks_keys:
            mid = (max(bids_keys) + min(asks_keys)) / 2

        mh = okx_futures_mid_history if market == 'futures' else okx_spot_mid_history
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

        removed_store = okx_futures_removed if market == 'futures' else okx_spot_removed
        removed_list = [x for x in removed_store.get(symbol, []) if (now - x[1]) < (RECENT_WINDOW * 2)]
        removed_store[symbol] = removed_list
        recently_removed = list(removed_list)

        # 🔥 ШАГ 1: Собираем все цены, которые реально есть в стакане ПРЯМО СЕЙЧАС
        current_prices = set(book.get('bids', {}).keys()) | set(book.get('asks', {}).keys())

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

                # 🔥 ШАГ 2: Выравниваем пороги с recon.js (Futures: 50k, Spot: 10k)
                is_mature = (price in ts) and ((now - ts[price]) >= MIN_AGE_SECONDS)

                # Целевой порог для "созревшей" плотности
                target_min = DENSITY_MIN_VOLUME_FUTURES if market == 'futures' else DENSITY_MIN_VOLUME_SPOT
                # Для "несозревшей" делаем порог чуть выше (на 20%), чтобы отсечь мгновенный шум
                min_volume = target_min if is_mature else int(target_min * DENSITY_IMMATURE_FACTOR)

                if volume < min_volume:
                    # Если объем упал, ОБЯЗАТЕЛЬНО удаляем из ts, чтобы сбросить таймер
                    if price in ts:
                        ts.pop(price, None)
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

                    # Проверка стабильности (порог 0.5 — твой оригинальный)
                    if new_stat['count'] >= 3:
                        avg = new_stat['sum'] / new_stat['count']
                        spread = new_stat['max'] - new_stat['min']
                        stability_ratio = spread / avg if avg > 0 else 0

                        if stability_ratio > 0.5:
                            ts[price] = now
                            new_stats[price] = {'min': volume, 'max': volume, 'sum': volume, 'count': 1}
                            continue
                else:
                    # Если это первый запуск символа — даём фору, иначе заставляем ждать
                    ts[price] = now
                    continue

                age_seconds = int(now - ts[price])

                densities.append({
                    'price': price,
                    'volume': volume,
                    'side': side_name,
                    'timestamp': ts[price],
                    'age_seconds': age_seconds,
                    'exchange': 'okx'
                })

        # 🔥 ШАГ 3: ЖЕСТКАЯ ЗАЧИСТКА ПРИЗРАКОВ
        # Детекция повторяющихся объёмов (volume clustering)
        densities = _detect_volume_clustering(densities, mid, log_func, symbol)

        # Детекция layering (анализ стакана)
        layering_prices = _detect_layering(book, mid, log_func, symbol, market)
        if layering_prices:
            before = len(densities)
            densities = [d for d in densities if d['price'] not in layering_prices]

        # Анти-спуф: отсекаем симметричные пары
        densities = _filter_symmetric_spoofs(densities, mid, log_func, symbol)

        # Удаляем из памяти всё, чего уже нет в текущем стакане
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

            async with websockets.connect(OKX_WS_URL, ping_interval=None, ping_timeout=None) as ws:
                heartbeat_task = asyncio.create_task(ws_heartbeat(ws, market, log_func))

                try:
                    args = [{"channel": "books", "instId": f"{s}{inst_id_suffix}"} for s in symbols]
                    await ws.send(json.dumps({"op": "subscribe", "args": args}))
                    

                    while True:
                        if reconnect_event.is_set():
                            reconnect_event.clear()
                            
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
            old_book = okx_futures_order_books.get(symbol, {})
            _ob = list(old_book.get('bids', {}).keys())
            _oa = list(old_book.get('asks', {}).keys())
            _omid = (max(_ob) + min(_oa)) / 2 if _ob and _oa else None
            new_ts = {p: old_ts[p] for p in new_bids if p in old_ts}
            new_ts.update({p: old_ts[p] for p in new_asks if p in old_ts})
            new_stats = {p: old_stats[p] for p in new_bids if p in old_stats}
            new_stats.update({p: old_stats[p] for p in new_asks if p in old_stats})

            if _omid is not None:
                for p in (set(old_ts) - set(new_ts)):
                    okx_futures_removed.setdefault(symbol, []).append((p, time.time(), (p - _omid) / _omid))

            okx_futures_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            okx_futures_density_timestamps[symbol] = new_ts
            okx_futures_volume_stats[symbol] = new_stats
    else:
        async with okx_spot_lock:
            old_ts = okx_spot_density_timestamps.get(symbol, {})
            old_stats = okx_spot_volume_stats.get(symbol, {})
            old_book = okx_spot_order_books.get(symbol, {})
            _ob = list(old_book.get('bids', {}).keys())
            _oa = list(old_book.get('asks', {}).keys())
            _omid = (max(_ob) + min(_oa)) / 2 if _ob and _oa else None
            new_ts = {p: old_ts[p] for p in new_bids if p in old_ts}
            new_ts.update({p: old_ts[p] for p in new_asks if p in old_ts})
            new_stats = {p: old_stats[p] for p in new_bids if p in old_stats}
            new_stats.update({p: old_stats[p] for p in new_asks if p in old_stats})

            if _omid is not None:
                for p in (set(old_ts) - set(new_ts)):
                    okx_spot_removed.setdefault(symbol, []).append((p, time.time(), (p - _omid) / _omid))

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
                            _bids = list(book['bids'].keys())
                            _asks = list(book['asks'].keys())
                            _mid = (max(_bids) + min(_asks)) / 2 if _bids and _asks else None
                            _removed = okx_futures_removed if market == 'futures' else okx_spot_removed
                            if _mid is not None:
                                _removed.setdefault(symbol, []).append((price, time.time(), (price - _mid) / _mid))
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
                            _bids = list(book['bids'].keys())
                            _asks = list(book['asks'].keys())
                            _mid = (max(_bids) + min(_asks)) / 2 if _bids and _asks else None
                            _removed = okx_futures_removed if market == 'futures' else okx_spot_removed
                            if _mid is not None:
                                _removed.setdefault(symbol, []).append((price, time.time(), (price - _mid) / _mid))
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
                            _bids = list(book['bids'].keys())
                            _asks = list(book['asks'].keys())
                            _mid = (max(_bids) + min(_asks)) / 2 if _bids and _asks else None
                            _removed = okx_futures_removed if market == 'futures' else okx_spot_removed
                            if _mid is not None:
                                _removed.setdefault(symbol, []).append((price, time.time(), (price - _mid) / _mid))
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
                            _bids = list(book['bids'].keys())
                            _asks = list(book['asks'].keys())
                            _mid = (max(_bids) + min(_asks)) / 2 if _bids and _asks else None
                            _removed = okx_futures_removed if market == 'futures' else okx_spot_removed
                            if _mid is not None:
                                _removed.setdefault(symbol, []).append((price, time.time(), (price - _mid) / _mid))
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
                continue

            old_symbols = set(okx_futures_symbols if market == 'futures' else okx_spot_symbols)
            new_active = []
            added = []
            removed = old_symbols - set(master_symbols)

            for symbol in master_symbols:
                if symbol not in old_symbols:
                    saved_count = await init_order_book_async(symbol, market, log_func)
                    new_active.append(symbol)
                    added.append(symbol)
                    
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
                    

                if added or removed:
                    okx_futures_reconnect_event.set()
                    
            else:
                okx_spot_symbols = new_active
                if removed:
                    async with okx_spot_lock:
                        for sym in removed:
                            okx_spot_order_books.pop(sym, None)
                            okx_spot_density_timestamps.pop(sym, None)
                            okx_spot_volume_stats.pop(sym, None)
                            last_sync_time.pop(f"okx:spot:{sym}", None)
                    

                if added or removed:
                    okx_spot_reconnect_event.set()
                    

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
                except Exception:
                    pass

            for symbol in list(okx_spot_symbols):
                try:
                    await sync_to_cache_async(symbol, 'spot', log_func)
                except Exception:
                    pass
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
        active_futures.append(symbol)
        

    active_spot = []
    for symbol in master_s:
        saved_count = await init_order_book_async(symbol, 'spot', log_func)
        active_spot.append(symbol)
        

    okx_futures_symbols = active_futures
    okx_spot_symbols = active_spot

    

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