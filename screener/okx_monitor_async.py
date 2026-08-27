"""
OKX Monitor ASYNC — асинхронная версия
Адаптация по образцу bitget_monitor_async.py / bybit_monitor_async.py
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
okx_futures_order_books = {}
okx_futures_density_timestamps = {}
okx_futures_symbols = []
okx_futures_message_queue = asyncio.Queue(maxsize=10000)
okx_futures_lock = asyncio.Lock()
okx_futures_reconnect_event = asyncio.Event()

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — SPOT
# ==========================================
okx_spot_order_books = {}
okx_spot_density_timestamps = {}
okx_spot_symbols = []
okx_spot_message_queue = asyncio.Queue(maxsize=10000)
okx_spot_lock = asyncio.Lock()
okx_spot_reconnect_event = asyncio.Event()

# URLs — У OKX один и тот же WS URL для futures и spot!
OKX_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
OKX_FUTURES_REST_URL = "https://www.okx.com/api/v5/market/books?instId={}-USDT-SWAP&sz=200"
OKX_SPOT_REST_URL = "https://www.okx.com/api/v5/market/books?instId={}-USDT&sz=200"

# Rate limiting
last_sync_time = {}

MIN_AGE_SECONDS = 180
CACHE_TTL = 900

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
    """Синхронная функция для ccxt"""
    try:
        exchange = ccxt.okx({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': market_type}
        })
        tickers = exchange.fetch_tickers()

        # OKX специфика: для swap volCcy24h в базовой валюте, пересчитываем в USDT
        if market_type == 'swap':
            for symbol, data in tickers.items():
                if not (data.get('quoteVolume') or 0):
                    try:
                        info = data.get('info', {}) or {}
                        last = float(data.get('last') or 0)
                        vol_ccy = float(info.get('volCcy24h') or 0)
                        data['quoteVolume'] = vol_ccy * last
                    except Exception:
                        pass

        clean_fn = coin_selection.clean_swap if market_type == 'swap' else coin_selection.clean_spot
        coin_selection.update_volume_history(tickers, clean_fn)

        candidates = coin_selection.select_candidates(
            tickers, clean_fn, limit=60,
            log_func=lambda msg: print(msg)
        )
        return candidates[:30]
    except Exception as e:
        print(f"❌ Ошибка fetch_top_symbols(okx {market_type}): {e}")
        return []


async def get_top_symbols_async(market_type='swap'):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_top_symbols_sync, market_type)


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
            log_func(f"⚠️ okx {market} {symbol}: пустой стакан")
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
        log_func(f"✅ okx {market} Стакан {symbol}: {len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}")
        return saved_count

    except Exception as e:
        log_func(f"❌ init_order_book_async(okx {market} {symbol}): {e}")
        return 0


# ==========================================
# СИНХРОНИЗАЦИЯ В REDIS (async)
# ==========================================
async def sync_to_cache_async(symbol, market='futures', log_func=print):
    try:
        if market == 'futures':
            async with okx_futures_lock:
                book = okx_futures_order_books.get(symbol, {})
                ts = okx_futures_density_timestamps.get(symbol, {})
                if not book:
                    return 0
        else:
            async with okx_spot_lock:
                book = okx_spot_order_books.get(symbol, {})
                ts = okx_spot_density_timestamps.get(symbol, {})
                if not book:
                    return 0

        key = f"scalp:{market}:okx:{symbol}"
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
                    'exchange': 'okx'
                })

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, cache.set, key, densities, CACHE_TTL)
        except RuntimeError:
            pass

        if market == 'futures':
            async with okx_futures_lock:
                okx_futures_density_timestamps[symbol] = ts
        else:
            async with okx_spot_lock:
                okx_spot_density_timestamps[symbol] = ts

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
                log_func(f"⚠️ okx {market} heartbeat завершён: {e}")
                return
    except asyncio.CancelledError:
        return


# ==========================================
# WEBSOCKET LISTENER (async) — один URL для futures и spot
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

            log_func(f"🔌 okx {market} WS подключение: {len(symbols)} символов")

            async with websockets.connect(OKX_WS_URL, ping_interval=None, ping_timeout=None) as ws:
                heartbeat_task = asyncio.create_task(ws_heartbeat(ws, market, log_func))

                try:
                    # Подписка OKX: {"op":"subscribe","args":[{"channel":"books","instId":"BTC-USDT-SWAP"}]}
                    args = [{"channel": "books", "instId": f"{s}{inst_id_suffix}"} for s in symbols]
                    await ws.send(json.dumps({"op": "subscribe", "args": args}))
                    log_func(f"✅ okx {market} WS подписан на {len(symbols)} символов")

                    while True:
                        if reconnect_event.is_set():
                            reconnect_event.clear()
                            log_func(f"🔄 okx {market}: сигнал переподключения получен")
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
            log_func(f"⚠️ okx {market} WS закрыт, переподключение через 3 сек")
            await asyncio.sleep(3)
        except Exception as e:
            log_func(f"❌ okx {market} WS ошибка: {e}, переподключение через 5 сек")
            await asyncio.sleep(5)


# ==========================================
# ОБРАБОТКА ОЧЕРЕДИ (async)
# ==========================================
async def process_queue(market='futures', log_func=print):
    """Обработка очереди. OKX: futures имеют instId с '-USDT-SWAP', spot только '-USDT'"""
    queue = okx_futures_message_queue if market == 'futures' else okx_spot_message_queue

    while True:
        try:
            message = await queue.get()

            # OKX шлёт "pong" строкой, не JSON
            if message == 'pong':
                continue

            data = json.loads(message)

            if 'arg' not in data or 'data' not in data:
                continue

            arg = data.get('arg', {})
            if arg.get('channel') != 'books':
                continue

            inst_id = arg.get('instId', '')

            # Различаем futures и spot по instId
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
    """Обработка снапшота — полная замена стакана"""
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
            new_ts = {}
            for p in new_bids:
                if p in old_ts:
                    new_ts[p] = old_ts[p]
            for p in new_asks:
                if p in old_ts:
                    new_ts[p] = old_ts[p]

            okx_futures_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            okx_futures_density_timestamps[symbol] = new_ts
    else:
        async with okx_spot_lock:
            old_ts = okx_spot_density_timestamps.get(symbol, {})
            new_ts = {}
            for p in new_bids:
                if p in old_ts:
                    new_ts[p] = old_ts[p]
            for p in new_asks:
                if p in old_ts:
                    new_ts[p] = old_ts[p]

            okx_spot_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            okx_spot_density_timestamps[symbol] = new_ts

    await sync_to_cache_async(symbol, market, log_func)


async def handle_update_async(symbol, bids_delta, asks_delta, market, log_func):
    """Обработка дельты — обновление стакана"""
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
                okx_spot_density_timestamps[symbol] = ts

    # Rate limit: sync раз в 3 секунды
    key = f"okx:{market}:{symbol}"
    now = time.time()
    if key not in last_sync_time or (now - last_sync_time[key]) >= 3:
        await sync_to_cache_async(symbol, market, log_func)
        last_sync_time[key] = now


# ==========================================
# ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ СПИСКОВ
# ==========================================
async def periodic_refresh(market='futures', log_func=print):
    """Периодическое обновление списка монет каждые 5 минут"""
    global okx_futures_symbols, okx_spot_symbols

    while True:
        await asyncio.sleep(300)  # 5 минут

        try:
            market_type = 'swap' if market == 'futures' else 'spot'
            candidates = await get_top_symbols_async(market_type)

            old_symbols = set(okx_futures_symbols if market == 'futures' else okx_spot_symbols)

            new_active = []
            TARGET = 30

            # ШАГ 1: Сохраняем ВСЕ старые монеты
            for symbol in old_symbols:
                if len(new_active) >= TARGET:
                    break
                new_active.append(symbol)

            # ШАГ 2: Добавляем новых кандидатов если есть место
            for symbol in candidates:
                if len(new_active) >= TARGET:
                    break
                if symbol in new_active:
                    continue

                saved_count = await init_order_book_async(symbol, market, log_func)
                if saved_count > 0:
                    new_active.append(symbol)
                    log_func(f"✅ okx {market} {symbol}: добавлен (плотностей: {saved_count})")
                else:
                    log_func(f"⚠️ okx {market} {symbol}: пропущен (нет плотностей)")

            if market == 'futures':
                removed = old_symbols - set(new_active)
                okx_futures_symbols = new_active
                if removed:
                    async with okx_futures_lock:
                        for sym in removed:
                            okx_futures_order_books.pop(sym, None)
                            okx_futures_density_timestamps.pop(sym, None)
                    log_func(f"🗑️ okx futures удалены: {', '.join(sorted(removed))}")

                okx_futures_reconnect_event.set()
            else:
                removed = old_symbols - set(new_active)
                okx_spot_symbols = new_active
                if removed:
                    async with okx_spot_lock:
                        for sym in removed:
                            okx_spot_order_books.pop(sym, None)
                            okx_spot_density_timestamps.pop(sym, None)
                    log_func(f"🗑️ okx spot удалены: {', '.join(sorted(removed))}")

                okx_spot_reconnect_event.set()

            log_func(f"🔄 okx {market}: ротация завершена, активных {len(new_active)}")

        except Exception as e:
            log_func(f"❌ Ошибка в periodic_refresh(okx {market}): {e}")


# ==========================================
# ГЛАВНАЯ ФУНКЦИЯ
# ==========================================
async def main_async(log_func=print):
    """Главная асинхронная функция"""
    global okx_futures_symbols, okx_spot_symbols

    log_func("🚀 Запуск OKX Async Monitor...")

    futures_candidates = await get_top_symbols_async('swap')
    spot_candidates = await get_top_symbols_async('spot')

    active_futures = []
    active_spot = []

    for symbol in futures_candidates[:30]:
        saved_count = await init_order_book_async(symbol, 'futures', log_func)
        if saved_count > 0:
            active_futures.append(symbol)
            log_func(f"✅ okx futures {symbol}: принят (плотностей: {saved_count})")

    for symbol in spot_candidates[:30]:
        saved_count = await init_order_book_async(symbol, 'spot', log_func)
        if saved_count > 0:
            active_spot.append(symbol)
            log_func(f"✅ okx spot {symbol}: принят (плотностей: {saved_count})")

    okx_futures_symbols = active_futures
    okx_spot_symbols = active_spot

    log_func(f"✅ OKX Async Monitor инициализирован: {len(active_futures)} futures, {len(active_spot)} spot")

    tasks = [
        ws_listener('futures', log_func),
        ws_listener('spot', log_func),
        process_queue('futures', log_func),
        process_queue('spot', log_func),
        periodic_refresh('futures', log_func),
        periodic_refresh('spot', log_func),
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