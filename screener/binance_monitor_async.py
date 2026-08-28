"""
Binance Monitor ASYNC — асинхронная версия
Сохранена вся специфика Binance:
  - REST инициализация через ccxt (fetch_order_book)
  - Ключи Redis БЕЗ имени биржи (для совместимости): scalp:futures:{symbol}, scalp:spot:{symbol}
  - TARGET=20 при старте, TARGET=30 при ротации
  - Имена переменных futures_symbols/spot_symbols (как в оригинале)
"""
import asyncio
import json
import time
import websockets
from django.core.cache import cache
import ccxt
from . import coin_selection

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — FUTURES
# ==========================================
binance_futures_order_books = {}
binance_futures_density_timestamps = {}
futures_symbols = []  # ← оставил как в оригинале (не binance_futures_symbols)
binance_futures_message_queue = asyncio.Queue(maxsize=10000)
binance_futures_lock = asyncio.Lock()
binance_futures_reconnect_event = asyncio.Event()

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — SPOT
# ==========================================
binance_spot_order_books = {}
binance_spot_density_timestamps = {}
spot_symbols = []  # ← оставил как в оригинале
binance_spot_message_queue = asyncio.Queue(maxsize=10000)
binance_spot_lock = asyncio.Lock()
binance_spot_reconnect_event = asyncio.Event()

# URLs
FUTURES_WS_URL = "wss://fstream.binance.com/ws"
SPOT_WS_URL = "wss://stream.binance.com:9443/ws"

# Rate limiting
last_sync_time = {}

MIN_AGE_SECONDS = 180
CACHE_TTL = 900
SYNC_INTERVAL = 3


# ==========================================
# ТОП МОНЕТ (через ccxt в executor)
# ==========================================
def _fetch_top_symbols_sync(market='futures'):
    try:
        ccxt_market = 'future' if market == 'futures' else 'spot'  # Binance специфика: 'future'!
        exchange = ccxt.binance({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': ccxt_market}
        })
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
# ИНИЦИАЛИЗАЦИЯ СТАКАНА через ccxt.fetch_order_book
# ==========================================
def _init_order_book_sync(symbol, market):
    """Синхронная функция — использует ccxt для инициализации"""
    try:
        ccxt_market = 'future' if market == 'futures' else 'spot'
        exchange = ccxt.binance({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': ccxt_market}
        })
        exchange.load_markets()

        if market == 'futures':
            ccxt_symbol = f"{symbol}/USDT:USDT"
        else:
            ccxt_symbol = f"{symbol}/USDT"

        ob = exchange.fetch_order_book(ccxt_symbol, limit=100)

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
# ВНИМАНИЕ: Ключи БЕЗ имени биржи! scalp:futures:{symbol}, scalp:spot:{symbol}
# ==========================================
async def sync_to_cache_async(symbol, market='futures', log_func=print):
    try:
        if market == 'futures':
            async with binance_futures_lock:
                book = binance_futures_order_books.get(symbol, {})
                ts = binance_futures_density_timestamps.get(symbol, {})
                if not book:
                    return 0
            key = f"scalp:futures:{symbol}"  # ← БЕЗ :binance:
        else:
            async with binance_spot_lock:
                book = binance_spot_order_books.get(symbol, {})
                ts = binance_spot_density_timestamps.get(symbol, {})
                if not book:
                    return 0
            key = f"scalp:spot:{symbol}"  # ← БЕЗ :binance:

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
                    'exchange': 'binance'
                })

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, cache.set, key, densities, CACHE_TTL)
        except RuntimeError:
            pass

        if market == 'futures':
            async with binance_futures_lock:
                binance_futures_density_timestamps[symbol] = ts
        else:
            async with binance_spot_lock:
                binance_spot_density_timestamps[symbol] = ts

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_to_cache_async(binance {market} {symbol}): {e}")
        return 0


# ==========================================
# WEBSOCKET LISTENER — Binance не требует клиентский heartbeat
# Используем встроенные ping-фреймы библиотеки websockets
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

            # Binance не требует heartbeat от клиента — сервер сам шлёт ping
            # ping_interval=20 — встроенные протокольные пинги от библиотеки websockets
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                try:
                    # Подписка: {"method":"SUBSCRIBE","params":["btcusdt@depth@100ms",...],"id":1}
                    streams = [f"{s.lower()}usdt@depth@100ms" for s in symbols]
                    subscribe_msg = {
                        "method": "SUBSCRIBE",
                        "params": streams,
                        "id": 1
                    }
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
# Binance шлёт как {"data":{"s":...}} (combined stream), так и {"s":...} напрямую
# ==========================================
async def process_queue(market='futures', log_func=print):
    queue = binance_futures_message_queue if market == 'futures' else binance_spot_message_queue

    while True:
        try:
            message = await queue.get()
            data = json.loads(message)

            # Определяем symbol и уровни из двух возможных форматов
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

    # Rate limit: 3 сек
    key = f"binance:{market}:{symbol}"
    now = time.time()
    if key not in last_sync_time or (now - last_sync_time[key]) >= SYNC_INTERVAL:
        await sync_to_cache_async(symbol, market, log_func)
        last_sync_time[key] = now


# ==========================================
# ПЕРИОДИЧЕСКАЯ РОТАЦИЯ
# ==========================================
async def periodic_refresh(log_func=print):
    global futures_symbols, spot_symbols

    while True:
        await asyncio.sleep(300)  # 5 минут

        try:
            # --- Futures ротация ---
            candidates_f = await get_top_symbols_async('futures')
            old_symbols = set(futures_symbols)

            new_active = []
            TARGET = 30

            for symbol in old_symbols:
                if len(new_active) >= TARGET:
                    break
                new_active.append(symbol)

            for symbol in candidates_f:
                if len(new_active) >= TARGET:
                    break
                if symbol in new_active:
                    continue
                saved_count = await init_order_book_async(symbol, 'futures', log_func)
                if saved_count > 0:
                    new_active.append(symbol)
                    log_func(f"✅ binance futures {symbol}: добавлен (плотностей: {saved_count})")
                else:
                    log_func(f"⚠️ binance futures {symbol}: пропущен (нет плотностей)")

            removed = old_symbols - set(new_active)
            added = set(new_active) - old_symbols
            futures_symbols = new_active
            if removed:
                async with binance_futures_lock:
                    for sym in removed:
                        binance_futures_order_books.pop(sym, None)
                        binance_futures_density_timestamps.pop(sym, None)
                log_func(f"🗑️ binance futures удалены: {', '.join(sorted(removed))}")

            if removed or added:
                binance_futures_reconnect_event.set()
                log_func(f"🔄 binance futures: список изменился (+{len(added)} -{len(removed)}), переподключение")
            else:
                log_func(f"✅ binance futures: список не изменился ({len(new_active)} монет)")

            # --- Spot ротация ---
            candidates_s = await get_top_symbols_async('spot')
            old_symbols = set(spot_symbols)

            new_active = []
            TARGET = 30

            for symbol in old_symbols:
                if len(new_active) >= TARGET:
                    break
                new_active.append(symbol)

            for symbol in candidates_s:
                if len(new_active) >= TARGET:
                    break
                if symbol in new_active:
                    continue
                saved_count = await init_order_book_async(symbol, 'spot', log_func)
                if saved_count > 0:
                    new_active.append(symbol)
                    log_func(f"✅ binance spot {symbol}: добавлен (плотностей: {saved_count})")
                else:
                    log_func(f"⚠️ binance spot {symbol}: пропущен (нет плотностей)")

            removed = old_symbols - set(new_active)
            added = set(new_active) - old_symbols
            spot_symbols = new_active
            if removed:
                async with binance_spot_lock:
                    for sym in removed:
                        binance_spot_order_books.pop(sym, None)
                        binance_spot_density_timestamps.pop(sym, None)
                log_func(f"🗑️ binance spot удалены: {', '.join(sorted(removed))}")

            if removed or added:
                binance_spot_reconnect_event.set()
                log_func(f"🔄 binance spot: список изменился (+{len(added)} -{len(removed)}), переподключение")
            else:
                log_func(f"✅ binance spot: список не изменился ({len(new_active)} монет)")

        except Exception as e:
            log_func(f"❌ Ошибка в periodic_refresh(binance): {e}")


# ==========================================
# ГЛАВНАЯ ФУНКЦИЯ
# ==========================================
async def main_async(log_func=print):
    global futures_symbols, spot_symbols

    log_func("🚀 Запуск Binance Async Monitor...")

    futures_candidates = await get_top_symbols_async('futures')
    spot_candidates = await get_top_symbols_async('spot')

    active_futures = []
    active_spot = []

    TARGET_START = 20  # При старте меньше — как в оригинале

    for symbol in futures_candidates[:TARGET_START]:
        saved_count = await init_order_book_async(symbol, 'futures', log_func)
        if saved_count > 0:
            active_futures.append(symbol)
            log_func(f"✅ binance futures {symbol}: принят (плотностей: {saved_count})")

    for symbol in spot_candidates[:TARGET_START]:
        saved_count = await init_order_book_async(symbol, 'spot', log_func)
        if saved_count > 0:
            active_spot.append(symbol)
            log_func(f"✅ binance spot {symbol}: принят (плотностей: {saved_count})")

    futures_symbols = active_futures
    spot_symbols = active_spot

    log_func(f"✅ Binance Async Monitor инициализирован: {len(active_futures)} futures, {len(active_spot)} spot")

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
    finally:
        pass