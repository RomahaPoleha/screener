"""
Async Scalp Monitor — Binance + Bybit (Futures)
WebSocket + Django cache. Асинхронная архитектура.
"""
import asyncio
import time
import orjson as json
import websockets
import os
import traceback
from logging.handlers import RotatingFileHandler
from django.core.cache import cache
import ccxt

# === КОНФИГУРАЦИЯ ===
GLOBAL_MIN_VOLUME = 10000
MIN_AGE_SECONDS = 180
CACHE_TTL = 900

# Binance
BINANCE_FUTURES_WS = "wss://fstream.binance.com/ws"
BINANCE_FUTURES_REST = "https://fapi.binance.com/fapi/v1/depth"
BINANCE_SPOT_WS = "wss://stream.binance.com:9443/ws"
BINANCE_SPOT_REST = "https://api.binance.com/api/v3/depth"

# Bybit (пока только Futures для теста)
BYBIT_FUTURES_WS = "wss://stream.bybit.com/v5/public/linear"
BYBIT_FUTURES_REST = "https://api.bybit.com/v5/market/orderbook?category=linear&symbol={}USDT"

# === ЛОГИРОВАНИЕ ===
LOG_DIR = '/app/data'
LOG_FILE = os.path.join(LOG_DIR, 'scalp_monitor.log')
os.makedirs(LOG_DIR, exist_ok=True)

_logger = __import__('logging').getLogger('scalp_monitor')
_logger.setLevel(__import__('logging').INFO)
_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
_handler.setFormatter(__import__('logging').Formatter('%(asctime)s - %(message)s'))
_logger.addHandler(_handler)
_logger.addHandler(__import__('logging').StreamHandler())

def log(msg):
    _logger.info(msg)

# === ГЛОБАЛЬНОЕ СОСТОЯНИЕ ===
futures_order_books = {}
futures_density_timestamps = {}
spot_order_books = {}
spot_density_timestamps = {}
bybit_futures_order_books = {}
bybit_futures_density_timestamps = {}
last_sync_time = {}
shutdown_event = asyncio.Event()

# Списки символов
futures_symbols = []
spot_symbols = []
bybit_futures_symbols = []

def is_valid_symbol(symbol):
    if '-' in symbol or len(symbol) < 2 or len(symbol) > 15:
        return False
    return symbol.replace('_', '').isalnum()

def get_top_symbols(limit, market='futures', exchange='binance'):
    """Получает топ монет через CCXT"""
    try:
        if exchange == 'binance':
            ccxt_market = 'future' if market == 'futures' else 'spot'
            ex = ccxt.binance({'enableRateLimit': True, 'timeout': 10000, 'options': {'defaultType': ccxt_market}})
        else: # bybit
            ex = ccxt.bybit({'enableRateLimit': True, 'timeout': 10000, 'options': {'defaultType': 'linear'}})

        tickers = ex.fetch_tickers()
        symbols_with_volume = []
        for symbol, data in tickers.items():
            clean_symbol = symbol.replace('/USDT', '').replace(':USDT', '').replace('USDT', '')
            if not is_valid_symbol(clean_symbol):
                continue
            volume = data.get('quoteVolume') or 0
            if volume > 0:
                symbols_with_volume.append((clean_symbol, volume))

        symbols_with_volume.sort(key=lambda x: x[1], reverse=True)
        symbols = [s[0] for s in symbols_with_volume[:limit]]
        log(f"✅ {exchange} {market}: Найдено {len(symbols)} монет")
        return symbols
    except Exception as e:
        log(f"❌ Ошибка get_top_symbols({exchange} {market}): {e}")
        return []

def init_order_book_sync(symbol, market='futures', exchange='binance'):
    """Инициализация стакана с защитой от банов"""
    try:
        if exchange == 'binance':
            url = f"{BINANCE_FUTURES_REST if market == 'futures' else BINANCE_SPOT_REST}?symbol={symbol}USDT&limit=1000"
        else:
            url = BYBIT_FUTURES_REST.format(symbol)

        res = __import__('requests').get(url, timeout=10)
        if not res.ok:
            return False

        data = res.json()

        if exchange == 'binance':
            bids = {float(price): float(qty) for price, qty in data.get('bids', [])}
            asks = {float(price): float(qty) for price, qty in data.get('asks', [])}
        else: # bybit
            result = data.get('result', {})
            bids = {float(price): float(qty) for price, qty in result.get('b', [])}
            asks = {float(price): float(qty) for price, qty in result.get('a', [])}

        initial_ts = {float(price): time.time() - 200 for price in bids}
        initial_ts.update({float(price): time.time() - 200 for price in asks})

        # Сохраняем в нужные словари
        if exchange == 'binance' and market == 'futures':
            futures_order_books[symbol] = {'bids': bids, 'asks': asks}
            futures_density_timestamps[symbol] = initial_ts
        elif exchange == 'binance' and market == 'spot':
            spot_order_books[symbol] = {'bids': bids, 'asks': asks}
            spot_density_timestamps[symbol] = initial_ts
        elif exchange == 'bybit' and market == 'futures':
            bybit_futures_order_books[symbol] = {'bids': bids, 'asks': asks}
            bybit_futures_density_timestamps[symbol] = initial_ts

        sync_to_cache_sync(symbol, market, exchange)
        return True
    except Exception as e:
        log(f"❌ init_order_book({exchange} {market} {symbol}): {e}")
        return False

def sync_to_cache_sync(symbol, market='futures', exchange='binance'):
    """Синхронная запись в кэш"""
    try:
        if exchange == 'binance' and market == 'futures':
            order_books, timestamps = futures_order_books, futures_density_timestamps
        elif exchange == 'binance' and market == 'spot':
            order_books, timestamps = spot_order_books, spot_density_timestamps
        else:
            order_books, timestamps = bybit_futures_order_books, bybit_futures_density_timestamps
            market = 'bybit_futures' # Уникальный ключ для фронта, если нужно, или оставим 'futures'

        book = order_books.get(symbol, {})
        ts = timestamps.get(symbol, {})
        if not book:
            return

        # Для совместимости с текущим views.py используем ключ 'futures' для обоих,
        # либо можно сделать 'bybit_futures'. Пока оставим 'futures', но добавим пометку в данные.
        key = f"scalp:futures:{symbol}"
        now = time.time()
        densities = []

        for side, side_name in [('bids', 'buy'), ('asks', 'sell')]:
            for price, qty in book.get(side, {}).items():
                volume = price * qty
                if volume < GLOBAL_MIN_VOLUME:
                    continue
                if price in ts:
                    if (now - ts[price]) < MIN_AGE_SECONDS:
                        continue
                else:
                    ts[price] = now
                    continue

                densities.append({
                    'price': price, 'volume': volume, 'side': side_name,
                    'timestamp': ts[price], 'exchange': exchange # Добавили биржу для ясности
                })

        cache.set(key, densities, CACHE_TTL)
        timestamps[symbol] = ts
    except Exception as e:
        log(f"❌ sync_to_cache({exchange} {market} {symbol}): {e}")

def update_order_book_sync(symbol, bids_delta, asks_delta, market='futures', exchange='binance'):
    """Обновление стакана"""
    try:
        if exchange == 'binance' and market == 'futures':
            order_books, timestamps = futures_order_books, futures_density_timestamps
        elif exchange == 'binance' and market == 'spot':
            order_books, timestamps = spot_order_books, spot_density_timestamps
        else:
            order_books, timestamps = bybit_futures_order_books, bybit_futures_density_timestamps
            market = 'bybit_futures'

        if symbol not in order_books:
            return

        book = order_books[symbol]
        ts = timestamps.get(symbol, {})
        changed = False

        for price_str, qty_str in bids_delta:
            price, qty = float(price_str), float(qty_str)
            if qty == 0:
                if price in book['bids']: del book['bids'][price]; ts.pop(price, None); changed = True
            else:
                book['bids'][price] = qty
                if price not in ts: ts[price] = time.time()
                changed = True

        for price_str, qty_str in asks_delta:
            price, qty = float(price_str), float(qty_str)
            if qty == 0:
                if price in book['asks']: del book['asks'][price]; ts.pop(price, None); changed = True
            else:
                book['asks'][price] = qty
                if price not in ts: ts[price] = time.time()
                changed = True

        if changed:
            key = f"{exchange}:{market}:{symbol}"
            now = time.time()
            if key not in last_sync_time or (now - last_sync_time[key]) >= 2:
                sync_to_cache_sync(symbol, market, exchange)
                last_sync_time[key] = now

    except Exception as e:
        log(f"❌ update_order_book({exchange} {market} {symbol}): {e}")

async def run_websocket(market='futures', exchange='binance'):
    """Асинхронный обработчик WebSocket"""
    if exchange == 'binance':
        url = BINANCE_FUTURES_WS if market == 'futures' else BINANCE_SPOT_WS
        symbols = futures_symbols if market == 'futures' else spot_symbols
    else:
        url = BYBIT_FUTURES_WS
        symbols = bybit_futures_symbols

    if not symbols:
        log(f"⚠️ {exchange} {market}: Нет символов. Пропускаем.")
        return

    if exchange == 'binance':
        streams = [f"{s.lower()}usdt@depth@100ms" for s in symbols]
        subscribe_msg = {"method": "SUBSCRIBE", "params": streams, "id": 1}
    else:
        args = [f"orderbook.50.{s}USDT" for s in symbols]
        subscribe_msg = {"op": "subscribe", "args": args}

    log(f"🔌 {exchange} {market}: Подключаемся к {len(symbols)} символам...")

    while not shutdown_event.is_set():
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=30) as ws:
                log(f"✅ {exchange} {market}: WebSocket соединён")
                await ws.send(json.dumps(subscribe_msg).decode('utf-8'))
                log(f"📤 {exchange} {market}: Подписка отправлена")

                async for message in ws:
                    if shutdown_event.is_set():
                        break

                    data = json.loads(message)
                    symbol, bids, asks = None, [], []

                    if exchange == 'binance':
                        if 'data' in data:
                            d = data['data']
                            sym = d.get('s', '')
                            symbol = sym[:-4] if sym.endswith('USDT') else sym
                            bids, asks = d.get('b', []), d.get('a', [])
                        elif 's' in data:
                            sym = data.get('s', '')
                            symbol = sym[:-4] if sym.endswith('USDT') else sym
                            bids, asks = data.get('b', []), data.get('a', [])
                    else: # bybit
                        if data.get('topic', '').startswith('orderbook') and data.get('type') == 'delta':
                            sym = data['topic'].split('.')[2]
                            symbol = sym[:-4] if sym.endswith('USDT') else sym
                            d = data.get('data', {})
                            bids, asks = d.get('b', []), d.get('a', [])

                    if symbol and (bids or asks):
                        update_order_book_sync(symbol, bids, asks, market, exchange)

        except websockets.exceptions.ConnectionClosed as e:
            log(f"⚠️ {exchange} {market}: Соединение закрыто (code={e.code}). Переподключение...")
        except Exception as e:
            log(f"❌ {exchange} {market}: Ошибка: {e}")

        if not shutdown_event.is_set():
            await asyncio.sleep(3)

async def main_loop():
    global futures_symbols, spot_symbols, bybit_futures_symbols
    log("🚀 Async Scalp Monitor запущен (Binance + Bybit)!")

    loop = asyncio.get_event_loop()

    # 1. Получаем символы
    futures_symbols = await loop.run_in_executor(None, get_top_symbols, 100, 'futures', 'binance')
    spot_symbols = await loop.run_in_executor(None, get_top_symbols, 100, 'spot', 'binance')
    bybit_futures_symbols = await loop.run_in_executor(None, get_top_symbols, 20, 'futures', 'bybit') # Только 20 для теста

    # 2. Инициализация стаканов
    log("🔄 Инициализация стаканов...")
    init_tasks = []

    for sym in futures_symbols:
        init_tasks.append(loop.run_in_executor(None, init_order_book_sync, sym, 'futures', 'binance'))
    for sym in spot_symbols:
        init_tasks.append(loop.run_in_executor(None, init_order_book_sync, sym, 'spot', 'binance'))
    for sym in bybit_futures_symbols:
        init_tasks.append(loop.run_in_executor(None, init_order_book_sync, sym, 'futures', 'bybit'))

    # Выполняем с ограничением параллельности (semaphore), чтобы не получить бан
    semaphore = asyncio.Semaphore(10)
    async def bounded_init(coro):
        async with semaphore:
            return await coro

    results = await asyncio.gather(*[bounded_init(t) for t in init_tasks])
    success_count = sum(1 for r in results if r)
    log(f"✅ Инициализация завершена. Успешно: {success_count}/{len(init_tasks)}")

    # 3. Запуск WebSocket
    ws_tasks = []
    if futures_symbols:
        ws_tasks.append(asyncio.create_task(run_websocket('futures', 'binance')))
    if spot_symbols:
        ws_tasks.append(asyncio.create_task(run_websocket('spot', 'binance')))
    if bybit_futures_symbols:
        ws_tasks.append(asyncio.create_task(run_websocket('futures', 'bybit')))

    log("🎯 WebSocket потоки запущены")

    # 4. Heartbeat
    while not shutdown_event.is_set():
        await asyncio.sleep(30)
        log(f"💓 Heartbeat: Binance F:{len(futures_symbols)} S:{len(spot_symbols)} | Bybit F:{len(bybit_futures_symbols)}")

def start_scalp_monitor():
    log("🔧 Запуск Scalp Monitor...")
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main_loop())
    except Exception as e:
        log(f"❌ Критическая ошибка запуска: {e}")
        log(traceback.format_exc())

def stop_scalp_monitor():
    log("🛑 Остановка Scalp Monitor...")
    shutdown_event.set()