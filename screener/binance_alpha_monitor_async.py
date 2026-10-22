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

# ==========================================


async def get_http_client():
    """Ленивая инициализация aiohttp клиента"""
    global _http_client
    if _http_client is None or _http_client.closed:
        _http_client = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
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
            log_func(f"⚠️ alpha {symbol}: нет в маппинге Alpha (base={base_symbol})")
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
            log_func(f"⚠️ alpha {symbol}: пустой стакан (ожидаем WS апдейты)")

        saved_count = await sync_to_cache_async(symbol, log_func)
        log_func(f"✅ alpha Стакан {symbol}: {len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}")
        return True, saved_count

    except Exception as e:
        log_func(f"❌ init_order_book_async(alpha {symbol}): {e}")
        import traceback
        log_func(traceback.format_exc())
        return False, 0


# ==========================================
# СИНХРОНИЗАЦИЯ В REDIS (async)
# ==========================================
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

        for side, side_name in [('bids', 'buy'), ('asks', 'sell')]:
            for price, qty in book.get(side, {}).items():
                volume = price * qty

                is_mature = (price in ts) and ((now - ts[price]) >= MIN_AGE_SECONDS)

                if not is_mature:
                    if is_first_load:
                        ts[price] = now - MIN_AGE_SECONDS
                    else:
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
            if densities:
                log_func(f"💾 alpha {symbol}: сохранено {len(densities)} плотностей в {key}")
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
                log_func(f"⚠️ alpha heartbeat завершён: {e}")
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
                log_func("⚠️ alpha: alpha_symbols пуст, жду 5 сек")
                await asyncio.sleep(5)
                continue

            if not alpha_map:
                log_func("⚠️ alpha: exchange:alpha:map пуст, жду 5 сек")
                await asyncio.sleep(5)
                continue

            log_func(f"🔌 alpha WS подключение: {len(symbols)} символов: {symbols[:5]}...")

            async with websockets.connect(ALPHA_WS_URL, ping_interval=30, ping_timeout=10) as ws:
                heartbeat_task = asyncio.create_task(ws_heartbeat(ws, log_func))

                try:
                    args = []
                    for s in symbols:
                        base = s.upper().replace('USDT', '')
                        alpha_sym = alpha_map.get(base)
                        if alpha_sym:
                            args.append(f"{alpha_sym.lower()}@fulldepth@100ms")
                        else:
                            log_func(f"⚠️ alpha: {s} не в alpha_map")

                    if not args:
                        log_func("⚠️ alpha: нет валидных символов для подписки")
                        await asyncio.sleep(30)
                        continue

                    subscribe_msg = {
                        "method": "SUBSCRIBE",
                        "params": args,
                        "id": int(time.time() * 1000)
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    log_func(f"✅ alpha WS подписан на {len(args)} символов: {args[:5]}...")

                    while True:
                        if alpha_reconnect_event.is_set():
                            alpha_reconnect_event.clear()
                            log_func("🔄 alpha: сигнал переподключения получен")
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
            log_func("⚠️ alpha WS закрыт, переподключение через 3 сек")
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
    log_func(f"🔍 alpha reverse_map built: {len(reverse_map)} entries")

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
                log_func(f"⚠️ alpha: unknown stream symbol: {alpha_symbol}")
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

        new_ts = {p: old_ts[p] for p in new_bids if p in old_ts}
        new_ts.update({p: old_ts[p] for p in new_asks if p in old_ts})

        new_stats = {p: old_stats[p] for p in new_bids if p in old_stats}
        new_stats.update({p: old_stats[p] for p in new_asks if p in old_stats})

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
                        log_func(f"✅ alpha {symbol}: добавлен (плотностей: {saved_count})")
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
                log_func(f"🗑️ alpha удалены: {', '.join(sorted(removed))}")

            if added or removed:
                alpha_reconnect_event.set()
                log_func(f"🔄 alpha: список синхронизирован (+{len(added)} -{len(removed)})")

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
                except Exception as e:
                    log_func(f"⚠️ alpha force sync {symbol}: {e}")
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
        log_func("✅ Alpha map кэш заполнен/проверен")
    except Exception as e:
        log_func(f"⚠️ Не удалось заполнить alpha map кэш: {e}")

    # Строим пересечение Binance master + alpha_map
    initial_symbols = await build_alpha_symbol_list()

    attempts = 0
    while not initial_symbols and attempts < 12:
        log_func("⏳ Alpha ожидает мастер-список и alpha_map из Redis...")
        await asyncio.sleep(5)
        attempts += 1
        initial_symbols = await build_alpha_symbol_list()

    if not initial_symbols:
        log_func("⚠️ Мастер-список пуст после ожидания. Alpha будет ждать обновления.")

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
            log_func(f"✅ alpha {symbol}: принят (плотностей: {saved_count})")
        else:
            log_func(f"⚠️ alpha {symbol}: пропущен")

    alpha_symbols = active_symbols
    log_func(f"✅ Binance Alpha Async Monitor инициализирован: {len(active_symbols)} символов")

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
