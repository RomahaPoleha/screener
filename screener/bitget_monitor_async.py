"""
Bitget Monitor ASYNC — пилотная версия на asyncio
Работает параллельно со старым монитором для теста
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
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ
# ==========================================
bitget_futures_order_books = {}
bitget_futures_density_timestamps = {}
bitget_futures_symbols = []
bitget_futures_message_queue = asyncio.Queue(maxsize=10000)
bitget_futures_lock = asyncio.Lock()

bitget_spot_order_books = {}
bitget_spot_density_timestamps = {}
bitget_spot_symbols = []
bitget_spot_message_queue = asyncio.Queue(maxsize=10000)
bitget_spot_lock = asyncio.Lock()

# URLs
BITGET_WS_URL = "wss://ws.bitget.com/v2/ws/public"
BITGET_FUTURES_REST_URL = "https://api.bitget.com/api/v2/mix/market/merge-depth?symbol={}USDT&productType=USDT-FUTURES&limit=100"
BITGET_SPOT_REST_URL = "https://api.bitget.com/api/v2/spot/market/merge-depth?symbol={}USDT&limit=100"

# Rate limiting
last_sync_time = {}

MIN_AGE_SECONDS = 180
CACHE_TTL = 900

# Глобальный aiohttp клиент (создаётся один раз)
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
# ТОП МОНЕТ (синхронный ccxt, запускаем в потоке)
# ==========================================
def _fetch_top_symbols_sync(market_type='swap'):
    """Синхронная функция для ccxt (запускается в отдельном потоке)"""
    try:
        exchange = ccxt.bitget({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': market_type}
        })
        tickers = exchange.fetch_tickers()

        clean_fn = coin_selection.clean_swap if market_type == 'swap' else coin_selection.clean_spot
        coin_selection.update_volume_history(tickers, clean_fn)

        candidates = coin_selection.select_candidates(
            tickers, clean_fn, limit=60,
            log_func=lambda msg: print(msg)
        )
        return candidates[:30]
    except Exception as e:
        print(f"❌ Ошибка fetch_top_symbols(bitget {market_type}): {e}")
        return []


async def get_top_symbols_async(market_type='swap'):
    """Асинхронная обёртка — запускает ccxt в отдельном потоке"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_top_symbols_sync, market_type)


# ==========================================
# ИНИЦИАЛИЗАЦИЯ СТАКАНОВ (async HTTP)
# ==========================================
async def init_order_book_async(symbol, market='futures', log_func=print):
    """Инициализация стакана через async HTTP"""
    try:
        url = (BITGET_FUTURES_REST_URL if market == 'futures' else BITGET_SPOT_REST_URL).format(symbol)

        client = await get_http_client()
        async with client.get(url) as resp:
            if resp.status != 200:
                log_func(f"⚠️ bitget {market} {symbol}: HTTP {resp.status}")
                return 0
            data = await resp.json()

        if data.get('code') != '00000':
            log_func(f"⚠️ bitget {market} {symbol}: code={data.get('code')}")
            return 0

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

        if not bids and not asks:
            log_func(f"⚠️ bitget {market} {symbol}: пустой стакан")
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
        log_func(f"✅ bitget {market} Стакан {symbol}: {len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}")
        return saved_count

    except Exception as e:
        log_func(f"❌ init_order_book_async(bitget {market} {symbol}): {e}")
        return 0


# ==========================================
# СИНХРОНИЗАЦИЯ В REDIS (async)
# ==========================================
async def sync_to_cache_async(symbol, market='futures', log_func=print):
    """Синхронизация стакана в Redis (async версия)"""
    try:
        if market == 'futures':
            async with bitget_futures_lock:
                book = bitget_futures_order_books.get(symbol, {})
                ts = bitget_futures_density_timestamps.get(symbol, {})
                if not book:
                    return 0
        else:
            async with bitget_spot_lock:
                book = bitget_spot_order_books.get(symbol, {})
                ts = bitget_spot_density_timestamps.get(symbol, {})
                if not book:
                    return 0

        key = f"scalp:{market}:bitget:{symbol}"
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
                    'exchange': 'bitget'
                })

        # Django cache синхронный — запускаем в потоке
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, cache.set, key, densities, CACHE_TTL)

        if market == 'futures':
            async with bitget_futures_lock:
                bitget_futures_density_timestamps[symbol] = ts
        else:
            async with bitget_spot_lock:
                bitget_spot_density_timestamps[symbol] = ts

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_to_cache_async(bitget {market} {symbol}): {e}")
        return 0


# ==========================================
# WEBSOCKET LISTENER (async)
# ==========================================
async def ws_listener(market='futures', log_func=print):
    """Бесконечный цикл подключения к WebSocket"""
    global bitget_futures_symbols, bitget_spot_symbols

    while True:
        try:
            symbols = bitget_futures_symbols if market == 'futures' else bitget_spot_symbols

            if not symbols:
                await asyncio.sleep(5)
                continue

            log_func(f"🔌 bitget {market} WS подключение: {len(symbols)} символов")

            async with websockets.connect(BITGET_WS_URL, ping_interval=None) as ws:
                # Подписка
                inst_type = "USDT-FUTURES" if market == 'futures' else "SPOT"
                args = [
                    {
                        "instId": f"{s}USDT",
                        "channel": "books15",
                        "instType": inst_type
                    }
                    for s in symbols
                ]
                await ws.send(json.dumps({"op": "subscribe", "args": args}))
                log_func(f"✅ bitget {market} WS подписан на {len(symbols)} символов")

                # Цикл приёма сообщений
                async for message in ws:
                    if message == 'pong':
                        continue

                    queue = bitget_futures_message_queue if market == 'futures' else bitget_spot_message_queue
                    try:
                        queue.put_nowait(message)
                    except asyncio.QueueFull:
                        pass  # Очередь переполнена — пропускаем

        except websockets.exceptions.ConnectionClosed:
            log_func(f"⚠️ bitget {market} WS закрыт, переподключение через 3 сек")
            await asyncio.sleep(3)
        except Exception as e:
            log_func(f"❌ bitget {market} WS ошибка: {e}, переподключение через 5 сек")
            await asyncio.sleep(5)


# ==========================================
# HEARTBEAT (async)
# ==========================================
async def ws_heartbeat(market='futures', log_func=print):
    """Отправка ping каждые 25 секунд"""
    # В websockets ping отправляется автоматически, эта функция не нужна
    # Оставляем для совместимости
    pass


# ==========================================
# ОБРАБОТКА ОЧЕРЕДИ (async)
# ==========================================
async def process_queue(market='futures', log_func=print):
    """Обработка очереди сообщений"""
    queue = bitget_futures_message_queue if market == 'futures' else bitget_spot_message_queue

    while True:
        try:
            message = await queue.get()

            if message == 'pong':
                continue

            data = json.loads(message)

            action = data.get('action', '')
            if action not in ('snapshot', 'update'):
                continue

            arg = data.get('arg', {})
            if arg.get('channel') not in ('books', 'books15'):
                continue

            expected_inst_type = "USDT-FUTURES" if market == 'futures' else "SPOT"
            if arg.get('instType') != expected_inst_type:
                continue

            inst_id = arg.get('instId', '')
            if not inst_id.endswith('USDT'):
                continue
            symbol = inst_id[:-4]

            data_list = data.get('data', [])
            if not data_list:
                continue

            for entry in data_list:
                raw_bids = entry.get('bids', [])
                raw_asks = entry.get('asks', [])

                if action == 'snapshot':
                    await handle_snapshot_async(symbol, raw_bids, raw_asks, market, log_func)
                elif action == 'update' and (raw_bids or raw_asks):
                    await handle_update_async(symbol, raw_bids, raw_asks, market, log_func)

        except Exception as e:
            log_func(f"❌ bitget {market} process_queue ошибка: {e}")


async def handle_snapshot_async(symbol, raw_bids, raw_asks, market, log_func):
    """Обработка снапшота"""
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
        async with bitget_futures_lock:
            old_ts = bitget_futures_density_timestamps.get(symbol, {})
            new_ts = {}
            for p in new_bids:
                if p in old_ts:
                    new_ts[p] = old_ts[p]
            for p in new_asks:
                if p in old_ts:
                    new_ts[p] = old_ts[p]

            bitget_futures_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            bitget_futures_density_timestamps[symbol] = new_ts
    else:
        async with bitget_spot_lock:
            old_ts = bitget_spot_density_timestamps.get(symbol, {})
            new_ts = {}
            for p in new_bids:
                if p in old_ts:
                    new_ts[p] = old_ts[p]
            for p in new_asks:
                if p in old_ts:
                    new_ts[p] = old_ts[p]

            bitget_spot_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            bitget_spot_density_timestamps[symbol] = new_ts

    await sync_to_cache_async(symbol, market, log_func)


async def handle_update_async(symbol, bids_delta, asks_delta, market, log_func):
    """Обработка дельты"""
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
                bitget_spot_density_timestamps[symbol] = ts

    # Rate limit: sync раз в 3 секунды
    key = f"bitget:{market}:{symbol}"
    now = time.time()
    if key not in last_sync_time or (now - last_sync_time[key]) >= 3:
        await sync_to_cache_async(symbol, market, log_func)
        last_sync_time[key] = now


# ==========================================
# ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ СПИСКОВ
# ==========================================
async def periodic_refresh(market='futures', log_func=print):
    """Периодическое обновление списка монет каждые 5 минут"""
    global bitget_futures_symbols, bitget_spot_symbols

    while True:
        await asyncio.sleep(300)  # 5 минут

        try:
            market_type = 'swap' if market == 'futures' else 'spot'
            candidates = await get_top_symbols_async(market_type)

            old_symbols = set(bitget_futures_symbols if market == 'futures' else bitget_spot_symbols)

            new_active = []
            TARGET = 30

            for symbol in candidates:
                if len(new_active) >= TARGET:
                    break

                if symbol in old_symbols:
                    new_active.append(symbol)
                    continue

                saved_count = await init_order_book_async(symbol, market, log_func)
                if saved_count > 0:
                    new_active.append(symbol)
                    log_func(f"✅ bitget {market} {symbol}: добавлен (плотностей: {saved_count})")
                else:
                    log_func(f"⚠️ bitget {market} {symbol}: пропущен")

            if market == 'futures':
                removed = old_symbols - set(new_active)
                bitget_futures_symbols = new_active
                if removed:
                    async with bitget_futures_lock:
                        for sym in removed:
                            bitget_futures_order_books.pop(sym, None)
                            bitget_futures_density_timestamps.pop(sym, None)
                    log_func(f"🗑️ bitget futures удалены: {', '.join(sorted(removed))}")
            else:
                removed = old_symbols - set(new_active)
                bitget_spot_symbols = new_active
                if removed:
                    async with bitget_spot_lock:
                        for sym in removed:
                            bitget_spot_order_books.pop(sym, None)
                            bitget_spot_density_timestamps.pop(sym, None)
                    log_func(f"🗑️ bitget spot удалены: {', '.join(sorted(removed))}")

            log_func(f"🔄 bitget {market}: ротация завершена, активных {len(new_active)}")

        except Exception as e:
            log_func(f"❌ Ошибка в periodic_refresh(bitget {market}): {e}")


# ==========================================
# ГЛАВНАЯ ФУНКЦИЯ
# ==========================================
async def main_async(log_func=print):
    """Главная асинхронная функция — запускает все задачи"""
    global bitget_futures_symbols, bitget_spot_symbols

    log_func("🚀 Запуск Bitget Async Monitor...")

    # Инициализация начальных списков монет
    futures_candidates = await get_top_symbols_async('swap')
    spot_candidates = await get_top_symbols_async('spot')

    active_futures = []
    active_spot = []

    for symbol in futures_candidates[:30]:
        saved_count = await init_order_book_async(symbol, 'futures', log_func)
        if saved_count > 0:
            active_futures.append(symbol)
            log_func(f"✅ bitget futures {symbol}: принят (плотностей: {saved_count})")

    for symbol in spot_candidates[:30]:
        saved_count = await init_order_book_async(symbol, 'spot', log_func)
        if saved_count > 0:
            active_spot.append(symbol)
            log_func(f"✅ bitget spot {symbol}: принят (плотностей: {saved_count})")

    bitget_futures_symbols = active_futures
    bitget_spot_symbols = active_spot

    log_func(f"✅ Bitget Async Monitor инициализирован: {len(active_futures)} futures, {len(active_spot)} spot")

    # Запуск всех задач параллельно
    tasks = [
        ws_listener('futures', log_func),
        ws_listener('spot', log_func),
        process_queue('futures', log_func),
        process_queue('spot', log_func),
        periodic_refresh('futures', log_func),
        periodic_refresh('spot', log_func),
    ]

    await asyncio.gather(*tasks)


def start_bitget_async_monitor(log_func=print):
    """Синхронная обёртка для запуска из Django"""
    asyncio.run(main_async(log_func))