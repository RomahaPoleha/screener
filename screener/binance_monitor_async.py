"""
Binance Monitor ASYNC — ФИНАЛЬНАЯ ОПТИМИЗИРОВАННАЯ ВЕРСИЯ
Исправления:
1. Возвращен @depth@100ms (дельта) для фьючерсов, чтобы стакан не "раздувался" фантомными заявками.
2. Добавлена защита от гонки данных (race condition) при обновлении last_sync_time.
3. Безопасное обрезание USDT из тикера (проверка .endswith).
4. Увеличен размер очереди и добавлено логирование при ее переполнении (вместо молчаливого игнорирования).
"""
import asyncio
import json
import time
import websockets
from django.core.cache import cache
import ccxt
from . import coin_selection

# ==========================================
# ГЛОБАЛЬНЫЕ ЭКЗЕМПЛЯРЫ CCXT (ОДИН на рынок)
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
binance_futures_volume_stats = {}
futures_symbols = []
# Увеличен размер очереди для предотвращения потерь при пиковых нагрузках
binance_futures_message_queue = asyncio.Queue(maxsize=50000)
binance_futures_lock = asyncio.Lock()
binance_futures_reconnect_event = asyncio.Event()

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — SPOT
# ==========================================
binance_spot_order_books = {}
binance_spot_density_timestamps = {}
binance_spot_volume_stats = {}
spot_symbols = []
binance_spot_message_queue = asyncio.Queue(maxsize=50000)
binance_spot_lock = asyncio.Lock()
binance_spot_reconnect_event = asyncio.Event()

# URLs
FUTURES_WS_URL = "wss://fstream.binance.com/ws"
SPOT_WS_URL = "wss://stream.binance.com:9443/ws"

# Настройки
TARGET_SYMBOLS = 15          # Целевое количество монет для отслеживания
SYNC_INTERVAL = 10           # Интервал синхронизации с Redis (сек)
CACHE_TTL = 30               # Время жизни в кэше (сек)
MIN_AGE_SECONDS = 180        # Минимальный возраст плотности для снижения порога
STABLE_COINS_LIMIT = 10      # Количество монет в "белом списке"

last_sync_time = {}


# ==========================================
# ТОП МОНЕТ И БЕЛЫЙ СПИСОК
# ==========================================
def _fetch_top_symbols_sync(market='futures'):
    try:
        exchange = ccxt_futures_exchange if market == 'futures' else ccxt_spot_exchange
        tickers = exchange.fetch_tickers()
        clean_fn = coin_selection.clean_swap if market == 'futures' else coin_selection.clean_spot
        coin_selection.update_volume_history(tickers, clean_fn)
        candidates = coin_selection.select_candidates(
            tickers, clean_fn, limit=60,
            log_func=lambda msg: None  # Подавляем лишний вывод при частых вызовах
        )
        return candidates[:30] # Берем с запасом, чтобы отфильтровать до TARGET_SYMBOLS
    except Exception as e:
        print(f"❌ Ошибка fetch_top_symbols(binance {market}): {e}")
        return []


async def get_top_symbols_async(market='futures'):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_top_symbols_sync, market)


def _fetch_stable_coins_sync(market='futures', limit=10):
    try:
        exchange = ccxt_futures_exchange if market == 'futures' else ccxt_spot_exchange
        tickers = exchange.fetch_tickers()
        coins_with_volume = []

        for symbol, data in tickers.items():
            if market == 'futures' and ':USDT' not in symbol:
                continue
            if market == 'spot' and '/USDT' not in symbol:
                continue

            volume = data.get('quoteVolume') or 0
            if volume < 100000:
                continue

            clean_symbol = symbol.replace('/USDT', '').replace(':USDT', '')
            if '-' in clean_symbol or not (2 <= len(clean_symbol) <= 15):
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
        ccxt_symbol = f"{symbol}/USDT:USDT" if market == 'futures' else f"{symbol}/USDT"

        # ✅ Оптимизация: берем 100 уровней вместо 500 (достаточно для плотностей)
        ob = exchange.fetch_order_book(ccxt_symbol, limit=100)

        bids = {float(price): float(qty) for price, qty in ob.get('bids', []) if float(price) > 0 and float(qty) > 0}
        asks = {float(price): float(qty) for price, qty in ob.get('asks', []) if float(price) > 0 and float(qty) > 0}

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

        lock = binance_futures_lock if market == 'futures' else binance_spot_lock
        order_books = binance_futures_order_books if market == 'futures' else binance_spot_order_books
        timestamps = binance_futures_density_timestamps if market == 'futures' else binance_spot_density_timestamps

        async with lock:
            order_books[symbol] = {'bids': bids, 'asks': asks}
            timestamps[symbol] = {}

        saved_count = await sync_to_cache_async(symbol, market, log_func)
        log_func(f"✅ binance {market} Стакан {symbol}: {len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}")
        return saved_count
    except Exception as e:
        log_func(f"❌ init_order_book_async(binance {market} {symbol}): {e}")
        return 0


# ==========================================
# СИНХРОНИЗАЦИЯ В REDIS (ОПТИМИЗИРОВАННАЯ)
# ==========================================
async def sync_to_cache_async(symbol, market='futures', log_func=print):
    try:
        lock = binance_futures_lock if market == 'futures' else binance_spot_lock
        order_books = binance_futures_order_books if market == 'futures' else binance_spot_order_books
        timestamps = binance_futures_density_timestamps if market == 'futures' else binance_spot_density_timestamps
        volume_stats = binance_futures_volume_stats if market == 'futures' else binance_spot_volume_stats
        key = f"scalp:{market}:binance:{symbol}"

        # ШАГ 1: Быстрое копирование данных под блокировкой
        async with lock:
            book = order_books.get(symbol, {})
            if not book:
                return 0
            # Делаем поверхностные копии словарей, чтобы работать с ними без блокировки
            ts = dict(timestamps.get(symbol, {}))
            stats = dict(volume_stats.get(symbol, {}))

        now = time.time()
        densities = []
        is_first_load = len(ts) == 0
        new_stats = {}

        # ШАГ 2: Тяжелые вычисления БЕЗ блокировки
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
                    ts[price] = now - MIN_AGE_SECONDS if is_first_load else now
                    continue

                densities.append({
                    'price': price,
                    'volume': volume,
                    'side': side_name,
                    'timestamp': ts[price],
                    'exchange': 'binance'
                })

        # ШАГ 3: Запись в Redis без блокировки
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, cache.set, key, densities, CACHE_TTL)
        except RuntimeError:
            pass

        # ШАГ 4: Короткая блокировка только для обновления метаданных
        async with lock:
            timestamps[symbol] = ts
            volume_stats[symbol] = new_stats

        return len(densities)
    except Exception as e:
        log_func(f"❌ sync_to_cache_async(binance {market} {symbol}): {e}")
        return 0


# ==========================================
# WEBSOCKET LISTENER
# ==========================================
async def ws_listener(market='futures', log_func=print):
    reconnect_event = binance_futures_reconnect_event if market == 'futures' else binance_spot_reconnect_event
    ws_url = FUTURES_WS_URL if market == 'futures' else SPOT_WS_URL
    queue = binance_futures_message_queue if market == 'futures' else binance_spot_message_queue

    while True:
        try:
            symbols = futures_symbols if market == 'futures' else spot_symbols
            if not symbols:
                await asyncio.sleep(5)
                continue

            log_func(f"🔌 binance {market} WS подключение: {len(symbols)} символов")

            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                # ✅ ИСПРАВЛЕНО: Используем @depth@100ms (дельта) для обоих рынков.
                # Это гарантирует, что мы получаем сообщения с qty=0 для удаления заявок.
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

                    # ✅ ИСПРАВЛЕНО: Логирование переполнения вместо молчаливого pass
                    try:
                        queue.put_nowait(message)
                    except asyncio.QueueFull:
                        log_func(f"⚠️ binance {market} Очередь переполнена! Пропуск сообщения.")

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

            # Извлекаем данные независимо от формата обертки Binance
            stream_data = data.get('data', data)
            symbol = stream_data.get('s', '')

            # ✅ ИСПРАВЛЕНО: Безопасное удаление USDT
            if symbol.endswith('USDT'):
                symbol = symbol[:-4]

            bids = stream_data.get('b', [])
            asks = stream_data.get('a', [])

            if bids or asks:
                await handle_update_async(symbol, bids, asks, market, log_func)

        except json.JSONDecodeError:
            continue # Игнорируем битые JSON
        except Exception as e:
            log_func(f"❌ binance {market} process_queue ошибка: {e}")


async def handle_update_async(symbol, bids_delta, asks_delta, market, log_func):
    lock = binance_futures_lock if market == 'futures' else binance_spot_lock
    order_books = binance_futures_order_books if market == 'futures' else binance_spot_order_books
    timestamps = binance_futures_density_timestamps if market == 'futures' else binance_spot_density_timestamps

    async with lock:
        if symbol not in order_books:
            return

        book = order_books[symbol]
        ts = timestamps.get(symbol, {})
        changed = False

        # Обрабатываем bids и asks одинаково
        for side_name, delta in [('bids', bids_delta), ('asks', asks_delta)]:
            for row in delta:
                if not isinstance(row, (list, tuple)) or len(row) < 2:
                    continue
                try:
                    price = float(row[0])
                    qty = float(row[1])
                    side_dict = book[side_name]

                    if qty == 0:
                        if price in side_dict:
                            del side_dict[price]
                            ts.pop(price, None)
                            changed = True
                    else:
                        side_dict[price] = qty
                        if price not in ts:
                            ts[price] = time.time()
                        changed = True
                except (ValueError, TypeError):
                    continue

        if changed:
            timestamps[symbol] = ts

    # ✅ ИСПРАВЛЕНО: Double-checked locking для last_sync_time, чтобы избежать гонки данных
    if changed:
        key = f"binance:{market}:{symbol}"
        now = time.time()

        if now - last_sync_time.get(key, 0.0) >= SYNC_INTERVAL:
            async with lock:
                now = time.time() # Проверяем время еще раз внутри блокировки
                if now - last_sync_time.get(key, 0.0) >= SYNC_INTERVAL:
                    last_sync_time[key] = now
                    # Запускаем синхронизацию без удержания основной блокировки (она отпустится после выхода из with)
                    # Но чтобы быть совсем корректными, вызовем ее отдельно:

    # Вызываем синхронизацию вне основного блока обработки дельт, чтобы не тормозить очередь
    if changed and (time.time() - last_sync_time.get(key, 0.0) >= SYNC_INTERVAL):
        await sync_to_cache_async(symbol, market, log_func)


# ==========================================
# ПЕРИОДИЧЕСКАЯ РОТАЦИЯ
# ==========================================
async def periodic_refresh(log_func=print):
    while True:
        await asyncio.sleep(300) # 5 минут
        try:
            for market in ['futures', 'spot']:
                symbols_list = futures_symbols if market == 'futures' else spot_symbols
                stable_list = stable_futures_symbols if market == 'futures' else stable_spot_symbols
                reconnect_event = binance_futures_reconnect_event if market == 'futures' else binance_spot_reconnect_event

                candidates = await get_top_symbols_async(market)
                old_symbols = set(symbols_list)
                new_active = []

                # 1. Добавляем стабильные монеты
                for symbol in stable_list:
                    if symbol in old_symbols:
                        new_active.append(symbol)
                    else:
                        saved_count = await init_order_book_async(symbol, market, log_func)
                        if saved_count > 0:
                            new_active.append(symbol)

                # 2. Добираем до TARGET_SYMBOLS из кандидатов
                for symbol in candidates:
                    if len(new_active) >= TARGET_SYMBOLS:
                        break
                    if symbol in new_active or symbol in old_symbols:
                        continue

                    saved_count = await init_order_book_async(symbol, market, log_func)
                    if saved_count > 0:
                        new_active.append(symbol)

                # 3. Очистка удаленных
                removed = old_symbols - set(new_active)
                added = set(new_active) - old_symbols

                if market == 'futures':
                    futures_symbols = new_active
                    lock = binance_futures_lock
                    books = binance_futures_order_books
                    tss = binance_futures_density_timestamps
                    stats = binance_futures_volume_stats
                else:
                    spot_symbols = new_active
                    lock = binance_spot_lock
                    books = binance_spot_order_books
                    tss = binance_spot_density_timestamps
                    stats = binance_spot_volume_stats

                if removed:
                    async with lock:
                        for sym in removed:
                            books.pop(sym, None)
                            tss.pop(sym, None)
                            stats.pop(sym, None)
                            last_sync_time.pop(f"binance:{market}:{sym}", None)
                    log_func(f"🗑️ binance {market} удалены: {', '.join(sorted(removed))}")

                if removed or added:
                    reconnect_event.set()
                    log_func(f"🔄 binance {market}: список изменен (+{len(added)} -{len(removed)})")
                else:
                    log_func(f"✅ binance {market}: список не изменен ({len(new_active)} монет)")

        except Exception as e:
            log_func(f"❌ Ошибка в periodic_refresh: {e}")


# ==========================================
# ГЛАВНАЯ ФУНКЦИЯ
# ==========================================
async def main_async(log_func=print):
    global futures_symbols, spot_symbols, stable_futures_symbols, stable_spot_symbols
    log_func("🚀 Запуск Binance Async Monitor...")

    stable_futures_symbols = await get_stable_coins_async('futures', STABLE_COINS_LIMIT)
    stable_spot_symbols = await get_stable_coins_async('spot', STABLE_COINS_LIMIT)
    log_func(f"🔒 Белый список futures: {stable_futures_symbols}")
    log_func(f"🔒 Белый список spot: {stable_spot_symbols}")

    futures_candidates = await get_top_symbols_async('futures')
    spot_candidates = await get_top_symbols_async('spot')

    active_futures = []
    for symbol in stable_futures_symbols:
        if await init_order_book_async(symbol, 'futures', log_func) > 0:
            active_futures.append(symbol)

    for symbol in futures_candidates[:TARGET_SYMBOLS]:
        if symbol not in active_futures and await init_order_book_async(symbol, 'futures', log_func) > 0:
            active_futures.append(symbol)

    active_spot = []
    for symbol in stable_spot_symbols:
        if await init_order_book_async(symbol, 'spot', log_func) > 0:
            active_spot.append(symbol)

    for symbol in spot_candidates[:TARGET_SYMBOLS]:
        if symbol not in active_spot and await init_order_book_async(symbol, 'spot', log_func) > 0:
            active_spot.append(symbol)

    futures_symbols = active_futures
    spot_symbols = active_spot
    log_func(f"✅ Монитор инициализирован: {len(active_futures)} futures, {len(active_spot)} spot")

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