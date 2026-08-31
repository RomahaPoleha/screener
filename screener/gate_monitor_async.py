"""
Gate Monitor ASYNC — асинхронная версия
Сохранена вся специфика Gate: два WS URL, серверный time в heartbeat, два формата уровней
"""
import asyncio
import json
import time
import aiohttp
import websockets
from django.core.cache import cache
import ccxt
from . import coin_selection

# Поддержка старых и новых версий ccxt
GateExchange = getattr(ccxt, 'gateio', None) or getattr(ccxt, 'gate', None)
if GateExchange is None:
    raise ImportError("ccxt не поддерживает Gate.io (нет ни 'gateio' ни 'gate')")

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — FUTURES
# ==========================================
gate_futures_order_books = {}
gate_futures_density_timestamps = {}
gate_futures_symbols = []
gate_futures_message_queue = asyncio.Queue(maxsize=10000)
gate_futures_lock = asyncio.Lock()
gate_futures_reconnect_event = asyncio.Event()

# ==========================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ — SPOT
# ==========================================
gate_spot_order_books = {}
gate_spot_density_timestamps = {}
gate_spot_symbols = []
gate_spot_message_queue = asyncio.Queue(maxsize=10000)
gate_spot_lock = asyncio.Lock()
gate_spot_reconnect_event = asyncio.Event()

# URLs — У Gate РАЗНЫЕ WS URL для futures и spot
GATE_FUTURES_WS_URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
GATE_SPOT_WS_URL = "wss://api.gateio.ws/ws/v4/"
GATE_FUTURES_REST_URL = "https://api.gateio.ws/api/v4/futures/usdt/order_book?contract={}_USDT&limit=50"
GATE_SPOT_REST_URL = "https://api.gateio.ws/api/v4/spot/order_book?currency_pair={}_USDT&limit=50"

# Rate limiting — у Gate интервал 10 сек (не 3!)
last_sync_time = {}
gate_spot_last_sync_time = {}

MIN_AGE_SECONDS = 180
CACHE_TTL = 900
SYNC_INTERVAL = 10  # Gate специфика

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
# ТОП МОНЕТ (обобщённая функция)
# ==========================================
def _fetch_top_symbols_sync(market='swap', log_func=print):
    try:
        exchange = GateExchange({
            'enableRateLimit': True,
            'timeout': 15000,
            'options': {'defaultType': market}
        })
        tickers = exchange.fetch_tickers(params={'type': market})

        # Gate swap отдаёт volume в контрактах — пересчитываем в quoteVolume
        if market == 'swap':
            for symbol, data in tickers.items():
                try:
                    info = data.get('info', {})
                    vol_contracts = float(info.get('volume_24h') or 0)
                    last_price = float(data.get('last') or 0)
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
        log_func(f"❌ Ошибка fetch_top_symbols(gate {market}): {e}")
        return []


async def get_top_symbols_async(market='swap', log_func=print):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_top_symbols_sync, market, log_func)

# ==========================================
# БЕЛЫЙ СПИСОК — топ монеты по абсолютному объёму
# ==========================================
STABLE_COINS_LIMIT = 10  # Размер белого списка

# Глобальные переменные для хранения белого списка
stable_futures_symbols = []
stable_spot_symbols = []


def _fetch_stable_coins_sync(market='swap', limit=10):
    """Синхронная функция — топ монет по абсолютному объёму"""
    try:
        exchange = GateExchange({
            'enableRateLimit': True,
            'timeout': 15000,
            'options': {'defaultType': market}
        })
        tickers = exchange.fetch_tickers(params={'type': market})

        # Gate swap отдаёт volume в контрактах — пересчитываем в quoteVolume
        if market == 'swap':
            for symbol, data in tickers.items():
                try:
                    info = data.get('info', {})
                    vol_contracts = float(info.get('volume_24h') or 0)
                    last_price = float(data.get('last') or 0)
                    data['quoteVolume'] = vol_contracts * last_price
                except Exception:
                    pass

        # Собираем монеты с объёмами
        coins_with_volume = []
        for symbol, data in tickers.items():
            # Gate ccxt форматы:
            # - spot: BTC/USDT
            # - swap: может быть BTC_USDT или BTC/USDT:USDT
            if market == 'swap':
                if '_USDT' in symbol:
                    # Формат: BTC_USDT
                    clean_symbol = symbol.replace('_USDT', '')
                elif ':USDT' in symbol:
                    # Формат: BTC/USDT:USDT
                    clean_symbol = symbol.split(':')[0].split('/')[0]
                elif '/USDT' in symbol:
                    # Формат: BTC/USDT
                    clean_symbol = symbol.replace('/USDT', '')
                else:
                    continue
            else:
                if '/USDT' not in symbol:
                    continue
                clean_symbol = symbol.replace('/USDT', '')

            volume = data.get('quoteVolume') or 0
            if volume < 100000:  # Минимальный порог
                continue

            # Валидация
            if not clean_symbol:
                continue
            if len(clean_symbol) < 2 or len(clean_symbol) > 15:
                continue
            if not clean_symbol.replace('_', '').isalnum():
                continue

            coins_with_volume.append((clean_symbol, volume))

        # Сортируем по убыванию объёма и берём топ-N
        coins_with_volume.sort(key=lambda x: x[1], reverse=True)
        return [s for s, v in coins_with_volume[:limit]]

    except Exception as e:
        print(f"❌ Ошибка _fetch_stable_coins(gate {market}): {e}")
        return []


async def get_stable_coins_async(market='swap', limit=10):
    """Асинхронная обёртка — топ монет по абсолютному объёму"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_stable_coins_sync, market, limit)

# ==========================================
# ПАРСИНГ УРОВНЕЙ (два формата: dict и list)
# ==========================================
def parse_levels(levels):
    """Парсит уровни Gate: futures = {p, s}, spot = [price, size]"""
    result = {}
    for row in levels:
        try:
            if isinstance(row, dict):
                p = float(row.get('p', 0))
                q = abs(float(row.get('s', 0)))
            elif isinstance(row, (list, tuple)):
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
# ИНИЦИАЛИЗАЦИЯ СТАКАНОВ (async HTTP)
# ==========================================
async def init_order_book_async(symbol, market='futures', log_func=print):
    try:
        url = (GATE_FUTURES_REST_URL if market == 'futures' else GATE_SPOT_REST_URL).format(symbol)

        client = await get_http_client()
        async with client.get(url) as resp:
            if resp.status != 200:
                log_func(f"⚠️ gate {market} {symbol}: HTTP {resp.status}")
                return 0
            data = await resp.json()

        # Gate специфика: может вернуть список вместо объекта
        if isinstance(data, list):
            log_func(f"⚠️ gate {market} {symbol}: API вернул список")
            return 0
        if not isinstance(data, dict):
            log_func(f"⚠️ gate {market} {symbol}: неожиданный тип {type(data)}")
            return 0

        # Gate ошибка в формате {label, message}
        if 'label' in data or 'message' in data:
            log_func(f"⚠️ gate {market} {symbol}: ошибка API: {data.get('label')} - {data.get('message')}")
            return 0

        if 'asks' not in data and 'bids' not in data:
            log_func(f"⚠️ gate {market} {symbol}: нет asks/bids в ответе")
            return 0

        raw_bids = data.get('bids') or []
        raw_asks = data.get('asks') or []

        bids = parse_levels(raw_bids)
        asks = parse_levels(raw_asks)

        if not bids and not asks:
            log_func(f"⚠️ gate {market} {symbol}: пустой стакан")
            return 0

        if market == 'futures':
            async with gate_futures_lock:
                gate_futures_order_books[symbol] = {'bids': bids, 'asks': asks}
                gate_futures_density_timestamps[symbol] = {}
        else:
            async with gate_spot_lock:
                gate_spot_order_books[symbol] = {'bids': bids, 'asks': asks}
                gate_spot_density_timestamps[symbol] = {}

        saved_count = await sync_to_cache_async(symbol, market, log_func)
        log_func(f"✅ gate {market} Стакан {symbol}: {len(bids)} bids, {len(asks)} asks | плотностей: {saved_count}")
        return saved_count

    except Exception as e:
        log_func(f"❌ init_order_book_async(gate {market} {symbol}): {e}")
        return 0


# ==========================================
# СИНХРОНИЗАЦИЯ В REDIS (rate limit 10 сек для Gate!)
# ==========================================
async def sync_to_cache_async(symbol, market='futures', log_func=print):
    try:
        if market == 'futures':
            async with gate_futures_lock:
                book = gate_futures_order_books.get(symbol, {})
                ts = gate_futures_density_timestamps.get(symbol, {})
                if not book:
                    return 0
        else:
            async with gate_spot_lock:
                book = gate_spot_order_books.get(symbol, {})
                ts = gate_spot_density_timestamps.get(symbol, {})
                if not book:
                    return 0

        key = f"scalp:{market}:gate:{symbol}"
        now = time.time()

        densities = []
        is_first_load = len(ts) == 0

        for side, side_name in [('bids', 'buy'), ('asks', 'sell')]:
            for price, qty in book.get(side, {}).items():
                volume = price * qty
                if volume < 10000:
                    continue
                if price in ts:
                    if now - ts[price] < MIN_AGE_SECONDS:
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
                    'exchange': 'gate'
                })

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, cache.set, key, densities, CACHE_TTL)
        except RuntimeError:
            pass

        if market == 'futures':
            async with gate_futures_lock:
                gate_futures_density_timestamps[symbol] = ts
        else:
            async with gate_spot_lock:
                gate_spot_density_timestamps[symbol] = ts

        return len(densities)

    except Exception as e:
        log_func(f"❌ sync_to_cache_async(gate {market} {symbol}): {e}")
        return 0


# ==========================================
# HEARTBEAT — серверный time для Gate
# ==========================================
async def ws_heartbeat(ws, market='futures', log_func=print):
    """Gate требует heartbeat с серверным timestamp каждые 17 секунд"""
    try:
        while True:
            await asyncio.sleep(17)
            try:
                channel = "futures.ping" if market == 'futures' else "spot.ping"
                await ws.send(json.dumps({
                    "time": int(time.time()),
                    "channel": channel
                }))
            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                log_func(f"⚠️ gate {market} heartbeat завершён: {e}")
                return
    except asyncio.CancelledError:
        return


# ==========================================
# WEBSOCKET LISTENER (разные URL для futures/spot)
# ==========================================
async def ws_listener(market='futures', log_func=print):
    global gate_futures_symbols, gate_spot_symbols

    reconnect_event = gate_futures_reconnect_event if market == 'futures' else gate_spot_reconnect_event
    ws_url = GATE_FUTURES_WS_URL if market == 'futures' else GATE_SPOT_WS_URL

    while True:
        try:
            symbols = gate_futures_symbols if market == 'futures' else gate_spot_symbols

            if not symbols:
                await asyncio.sleep(5)
                continue

            log_func(f"🔌 gate {market} WS подключение: {len(symbols)} символов")

            async with websockets.connect(ws_url, ping_interval=None, ping_timeout=None) as ws:
                heartbeat_task = asyncio.create_task(ws_heartbeat(ws, market, log_func))

                try:
                    # Gate специфика: подписка с payload в виде списка
                    if market == 'futures':
                        for symbol in symbols:
                            msg = {
                                "time": int(time.time()),
                                "channel": "futures.order_book_update",
                                "event": "subscribe",
                                "payload": [f"{symbol}_USDT", "100ms", "100"]
                            }
                            await ws.send(json.dumps(msg))
                    else:
                        for symbol in symbols:
                            msg = {
                                "time": int(time.time()),
                                "channel": "spot.order_book_update",
                                "event": "subscribe",
                                "payload": [f"{symbol}_USDT", "100ms"]
                            }
                            await ws.send(json.dumps(msg))

                    log_func(f"✅ gate {market} WS подписан на {len(symbols)} символов")

                    while True:
                        if reconnect_event.is_set():
                            reconnect_event.clear()
                            log_func(f"🔄 gate {market}: сигнал переподключения получен")
                            break

                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue

                        queue = gate_futures_message_queue if market == 'futures' else gate_spot_message_queue
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
            log_func(f"⚠️ gate {market} WS закрыт, переподключение через 3 сек")
            await asyncio.sleep(3)
        except Exception as e:
            log_func(f"❌ gate {market} WS ошибка: {e}, переподключение через 5 сек")
            await asyncio.sleep(5)


# ==========================================
# ОБРАБОТКА ОЧЕРЕДИ (с учётом full: true/false)
# ==========================================
async def process_queue(market='futures', log_func=print):
    queue = gate_futures_message_queue if market == 'futures' else gate_spot_message_queue
    expected_channel = 'futures.order_book_update' if market == 'futures' else 'spot.order_book_update'

    while True:
        try:
            message = await queue.get()

            data = json.loads(message)

            # Пропускаем heartbeat и служебные
            if data.get('event') in ('pong', 'connected'):
                continue
            if data.get('channel') in ('futures.ping', 'spot.ping'):
                continue
            if data.get('channel') != expected_channel:
                continue

            result = data.get('result')
            if not result:
                continue

            contract = result.get('s')
            if not contract:
                continue
            symbol = contract.replace('_USDT', '')

            is_full = result.get('full', False)
            raw_bids = result.get('b') or []
            raw_asks = result.get('a') or []

            if is_full:
                await handle_snapshot_async(symbol, raw_bids, raw_asks, market, log_func)
            elif raw_bids or raw_asks:
                await handle_update_async(symbol, raw_bids, raw_asks, market, log_func)

        except Exception as e:
            log_func(f"❌ gate {market} process_queue ошибка: {e}")


async def handle_snapshot_async(symbol, raw_bids, raw_asks, market, log_func):
    new_bids = parse_levels(raw_bids)
    new_asks = parse_levels(raw_asks)

    if market == 'futures':
        async with gate_futures_lock:
            old_ts = gate_futures_density_timestamps.get(symbol, {})
            new_ts = {}
            for p in new_bids:
                if p in old_ts:
                    new_ts[p] = old_ts[p]
            for p in new_asks:
                if p in old_ts:
                    new_ts[p] = old_ts[p]

            gate_futures_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            gate_futures_density_timestamps[symbol] = new_ts
    else:
        async with gate_spot_lock:
            old_ts = gate_spot_density_timestamps.get(symbol, {})
            new_ts = {}
            for p in new_bids:
                if p in old_ts:
                    new_ts[p] = old_ts[p]
            for p in new_asks:
                if p in old_ts:
                    new_ts[p] = old_ts[p]

            gate_spot_order_books[symbol] = {'bids': new_bids, 'asks': new_asks}
            gate_spot_density_timestamps[symbol] = new_ts

    await sync_to_cache_async(symbol, market, log_func)


async def handle_update_async(symbol, bids_delta, asks_delta, market, log_func):
    rate_dict = last_sync_time if market == 'futures' else gate_spot_last_sync_time

    if market == 'futures':
        async with gate_futures_lock:
            if symbol not in gate_futures_order_books:
                return
            book = gate_futures_order_books[symbol]
            ts = gate_futures_density_timestamps.get(symbol, {})
            changed = False

            for row in bids_delta:
                try:
                    if isinstance(row, dict):
                        price = float(row.get('p', 0))
                        qty = abs(float(row.get('s', 0)))
                    else:
                        price = float(row[0])
                        qty = abs(float(row[1]))

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
                    if isinstance(row, dict):
                        price = float(row.get('p', 0))
                        qty = abs(float(row.get('s', 0)))
                    else:
                        price = float(row[0])
                        qty = abs(float(row[1]))

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
                gate_futures_density_timestamps[symbol] = ts
    else:
        async with gate_spot_lock:
            if symbol not in gate_spot_order_books:
                return
            book = gate_spot_order_books[symbol]
            ts = gate_spot_density_timestamps.get(symbol, {})
            changed = False

            for row in bids_delta:
                try:
                    if isinstance(row, dict):
                        price = float(row.get('p', 0))
                        qty = abs(float(row.get('s', 0)))
                    else:
                        price = float(row[0])
                        qty = abs(float(row[1]))

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
                    if isinstance(row, dict):
                        price = float(row.get('p', 0))
                        qty = abs(float(row.get('s', 0)))
                    else:
                        price = float(row[0])
                        qty = abs(float(row[1]))

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
                gate_spot_density_timestamps[symbol] = ts

    # Rate limit: Gate требует 10 сек!
    key = f"gate:{market}:{symbol}"
    now = time.time()
    if key not in rate_dict or (now - rate_dict[key]) >= SYNC_INTERVAL:
        await sync_to_cache_async(symbol, market, log_func)
        rate_dict[key] = now


# ==========================================
# ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ СПИСКОВ
# ==========================================
async def periodic_refresh(market='futures', log_func=print):
    global gate_futures_symbols, gate_spot_symbols

    while True:
        await asyncio.sleep(300)

        try:
            market_type = 'swap' if market == 'futures' else 'spot'
            candidates = await get_top_symbols_async(market_type, log_func)

            old_symbols = set(gate_futures_symbols if market == 'futures' else gate_spot_symbols)
            stable_symbols = stable_futures_symbols if market == 'futures' else stable_spot_symbols

            new_active = []
            TARGET = 30

            # ШАГ 1: Сохраняем монеты из белого списка (без обновления)
            for symbol in stable_symbols:
                if len(new_active) >= TARGET:
                    break
                if symbol in old_symbols:
                    new_active.append(symbol)
                else:
                    saved_count = await init_order_book_async(symbol, market, log_func)
                    if saved_count > 0:
                        new_active.append(symbol)
                        log_func(f"✅ gate {market} {symbol}: добавлен (плотностей: {saved_count}) [стабильная]")

            # ШАГ 2: Добавляем топ по формуле
            for symbol in candidates:
                if len(new_active) >= TARGET:
                    break
                if symbol in new_active:
                    continue
                saved_count = await init_order_book_async(symbol, market, log_func)
                if saved_count > 0:
                    new_active.append(symbol)
                    log_func(f"✅ gate {market} {symbol}: добавлен (плотностей: {saved_count})")
                else:
                    log_func(f"⚠️ gate {market} {symbol}: пропущен")

            if market == 'futures':
                removed = old_symbols - set(new_active)
                added = set(new_active) - old_symbols
                gate_futures_symbols = new_active
                if removed:
                    async with gate_futures_lock:
                        for sym in removed:
                            gate_futures_order_books.pop(sym, None)
                            gate_futures_density_timestamps.pop(sym, None)
                    log_func(f"🗑️ gate futures удалены: {', '.join(sorted(removed))}")

                if removed or added:
                    gate_futures_reconnect_event.set()
                    log_func(f"🔄 gate futures: список изменился (+{len(added)} -{len(removed)}), переподключение")
                else:
                    log_func(f"✅ gate futures: список не изменился ({len(new_active)} монет)")
            else:
                removed = old_symbols - set(new_active)
                added = set(new_active) - old_symbols
                gate_spot_symbols = new_active
                if removed:
                    async with gate_spot_lock:
                        for sym in removed:
                            gate_spot_order_books.pop(sym, None)
                            gate_spot_density_timestamps.pop(sym, None)
                    log_func(f"🗑️ gate spot удалены: {', '.join(sorted(removed))}")

                if removed or added:
                    gate_spot_reconnect_event.set()
                    log_func(f"🔄 gate spot: список изменился (+{len(added)} -{len(removed)}), переподключение")
                else:
                    log_func(f"✅ gate spot: список не изменился ({len(new_active)} монет)")

        except Exception as e:
            log_func(f"❌ Ошибка в periodic_refresh(gate {market}): {e}")


# ==========================================
# ГЛАВНАЯ ФУНКЦИЯ
# ==========================================
async def main_async(log_func=print):
    global gate_futures_symbols, gate_spot_symbols, stable_futures_symbols, stable_spot_symbols

    log_func("🚀 Запуск Gate Async Monitor...")

    # --- Шаг 1: Получаем белый список (стабильные монеты) ---
    stable_f = await get_stable_coins_async('swap', STABLE_COINS_LIMIT)
    stable_s = await get_stable_coins_async('spot', STABLE_COINS_LIMIT)
    stable_futures_symbols = stable_f
    stable_spot_symbols = stable_s
    log_func(f"🔒 Белый список futures: {stable_f}")
    log_func(f"🔒 Белый список spot: {stable_s}")

    # --- Шаг 2: Получаем кандидатов по формуле ---
    futures_candidates = await get_top_symbols_async('swap', log_func)
    spot_candidates = await get_top_symbols_async('spot', log_func)

    # --- Шаг 3: Инициализируем белый список ---
    active_futures = []
    for symbol in stable_f:
        saved_count = await init_order_book_async(symbol, 'futures', log_func)
        if saved_count > 0:
            active_futures.append(symbol)
            log_func(f"✅ gate futures {symbol}: принят (плотностей: {saved_count}) [стабильная]")

    active_spot = []
    for symbol in stable_s:
        saved_count = await init_order_book_async(symbol, 'spot', log_func)
        if saved_count > 0:
            active_spot.append(symbol)
            log_func(f"✅ gate spot {symbol}: принят (плотностей: {saved_count}) [стабильная]")

    # --- Шаг 4: Добавляем топ по формуле (не из белого списка) ---
    for symbol in futures_candidates[:30]:
        if symbol in active_futures:
            continue  # Уже в белом списке
        saved_count = await init_order_book_async(symbol, 'futures', log_func)
        if saved_count > 0:
            active_futures.append(symbol)
            log_func(f"✅ gate futures {symbol}: принят (плотностей: {saved_count})")

    for symbol in spot_candidates[:30]:
        if symbol in active_spot:
            continue  # Уже в белом списке
        saved_count = await init_order_book_async(symbol, 'spot', log_func)
        if saved_count > 0:
            active_spot.append(symbol)
            log_func(f"✅ gate spot {symbol}: принят (плотностей: {saved_count})")

    gate_futures_symbols = active_futures
    gate_spot_symbols = active_spot

    log_func(f"✅ Gate Async Monitor инициализирован: {len(active_futures)} futures, {len(active_spot)} spot")

    tasks = [
        ws_listener('futures', log_func),
        ws_listener('spot', log_func),
        process_queue('futures', log_func),
        process_queue('spot', log_func),
        periodic_refresh('futures', log_func),
        periodic_refresh('spot', log_func),
    ]

    await asyncio.gather(*tasks)


def start_gate_async_monitor(log_func=print):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(main_async(log_func))
    except Exception as e:
        log_func(f"❌ Gate Async Monitor упал: {e}")
        import traceback
        log_func(traceback.format_exc())
    finally:
        pass