"""
Binance Monitor ASYNC — MASTER NODE
Управляет списками монет для всех бирж
"""
import asyncio
import json
import time
import websockets
from django.core.cache import cache
import ccxt
from . import coin_selection
from django.conf import settings

# ==========================================
# MERGED CACHE CONFIG
# ==========================================
ENABLE_MERGED_CACHE = getattr(settings, 'ENABLE_MERGED_CACHE', True)
MERGED_CACHE_TTL = getattr(settings, 'MERGED_CACHE_TTL', 30)
MERGED_MIN_VOLUME = getattr(settings, 'MERGED_MIN_VOLUME', 1000)

# ==========================================
# ГЛОБАЛЬНЫЕ ЭКЗЕМПЛЯРЫ CCXT (Безопасная оптимизация)
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

# Отслеживание первой инициализации стакана для каждого символа
binance_futures_first_load_done = set()
binance_spot_first_load_done = set()


# URLs
FUTURES_WS_URL = "wss://fstream.binance.com/ws"
SPOT_WS_URL = "wss://stream.binance.com:9443/ws"

# Rate limiting
last_sync_time = {}
MIN_AGE_SECONDS = 180
CACHE_TTL = 30
SYNC_INTERVAL = 3

# Лёгкая статистика объёмов
binance_futures_volume_stats = {}
binance_spot_volume_stats = {}

# ==========================================
# MEMORY LEAK PREVENTION CONSTANTS
# ==========================================
MAX_STATS_PER_SYMBOL = 200      # макс. ценовых уровней на символ
MAX_STATS_AGE_SEC = 7200        # 2 часа — макс. возраст записи в volume_stats
MAX_TS_AGE_SEC = 3600           # 1 час — макс. возраст записи в density_timestamps

# ==========================================
# НОВАЯ ФУНКЦИЯ: DYNAMIC ПО NATR
# ==========================================
# ==========================================
# ТОП МОНЕТ (Теперь по NATR, а не по RVOL+Score)
# ==========================================
# ==========================================
# ТОП МОНЕТ (Теперь по NATR, а не по RVOL+Score)
# ==========================================
def _fetch_dynamic_by_natr_sync(market='futures', limit=40):
    """Отбирает топ-N монет по 1-минутному NATR из Redis"""
    try:
        exchange = ccxt_futures_exchange if market == 'futures' else ccxt_spot_exchange
        tickers = exchange.fetch_tickers()

        # Шаг 1: Фильтруем по базовому объёму
        coins_with_volume = []
        for symbol, data in tickers.items():
            if market == 'futures' and ':USDT' not in symbol:
                continue
            if market == 'spot' and '/USDT' not in symbol:
                continue

            volume = data.get('quoteVolume') or 0
            if volume < 10000:
                continue

            clean_symbol = symbol.replace('/USDT', '').replace(':USDT', '')
            if '-' in clean_symbol or len(clean_symbol) < 2 or len(clean_symbol) > 15:
                continue
            if not clean_symbol.replace('_', '').isalnum():
                continue

            coins_with_volume.append((clean_symbol, volume))

        if not coins_with_volume:
            return []

        # Шаг 2: Batch-чтение NATR из Redis
        natr_batch = {}
        natr_keys = [f"natr_{s}_future" for s, _ in coins_with_volume]
        for key in natr_keys:
            natr_batch[key] = cache.get(key) or {}

        # Шаг 3: Извлекаем 1m NATR и сортируем
        coins_with_natr = []
        for symbol, volume in coins_with_volume:
            natr_data = natr_batch.get(f"natr_{symbol}_future", {})
            natr_1m = natr_data.get('natr_1m30')

            if natr_1m is not None:  # Пропускаем монеты без NATR
                coins_with_natr.append((symbol, float(natr_1m), volume))

        if not coins_with_natr:
            return []

        # Сортируем по NATR по убыванию (самые волатильные вверху)
        coins_with_natr.sort(key=lambda x: x[1], reverse=True)

        result = [s for s, natr, vol in coins_with_natr[:limit]]
        
        return result

    except Exception as e:
        print(f"❌ Ошибка _fetch_dynamic_by_natr({market}): {e}")
        return []


async def get_top_symbols_async(market='futures'):
    """Асинхронная обёртка для отбора по NATR"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_dynamic_by_natr_sync, market, 40)

# ==========================================
# БЕЛЫЙ СПИСОК (без изменений)
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
# ИНИЦИАЛИЗАЦИЯ СТАКАНА (без изменений)
# ==========================================
def _init_order_book_sync(symbol, market):
    try:
        exchange = ccxt_futures_exchange if market == 'futures' else ccxt_spot_exchange

        if market == 'futures':
            ccxt_symbol = f"{symbol}/USDT:USDT"
        else:
            ccxt_symbol = f"{symbol}/USDT"

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
        
        return saved_count

    except Exception as e:
        log_func(f"❌ init_order_book_async(binance {market} {symbol}): {e}")
        return 0


# ==========================================
# СИНХРОНИЗАЦИЯ В REDIS (ИСПРАВЛЕНО)
# ==========================================
# ==========================================
# СИНХРОНИЗАЦИЯ В REDIS (ИСПРАВЛЕНО: пороги + зачистка призраков)
# ==========================================
async def sync_to_cache_async(symbol, market='futures', log_func=print):
    try:
        if market == 'futures':
            async with binance_futures_lock:
                book = binance_futures_order_books.get(symbol, {})
                ts = binance_futures_density_timestamps.get(symbol, {})
                stats = binance_futures_volume_stats.get(symbol, {})
                if not book:
                    return 0
                key = f"scalp:futures:binance:{symbol}"
        else:
            async with binance_spot_lock:
                book = binance_spot_order_books.get(symbol, {})
                ts = binance_spot_density_timestamps.get(symbol, {})
                stats = binance_spot_volume_stats.get(symbol, {})
                if not book:
                    return 0
                key = f"scalp:spot:binance:{symbol}"

        now = time.time()
        densities = []

        first_load_set = binance_futures_first_load_done if market == 'futures' else binance_spot_first_load_done
        is_first_load = symbol not in first_load_set

        if is_first_load:
            first_load_set.add(symbol)

        new_stats = {}

        # 🔥 ШАГ 1: Собираем все цены, которые реально есть в стакане ПРЯМО СЕЙЧАС
        current_prices = set(book.get('bids', {}).keys()) | set(book.get('asks', {}).keys())

        for side, side_name in [('bids', 'buy'), ('asks', 'sell')]:
            for price, qty in book.get(side, {}).items():
                volume = price * qty

                # 🔥 ШАГ 2: Выравниваем пороги с recon.js (Futures: 50k, Spot: 10k)
                is_mature = (price in ts) and ((now - ts[price]) >= MIN_AGE_SECONDS)

                # Целевой порог для "созревшей" плотности
                target_min = 50000 if market == 'futures' else 10000
                # Для "несозревшей" делаем порог чуть выше (на 20%), чтобы отсечь мгновенный шум (флэппинг)
                min_volume = target_min if is_mature else int(target_min * 1.2)

                if volume < min_volume:
                    # 🔥 ШАГ 3: Если объем упал, ОБЯЗАТЕЛЬНО удаляем из ts, чтобы сбросить таймер
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

                age_seconds = int(now - ts[price])

                densities.append({
                    'price': price,
                    'volume': volume,
                    'side': side_name,
                    'timestamp': ts[price],
                    'age_seconds': age_seconds,
                    'exchange': 'binance'
                })

        # 🔥 ШАГ 4: ЖЕСТКАЯ ЗАЧИСТКА ПРИЗРАКОВ
        # Удаляем из памяти всё, чего уже нет в текущем стакане, даже если оно было "созревшим"
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

        # Ограничение volume_stats по возрасту записей (MAX_STATS_AGE_SEC)
        old_stats_prices = [p for p, stat in new_stats.items() if now - stat.get('sum', 0) / max(stat.get('count', 1), 1) > MAX_STATS_AGE_SEC]
        # Примечание: в stats нет явного timestamp, используем proxy через ts
        # Более точное ограничение по возрасту делаем через ts выше

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, cache.set, key, densities, CACHE_TTL)
        except RuntimeError:
            pass

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
# WEBSOCKET LISTENER (без изменений)
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
                    streams = [f"{s.lower()}usdt@depth@100ms" for s in symbols]
                    subscribe_msg = {
                        "method": "SUBSCRIBE",
                        "params": streams,
                        "id": 1
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    

                    while True:
                        if reconnect_event.is_set():
                            reconnect_event.clear()
                            
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
# ОБРАБОТКА ОЧЕРЕДИ (без изменений)
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
# MERGED CACHE BUILDER (Single Writer: Binance Monitor)
# ==========================================
async def build_merged_cache(market, symbols, log_func=print):
    """Строит pre-aggregated merged кэш для api_densities_table.
    Вызывается только из Binance monitor (single writer pattern)."""
    if not ENABLE_MERGED_CACHE or not symbols:
        return

    EXCHANGES = ['binance', 'bybit', 'okx', 'gate', 'mexc', 'bitget', 'binance_alpha']
    now = time.time()

    # Белый список (тяжеловесы) — стабильные монеты Binance
    stable_set = set(stable_futures_symbols if market == 'futures' else stable_spot_symbols)

    # 1. Собираем все ключи для batch read
    all_keys = []
    key_meta = {}  # key -> (symbol, exchange)

    for symbol in symbols:
        symbol_base = symbol.replace('USDT', '') if symbol.endswith('USDT') else symbol
        for exchange in EXCHANGES:
            if exchange == 'binance_alpha' and market == 'spot':
                cache_symbol = symbol_base + 'USDT'
            else:
                cache_symbol = symbol_base
            key = f"scalp:{market}:{exchange}:{cache_symbol}"
            all_keys.append(key)
            key_meta[key] = (symbol, exchange)

    # 2. Один batch read
    try:
        loop = asyncio.get_running_loop()
        all_data = await loop.run_in_executor(None, cache.get_many, all_keys)
    except Exception as e:
        log_func(f"⚠️ merged cache {market}: get_many failed: {e}")
        return

    # 3. Мержим и фильтруем
    merged = []
    for key, data in all_data.items():
        if not data:
            continue
        symbol, exchange = key_meta[key]
        for item in data:
            try:
                vol = item['volume']
                if vol < MERGED_MIN_VOLUME:
                    continue
                age = now - item['timestamp']
                if age < 180:
                    continue
                merged.append({
                    'symbol': symbol,
                    'price': item['price'],
                    'volume': vol,
                    'side': item['side'],
                    'age_seconds': round(age, 1),
                    'exchange': item.get('exchange', exchange),
                    'market': market,
                    'is_whitelist': symbol in stable_set,
                })
            except (KeyError, TypeError):
                continue

    # 4. Сортируем по объёму DESC
    merged.sort(key=lambda x: x['volume'], reverse=True)

    # 5. Пишем merged ключ с метаданными
    merged_payload = {
        'server_time': now,
        'symbols_count': len(symbols),
        'densities': merged,
        'min_volume': MERGED_MIN_VOLUME,
    }

    try:
        await loop.run_in_executor(
            None,
            cache.set,
            f'scalp:densities:merged:{market}',
            merged_payload,
            MERGED_CACHE_TTL
        )
        log_func(f"✅ merged cache {market}: {len(merged)} densities, {len(symbols)} symbols")
    except Exception as e:
        log_func(f"⚠️ merged cache {market}: set failed: {e}")


# ==========================================
# ПЕРИОДИЧЕСКАЯ РОТАЦИЯ (ИЗМЕНЕНА)
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

            # ШАГ 1: Сохраняем монеты из белого списка
            for symbol in stable_futures_symbols:
                if symbol in old_symbols:
                    new_active.append(symbol)
                else:
                    saved_count = await init_order_book_async(symbol, 'futures', log_func)
                    new_active.append(symbol)
                    

            # ШАГ 2: Добавляем топ по NATR
            for symbol in candidates_f:
                if len(new_active) >= 50:
                    break
                if symbol in new_active:
                    continue
                if symbol in old_symbols:
                    new_active.append(symbol)
                    continue
                saved_count = await init_order_book_async(symbol, 'futures', log_func)
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
                

            if removed or added:
                binance_futures_reconnect_event.set()
            else:
                pass

            # 🔥 Фильтруем master-лист: оставляем только символы с реальными данными в Redis
            # Это ускоряет api_densities_table, который иначе перебирает пустые символы
            try:
                loop = asyncio.get_running_loop()
                # Формируем ключи для проверки: scalp:{market}:binance:{symbol}
                check_keys = [f"scalp:futures:binance:{sym}" for sym in new_active]
                # batch get - один round-trip вместо N
                cached_data = await loop.run_in_executor(None, cache.get_many, check_keys)
                # Оставляем только те, у кого есть данные
                new_active = [sym for sym in new_active if cached_data.get(f"scalp:futures:binance:{sym}")]
            except Exception as e:
                log_func(f"⚠️ binance futures: ошибка фильтрации master-листа: {e}")

            # 🔥 НОВОЕ: Публикуем master-список в Redis
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, cache.set, 'scalp:master:futures', new_active, 3600)
                # Инвалидируем кэш таблицы плотностей для futures
                await loop.run_in_executor(None, lambda: _safe_delete_pattern("densities:table:futures:*"))
                
            except Exception as e:
                log_func(f"⚠️ binance futures: не удалось опубликовать master-список: {e}")

            # 🔥 MERGED CACHE: строим pre-aggregated кэш для futures
            try:
                await build_merged_cache('futures', new_active, log_func)
            except Exception as e:
                log_func(f"⚠️ merged cache futures: {e}")

            # --- Spot ротация ---
            candidates_s = await get_top_symbols_async('spot')
            old_symbols = set(spot_symbols)
            new_active = []

            for symbol in stable_spot_symbols:
                if symbol in old_symbols:
                    new_active.append(symbol)
                else:
                    saved_count = await init_order_book_async(symbol, 'spot', log_func)
                    new_active.append(symbol)
                    

            for symbol in candidates_s:
                if len(new_active) >= 50:
                    break
                if symbol in new_active:
                    continue
                if symbol in old_symbols:
                    new_active.append(symbol)
                    continue
                saved_count = await init_order_book_async(symbol, 'spot', log_func)
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
                

            if removed or added:
                binance_spot_reconnect_event.set()
            else:
                pass

            # 🔥 Фильтруем master-лист spot: оставляем только символы с реальными данными
            try:
                loop = asyncio.get_running_loop()
                check_keys = [f"scalp:spot:binance:{sym}" for sym in new_active]
                cached_data = await loop.run_in_executor(None, cache.get_many, check_keys)
                new_active = [sym for sym in new_active if cached_data.get(f"scalp:spot:binance:{sym}")]
            except Exception as e:
                log_func(f"⚠️ binance spot: ошибка фильтрации master-листа: {e}")

            # 🔥 НОВОЕ: Публикуем master-список в Redis
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, cache.set, 'scalp:master:spot', new_active, 3600)
                # Инвалидируем кэш таблицы плотностей для spot
                await loop.run_in_executor(None, lambda: _safe_delete_pattern("densities:table:spot:*"))
                
            except Exception as e:
                log_func(f"⚠️ binance spot: не удалось опубликовать master-список: {e}")

            # 🔥 MERGED CACHE: строим pre-aggregated кэш для spot
            try:
                await build_merged_cache('spot', new_active, log_func)
            except Exception as e:
                log_func(f"⚠️ merged cache spot: {e}")

        except Exception as e:
            log_func(f"❌ Ошибка в periodic_refresh(binance): {e}")

# ==========================================
# ГЛАВНАЯ ФУНКЦИЯ (ИЗМЕНЕНА)
# ==========================================
async def main_async(log_func=print):
    global futures_symbols, spot_symbols, stable_futures_symbols, stable_spot_symbols

    log_func("🚀 Запуск Binance Async Monitor (MASTER NODE)...")

    # --- Шаг 1: Получаем белый список ---
    stable_f = await get_stable_coins_async('futures', STABLE_COINS_LIMIT)
    stable_s = await get_stable_coins_async('spot', STABLE_COINS_LIMIT)
    stable_futures_symbols = stable_f
    stable_spot_symbols = stable_s

    log_func(f"🔒 Белый список futures: {stable_f}")
    log_func(f"🔒 Белый список spot: {stable_s}")

    # --- Шаг 2: Получаем кандидатов по NATR ---
    futures_candidates = await get_top_symbols_async('futures')
    spot_candidates = await get_top_symbols_async('spot')

    # --- Шаг 3: Инициализируем белый список ---
    active_futures = []
    for symbol in stable_f:
        saved_count = await init_order_book_async(symbol, 'futures', log_func)
        active_futures.append(symbol)
        

    active_spot = []
    for symbol in stable_s:
        saved_count = await init_order_book_async(symbol, 'spot', log_func)
        active_spot.append(symbol)
        

    # --- Шаг 4: Добавляем топ по NATR ---
    for symbol in futures_candidates[:40]:
        if symbol in active_futures:
            continue
        saved_count = await init_order_book_async(symbol, 'futures', log_func)
        active_futures.append(symbol)
        

    for symbol in spot_candidates[:40]:
        if symbol in active_spot:
            continue
        saved_count = await init_order_book_async(symbol, 'spot', log_func)
        active_spot.append(symbol)
        

    futures_symbols = active_futures
    spot_symbols = active_spot

    

    # 🔥 НОВОЕ: Публикуем master-списки при старте
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, cache.set, 'scalp:master:futures', active_futures, 600)
        await loop.run_in_executor(None, cache.set, 'scalp:master:spot', active_spot, 600)
        # Инвалидируем кэш таблицы плотностей при старте
        await loop.run_in_executor(None, lambda: _safe_delete_pattern("densities:table:*"))
        # 🔥 MERGED CACHE: строим сразу при старте, не ждём periodic_refresh (5 мин)
        await build_merged_cache('futures', active_futures, log_func)
        await build_merged_cache('spot', active_spot, log_func)
        
    except Exception as e:
        log_func(f"⚠️ Не удалось опубликовать master-списки при старте: {e}")


    tasks = [
        ws_listener('futures', log_func),
        ws_listener('spot', log_func),
        process_queue('futures', log_func),
        process_queue('spot', log_func),
        periodic_refresh(log_func),
    ]

    await asyncio.gather(*tasks)


# ==========================================
# HELPERS
# ==========================================
def _safe_delete_pattern(pattern):
    """Безопасное удаление по паттерну: работает с Redis, игнорирует file cache."""
    try:
        from django_redis.cache import RedisCache
        from django.core.cache import caches
        cache_backend = caches['default']
        if hasattr(cache_backend, 'delete_pattern'):
            cache_backend.delete_pattern(pattern)
        elif hasattr(cache_backend, 'client') and hasattr(cache_backend.client, 'delete_pattern'):
            cache_backend.client.delete_pattern(pattern)
    except Exception:
        pass  # file cache или другая реализация — игнорируем


def start_binance_async_monitor(log_func=print):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main_async(log_func))
    except Exception as e:
        log_func(f"❌ Binance Async Monitor упал: {e}")
        import traceback
        log_func(traceback.format_exc())
    finally:
        pass