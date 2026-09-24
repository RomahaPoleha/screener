"""
MEXC Monitor ASYNC — асинхронная версия (АБСОЛЮТНО ФИНАЛЬНАЯ)
Критические исправления для MEXC Spot:
  1. ИСПРАВЛЕНО имя канала подписки на официальное: spot@public.aggre.depth.v3.api.pb@100ms@SYMBOL
  2. ИСПРАВЛЕН парсинг ответа: данные теперь берутся из ключа 'publicAggreDepths' (а не 'd' или 'data')
  3. Сохранены все предыдущие исправления (лимит 30 символов, PING верхним регистром, защита от bytes).
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
mexc_futures_order_books = {}
mexc_futures_density_timestamps = {}
mexc_futures_symbols = []
mexc_futures_message_queue = asyncio.Queue(maxsize=10000)
mexc_futures_lock = asyncio.Lock()
mexc_futures_reconnect_event = asyncio.Event()

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — SPOT
# ==========================================
mexc_spot_order_books = {}
mexc_spot_density_timestamps = {}
mexc_spot_symbols = []
mexc_spot_message_queue = asyncio.Queue(maxsize=10000)
mexc_spot_lock = asyncio.Lock()
mexc_spot_reconnect_event = asyncio.Event()

# URLs
MEXC_FUTURES_WS_URL = "wss://contract.mexc.com/edge"
MEXC_SPOT_WS_URL = "wss://wbs-api.mexc.com/ws"
MEXC_FUTURES_REST_URL = "https://contract.mexc.com/api/v1/contract/depth/{}_USDT?limit=100"
MEXC_SPOT_REST_URL = "https://api.mexc.com/api/v3/depth?symbol={}USDT&limit=100"

# Rate limiting
last_sync_time = {}

MIN_AGE_SECONDS = 180
CACHE_TTL = 30
SYNC_INTERVAL = 3

_http_client = None


async def get_http_client():
    global _http_client
    if _http_client is None:
        _http_client = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={'User-Agent': 'Mozilla/5.0'}
        )
    return _http_client


# ==========================================
# ТОП МОНЕТ
# ==========================================
def _fetch_top_symbols_sync(market='swap', log_func=print):
    try:
        exchange = ccxt.mexc({
            'enableRateLimit': True,
            'timeout': 15000,
            'options': {'defaultType': market}
        })
        tickers = exchange.fetch_tickers()

        if market == 'swap':
            for symbol, data in tickers.items():
                if not (data.get('quoteVolume') or 0):
                    try:
                        info = data.get('info', {})
                        amount24 = float(info.get('amount24') or 0)
                        if amount24 > 0:
                            data['quoteVolume'] = amount24
                        else:
                            vol_contracts = float(info.get('volume24') or info.get('volume_24h') or 0)
                            last_price = float(data.get('last') or info.get('lastPrice') or 0)
                            data['quoteVolume'] = vol_contracts * last_price
                    except Exception:
                        pass

        clean_fn = coin_selection.clean_swap if market == 'swap' else coin_selection.clean_spot
        coin_selection.update_volume_history(tickers, clean_fn)

        candidates = coin_selection.select_candidates(
            tickers, clean_fn, limit=60,
            log_func=log_func
        )
        return candidates[:30]
    except Exception as e:
        log_func(f"❌ Ошибка fetch_top_symbols(mexc {market}): {e}")
        return []


async def get_top_symbols_async(market='swap', log_func=print):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_top_symbols_sync, market, log_func)


# ==========================================
# БЕЛЫЙ СПИСОК
# ==========================================
STABLE_COINS_LIMIT = 10
stable_futures_symbols = []
stable_spot_symbols = []


def _fetch_stable_coins_sync(market='swap', limit=10):
    try:
        exchange = ccxt.mexc({
            'enableRateLimit': True,
            'timeout': 15000,
            'options': {'defaultType': market}
        })
        tickers = exchange.fetch_tickers()

        if market == 'swap':
            for symbol, data in tickers.items():
                if not (data.get('quoteVolume') or 0):
                    try:
                        info = data.get('info', {})
                        amount24 = float(info.get('amount24') or 0)
                        if amount24 > 0:
                            data['quoteVolume'] = amount24
                        else:
                            vol_contracts = float(info.get('volume24') or info.get('volume_24h') or 0)
                            last_price = float(data.get('last') or info.get('lastPrice') or 0)
                            data['quoteVolume'] = vol_contracts * last_price
                    except Exception:
                        pass

        coins_with_volume = []
        for symbol, data in tickers.items():
            if market == 'swap':
                if '_USDT' in symbol:
                    clean_symbol = symbol.replace('_USDT', '')
                elif ':USDT' in symbol:
                    clean_symbol = symbol.split(':')[0].split('/')[0]
                elif '/USDT' in symbol:
                    clean_symbol = symbol.replace('/USDT', '')
                else:
                    continue
            else:
                if '/USDT' not in symbol:
                    continue
                clean_symbol = symbol.replace('/USDT', '')

            volume = data.get('quoteVolume') or 0
            if volume < 100000:
                continue

            if not clean_symbol or len(clean_symbol) < 2 or len(clean_symbol) > 15:
                continue
            if not clean_symbol.replace('_', '').isalnum():
                continue

            coins_with_volume.append((clean_symbol, volume))

        coins_with_volume.sort(key=lambda x: x[1], reverse=True)
        return [s for s, v in coins_with_volume[:limit]]

    except Exception as e:
        print(f"❌ Ошибка _fetch_stable_coins(mexc {market}): {e}")
        return []


async def get_stable_coins_async(market='swap', limit=10):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_stable_coins_sync, market, limit)


# ==========================================
# ИНИЦИАЛИЗАЦИЯ СТАКАНОВ (async HTTP)
# ==========================================
async def init_order_book_async(symbol, market='futures', log_func=print):
    try:
        url = (MEXC_FUTURES_REST_URL if market == 'futures' else MEXC_SPOT_REST_URL).format(symbol)

        client = await get_http_client()
        async with client.get(url) as resp:
            if resp.status != 200:
                log_func(f"⚠️ mexc {market} {symbol}: HTTP ошибка {resp.status}")
                return False, 0
            data = await resp.json()

        if market == 'futures':
            if not isinstance(data, dict) or data.get('success') is False:
                log_func(f"⚠️ mexc futures {symbol}: API ошибка: {str(data)[:200]}")
                return False, 0
            inner = data.get('data') or {}
            raw_bids = inner.get('bids') or []
            raw_asks = inner.get('asks') or []
        else:
            if isinstance(data, list) or not isinstance(data, dict):
                log_func(f"⚠️ mexc spot {symbol}: Неожиданный формат ответа REST")
                return False, 0
            if 'code' in data and data.get('code') != 0:
                log_func(f"⚠️ mexc spot {symbol}: code={data.get('code')}")
                return False, 0
            raw_bids = data.get('bids') or []
            raw_asks = data.get('asks') or []

        bids = {}
        asks = {}

        for row in raw_bids:
            try:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    price = float(row[0])
                    qty = abs(float(row[1]))
                    if price > 0 and qty > 0:
                        bids[price] = qty
            except Exception:
                continue

        for row in raw_asks:
            try:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    price = float(row[0])
                    qty = abs(float(row[1]))
                    if price > 0 and qty > 0:
                        asks[price] = qty
            except Exception:
                continue

        if not bids and not asks:
            log_func(f"⚠️ mexc {market} {symbol}: REST вернул пустой стакан")
            return True, 0

        if market == 'futures':
            async with mexc_futures_lock:
                mexc_futures_order_books[symbol] = {'bids': bids, 'asks': asks}
                mexc_futures_density_timestamps[symbol] = {}
        else:
            async with mexc_spot_lock:
                mexc_spot_order_books[symbol] = {'bids': bids, 'asks': asks}
                mexc_spot_density_timestamps[symbol] = {}

        saved_count = await sync_to_cache_async(symbol, market, log_func)
        log_func(f"✅ mexc {market} Стакан {symbol}: {len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}")
        return True, saved_count

    except Exception as e:
        log_func(f"❌ init_order_book_async(mexc {market} {symbol}): {e}")
        return False, 0


# ==========================================
# СИНХРОНИЗАЦИЯ В REDIS
# ==========================================
async def sync_to_cache_async(symbol, market='futures', log_func=print):
    try:
        if market == 'futures':
            async with mexc_futures_lock:
                book = mexc_futures_order_books.get(symbol, {})
                ts = mexc_futures_density_timestamps.get(symbol, {})
                if not book:
                    return 0
        else:
            async with mexc_spot_lock:
                book = mexc_spot_order_books.get(symbol, {})
                ts = mexc_spot_density_timestamps.get(symbol, {})
                if not book:
                    return 0

        key = f"scalp:{market}:mexc:{symbol}"
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
                    'exchange': 'mexc'
                })

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, cache.set, key, densities, CACHE_TTL)
        except RuntimeError:
            pass

        if market == 'futures':
            async with mexc_futures_lock:
                mexc_futures_density_timestamps[symbol] = ts
        else:
            async with mexc_spot_lock:
                mexc_spot_density_timestamps[symbol] = ts

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_to_cache_async(mexc {market} {symbol}): {e}")
        return 0


# ==========================================
# HEARTBEAT
# ==========================================
async def ws_heartbeat(ws, market='futures', log_func=print):
    try:
        while True:
            await asyncio.sleep(15)
            try:
                ping_msg = {"method": "PING"} if market == 'spot' else {"method": "ping"}
                await ws.send(json.dumps(ping_msg))
            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                log_func(f"⚠️ mexc {market} heartbeat завершён: {e}")
                return
    except asyncio.CancelledError:
        return


# ==========================================
# WEBSOCKET LISTENER
# ==========================================
async def ws_listener(market='futures', log_func=print):
    global mexc_futures_symbols, mexc_spot_symbols

    reconnect_event = mexc_futures_reconnect_event if market == 'futures' else mexc_spot_reconnect_event
    ws_url = MEXC_FUTURES_WS_URL if market == 'futures' else MEXC_SPOT_WS_URL

    while True:
        try:
            symbols = mexc_futures_symbols if market == 'futures' else mexc_spot_symbols

            if market == 'spot' and len(symbols) > 30:
                log_func(f"⚠️ mexc spot: обрезка списка с {len(symbols)} до 30 символов (лимит MEXC)")
                symbols = symbols[:30]
                mexc_spot_symbols = symbols

            if not symbols:
                log_func(f"⚠️ mexc {market}: список символов пуст, ожидание...")
                await asyncio.sleep(5)
                continue

            log_func(f"🔌 mexc {market} WS подключение: {len(symbols)} символов")

            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                heartbeat_task = asyncio.create_task(ws_heartbeat(ws, market, log_func))

                try:
                    if market == 'futures':
                        for symbol in symbols:
                            msg = {
                                "method": "sub.depth",
                                "param": {"symbol": f"{symbol}_USDT"}
                            }
                            await ws.send(json.dumps(msg))
                    else:
                        # ИСПРАВЛЕНИЕ: Официальный формат канала MEXC Spot v3 с 100ms обновлением
                        params = [f"spot@public.aggre.depth.v3.api.pb@100ms@{s.upper()}USDT" for s in symbols]
                        msg = {"method": "SUBSCRIPTION", "params": params}
                        await ws.send(json.dumps(msg))
                        log_func(f"📤 mexc spot отправлена подписка: {params[:2]}...")

                    log_func(f"✅ mexc {market} WS подписан на {len(symbols)} символов")

                    while True:
                        if reconnect_event.is_set():
                            reconnect_event.clear()
                            log_func(f"🔄 mexc {market}: сигнал переподключения получен")
                            break

                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue

                        queue = mexc_futures_message_queue if market == 'futures' else mexc_spot_message_queue
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
            log_func(f"⚠️ mexc {market} WS закрыт, переподключение через 3 сек")
            await asyncio.sleep(3)
        except Exception as e:
            log_func(f"❌ mexc {market} WS ошибка: {e}, переподключение через 5 сек")
            await asyncio.sleep(5)


# ==========================================
# ВСПОМОГАТЕЛЬНЫЙ ПАРСЕР
# ==========================================
def parse_depth_levels(levels):
    result = {}
    for row in levels:
        try:
            if isinstance(row, dict):
                p = float(row.get('price', 0))
                q = abs(float(row.get('quantity', 0)))
            elif isinstance(row, (list, tuple)) and len(row) >= 2:
                p = float(row[0])
                q = abs(float(row[1]))
            else:
                continue

            if p > 0 and q > 0:
                result[p] = q
        except Exception:
            continue
    return result


# ==========================================
# ОБРАБОТКА ОЧЕРЕДИ
# ==========================================
async def process_queue(market='futures', log_func=print):
    queue = mexc_futures_message_queue if market == 'futures' else mexc_spot_message_queue

    while True:
        try:
            message = await queue.get()

            if isinstance(message, bytes):
                continue

            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue

            if market == 'futures':
                channel = data.get('channel', '')
                if channel != 'push.depth':
                    continue

                sym = data.get('symbol', '')
                symbol = sym[:-5] if sym.endswith('_USDT') else sym

                inner = data.get('data') or {}
                raw_bids = inner.get('bids') or []
                raw_asks = inner.get('asks') or []

                if not (raw_bids or raw_asks):
                    continue

                await handle_update_async(symbol, raw_bids, raw_asks, market, log_func)

            else:
                # Spot: ИСПРАВЛЕНИЕ парсинга ответа MEXC Protobuf
                channel = data.get('c') or data.get('channel', '')
                if 'depth' not in channel.lower() or '.pb' not in channel.lower():
                    continue

                sym = (data.get('s') or data.get('symbol', '')).upper()
                symbol = sym[:-4] if sym.endswith('USDT') else sym

                # ИСПРАВЛЕНИЕ: данные в MEXC Spot v3 лежат в publicAggreDepths или publicLimitDepths
                inner = data.get('publicAggreDepths') or data.get('publicLimitDepths') or data.get('d') or data.get('data') or {}
                raw_bids = inner.get('bids') or []
                raw_asks = inner.get('asks') or []

                if not (raw_bids or raw_asks):
                    continue

                await handle_update_async(symbol, raw_bids, raw_asks, market, log_func)

        except Exception as e:
            log_func(f"❌ mexc {market} process_queue ошибка: {e}")


async def handle_update_async(symbol, bids_delta, asks_delta, market, log_func):
    new_bids = parse_depth_levels(bids_delta)
    new_asks = parse_depth_levels(asks_delta)

    if not new_bids and not new_asks:
        return

    if market == 'futures':
        async with mexc_futures_lock:
            if symbol not in mexc_futures_order_books:
                return
            book = mexc_futures_order_books[symbol]
            ts = mexc_futures_density_timestamps.get(symbol, {})
            changed = False

            for price, qty in new_bids.items():
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

            for price, qty in new_asks.items():
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

            if changed:
                mexc_futures_density_timestamps[symbol] = ts
    else:
        async with mexc_spot_lock:
            if symbol not in mexc_spot_order_books:
                return
            book = mexc_spot_order_books[symbol]
            ts = mexc_spot_density_timestamps.get(symbol, {})
            changed = False

            for price, qty in new_bids.items():
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

            for price, qty in new_asks.items():
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

            if changed:
                mexc_spot_density_timestamps[symbol] = ts

    key = f"mexc:{market}:{symbol}"
    now = time.time()
    if key not in last_sync_time or (now - last_sync_time[key]) >= SYNC_INTERVAL:
        await sync_to_cache_async(symbol, market, log_func)
        last_sync_time[key] = now


# ==========================================
# ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ СПИСКОВ
# ==========================================
async def periodic_refresh(market='futures', log_func=print):
    global mexc_futures_symbols, mexc_spot_symbols

    while True:
        await asyncio.sleep(300)

        try:
            market_type = 'swap' if market == 'futures' else 'spot'
            candidates = await get_top_symbols_async(market_type, log_func)

            old_symbols = set(mexc_futures_symbols if market == 'futures' else mexc_spot_symbols)
            stable_symbols = stable_futures_symbols if market == 'futures' else stable_spot_symbols

            new_active = []
            TARGET = 30

            for symbol in stable_symbols:
                if len(new_active) >= TARGET:
                    break
                if symbol in old_symbols:
                    new_active.append(symbol)
                else:
                    success, saved_count = await init_order_book_async(symbol, market, log_func)
                    if success:
                        new_active.append(symbol)
                        if saved_count > 0:
                            log_func(f"✅ mexc {market} {symbol}: добавлен (плотностей: {saved_count}) [стабильная]")
                        else:
                            log_func(f"⚠️ mexc {market} {symbol}: добавлен, но начальных плотностей не найдено")

            for symbol in candidates:
                if len(new_active) >= TARGET:
                    break
                if symbol in new_active:
                    continue
                success, saved_count = await init_order_book_async(symbol, market, log_func)
                if success:
                    new_active.append(symbol)
                    if saved_count > 0:
                        log_func(f"✅ mexc {market} {symbol}: добавлен (плотностей: {saved_count})")
                    else:
                        log_func(f"⚠️ mexc {market} {symbol}: добавлен, но начальных плотностей не найдено")

            if market == 'futures':
                removed = old_symbols - set(new_active)
                added = set(new_active) - old_symbols
                mexc_futures_symbols = new_active
                if removed:
                    async with mexc_futures_lock:
                        for sym in removed:
                            mexc_futures_order_books.pop(sym, None)
                            mexc_futures_density_timestamps.pop(sym, None)
                    log_func(f"🗑️ mexc futures удалены: {', '.join(sorted(removed))}")

                if removed or added:
                    mexc_futures_reconnect_event.set()
                    log_func(f"🔄 mexc futures: список изменился (+{len(added)} -{len(removed)}), переподключение")
                else:
                    log_func(f"✅ mexc futures: список не изменился ({len(new_active)} монет)")
            else:
                removed = old_symbols - set(new_active)
                added = set(new_active) - old_symbols
                mexc_spot_symbols = new_active
                if removed:
                    async with mexc_spot_lock:
                        for sym in removed:
                            mexc_spot_order_books.pop(sym, None)
                            mexc_spot_density_timestamps.pop(sym, None)
                    log_func(f"🗑️ mexc spot удалены: {', '.join(sorted(removed))}")

                if removed or added:
                    mexc_spot_reconnect_event.set()
                    log_func(f"🔄 mexc spot: список изменился (+{len(added)} -{len(removed)}), переподключение")
                else:
                    log_func(f"✅ mexc spot: список не изменился ({len(new_active)} монет)")

        except Exception as e:
            log_func(f"❌ Ошибка в periodic_refresh(mexc {market}): {e}")


# ==========================================
# ГЛАВНАЯ ФУНКЦИЯ
# ==========================================
async def main_async(log_func=print):
    global mexc_futures_symbols, mexc_spot_symbols, stable_futures_symbols, stable_spot_symbols

    log_func("🚀 Запуск MEXC Async Monitor...")

    stable_f = await get_stable_coins_async('swap', STABLE_COINS_LIMIT)
    stable_s = await get_stable_coins_async('spot', STABLE_COINS_LIMIT)
    stable_futures_symbols = stable_f
    stable_spot_symbols = stable_s
    log_func(f"🔒 Белый список futures: {stable_f}")
    log_func(f"🔒 Белый список spot: {stable_s}")

    futures_candidates = await get_top_symbols_async('swap', log_func)
    spot_candidates = await get_top_symbols_async('spot', log_func)

    active_futures = []
    for symbol in stable_f:
        success, saved_count = await init_order_book_async(symbol, 'futures', log_func)
        if success:
            active_futures.append(symbol)
            log_func(f"✅ mexc futures {symbol}: инициализирован (плотностей: {saved_count}) [стабильная]")

    active_spot = []
    for symbol in stable_s:
        success, saved_count = await init_order_book_async(symbol, 'spot', log_func)
        if success:
            active_spot.append(symbol)
            log_func(f"✅ mexc spot {symbol}: инициализирован (плотностей: {saved_count}) [стабильная]")
        else:
            log_func(f"❌ mexc spot {symbol}: не удалось инициализировать через REST")

    for symbol in futures_candidates[:30]:
        if symbol in active_futures:
            continue
        success, saved_count = await init_order_book_async(symbol, 'futures', log_func)
        if success:
            active_futures.append(symbol)
            log_func(f"✅ mexc futures {symbol}: инициализирован (плотностей: {saved_count})")

    for symbol in spot_candidates[:30]:
        if symbol in active_spot:
            continue
        success, saved_count = await init_order_book_async(symbol, 'spot', log_func)
        if success:
            active_spot.append(symbol)
            log_func(f"✅ mexc spot {symbol}: инициализирован (плотностей: {saved_count})")
        else:
            log_func(f"❌ mexc spot {symbol}: не удалось инициализировать через REST")

    mexc_futures_symbols = active_futures
    mexc_spot_symbols = active_spot

    log_func(f"✅ MEXC Async Monitor инициализирован: {len(active_futures)} futures, {len(active_spot)} spot")

    tasks = [
        ws_listener('futures', log_func),
        ws_listener('spot', log_func),
        process_queue('futures', log_func),
        process_queue('spot', log_func),
        periodic_refresh('futures', log_func),
        periodic_refresh('spot', log_func),
    ]

    await asyncio.gather(*tasks)


def start_mexc_async_monitor(log_func=print):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(main_async(log_func))
    except Exception as e:
        log_func(f"❌ MEXC Async Monitor упал: {e}")
        import traceback
        log_func(traceback.format_exc())
    finally:
        pass