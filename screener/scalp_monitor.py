"""
Async Scalp Monitor — мониторинг плотностей в реальном времени
WebSocket + Django cache. Асинхронная архитектура (asyncio).
"""
import asyncio
import time
import orjson as json
import aiohttp
import websockets
import os
import traceback
from logging.handlers import RotatingFileHandler
from django.core.cache import cache
from asgiref.sync import sync_to_async
import ccxt

# === КОНФИГУРАЦИЯ ===
ACTIVE_EXCHANGES = ['binance']  # <-- Добавь 'bybit' сюда, когда будешь готов
GLOBAL_MIN_VOLUME = 10000
MIN_AGE_SECONDS = 180
TOP_SYMBOLS_COUNT = 100
CACHE_TTL = 900

# URL-адреса
EXCHANGE_CONFIG = {
    'binance': {
        'futures_ws': "wss://fstream.binance.com/ws",
        'futures_rest': "https://fapi.binance.com/fapi/v1/depth",
        'spot_ws': "wss://stream.binance.com:9443/ws",
        'spot_rest': "https://api.binance.com/api/v3/depth",
    },
    'bybit': {
        'futures_ws': "wss://stream.bybit.com/v5/public/linear",
        'futures_rest': "https://api.bybit.com/v5/market/orderbook?category=linear&symbol={}USDT",
        'spot_ws': "wss://stream.bybit.com/v5/public/spot",
        'spot_rest': "https://api.bybit.com/v5/market/orderbook?category=spot&symbol={}USDT",
    }
}

# === ЛОГИРОВАНИЕ ===
LOG_DIR = '/app/data'
LOG_FILE = os.path.join(LOG_DIR, 'scalp_monitor.log')
os.makedirs(LOG_DIR, exist_ok=True)

_logger = __import__('logging').getLogger('scalp_monitor')
_logger.setLevel(__import__('logging').INFO)
_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
_handler.setFormatter(__import__('logging').Formatter('%(asctime)s - %(message)s'))
_logger.addHandler(_handler)
_console = __import__('logging').StreamHandler()
_logger.addHandler(_console)

def log(msg):
    _logger.info(msg)

# === ГЛОБАЛЬНОЕ СОСТОЯНИЕ (Безопасно в asyncio, т.к. один поток) ===
order_books = {}
density_timestamps = {}
last_sync_time = {}
shutdown_event = asyncio.Event()

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
@sync_to_async
def async_cache_set(key, value, ttl):
    """Безопасная асинхронная запись в Django Cache (Redis)"""
    cache.set(key, value, ttl)

def is_valid_symbol(symbol):
    if '-' in symbol or len(symbol) < 2 or len(symbol) > 15:
        return False
    return symbol.replace('_', '').isalnum()

async def get_top_symbols_async(market):
    """Асинхронное получение топ-символов через CCXT"""
    loop = asyncio.get_event_loop()
    def fetch():
        exchange = ccxt.binance({
            'enableRateLimit': True, 'timeout': 10000,
            'options': {'defaultType': 'future' if market == 'futures' else 'spot'}
        })
        tickers = exchange.fetch_tickers()
        valid = []
        for symbol, data in tickers.items():
            if ':USDT' not in symbol and not symbol.endswith('/USDT'):
                continue
            vol = data.get('quoteVolume') or 0
            if vol < 1000000:
                continue
            clean = symbol.replace('/USDT', '').replace(':USDT', '')
            if is_valid_symbol(clean):
                valid.append((clean, vol))
        valid.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in valid[:TOP_SYMBOLS_COUNT]]

    try:
        return await loop.run_in_executor(None, fetch)
    except Exception as e:
        log(f"❌ Ошибка получения символов ({market}): {e}")
        return []

async def init_order_book_async(exchange, market, symbol, session):
    """Асинхронная загрузка начального снимка стакана"""
    try:
        url = EXCHANGE_CONFIG[exchange][f'{market}_rest'].format(symbol)
        async with session.get(url, timeout=10) as resp:
            if resp.status != 200:
                return False

            data = await resp.json()

            if exchange == 'binance':
                bids = {float(p): float(q) for p, q in data.get('bids', [])}
                asks = {float(p): float(q) for p, q in data.get('asks', [])}
            else: # bybit
                result = data.get('result', {})
                bids = {float(p): float(q) for p, q in result.get('b', [])}
                asks = {float(p): float(q) for p, q in result.get('a', [])}

        if exchange not in order_books:
            order_books[exchange] = {market: {}}
            density_timestamps[exchange] = {market: {}}

        if market not in order_books[exchange]:
            order_books[exchange][market] = {}
            density_timestamps[exchange][market] = {}

        order_books[exchange][market][symbol] = {'bids': bids, 'asks': asks}
        density_timestamps[exchange][market][symbol] = {}

        await sync_to_cache_async(symbol, exchange, market)
        return True
    except Exception as e:
        log(f"❌ init_order_book ({exchange} {market} {symbol}): {e}")
        return False

async def sync_to_cache_async(symbol, exchange, market):
    """Сбор плотностей и асинхронная запись в Redis"""
    try:
        book = order_books.get(exchange, {}).get(market, {}).get(symbol, {})
        if not book:
            return

        ts_dict = density_timestamps[exchange][market].setdefault(symbol, {})
        now = time.time()
        densities = []

        for side, side_name in [('bids', 'buy'), ('asks', 'sell')]:
            for price, qty in book.get(side, {}).items():
                volume = price * qty
                if volume < GLOBAL_MIN_VOLUME:
                    continue

                if price in ts_dict:
                    if (now - ts_dict[price]) < MIN_AGE_SECONDS:
                        continue
                else:
                    ts_dict[price] = now
                    continue

                densities.append({
                    'price': price, 'volume': volume, 'side': side_name, 'timestamp': ts_dict[price]
                })

        # Формат ключа строго как в views.py: f"scalp:{market}:{symbol}"
        # Но мы добавляем exchange, чтобы в будущем не было конфликтов,
        # а views.py пока читает 'futures' или 'spot'.
        # Для обратной совместимости с текущим views.py пишем в старый ключ тоже, или адаптируем views.py.
        # Лучший вариант: писать в f"scalp:{market}:{symbol}", как ждет views.py.
        key = f"scalp:{market}:{symbol}"
        await async_cache_set(key, densities, CACHE_TTL)

        last_sync_time[f"{exchange}:{market}:{symbol}"] = now

    except Exception as e:
        log(f"❌ sync_to_cache ({exchange} {market} {symbol}): {e}")

def parse_ws_message(exchange, market, raw_message):
    """Унифицирует сообщения от разных бирж"""
    try:
        data = json.loads(raw_message)

        if exchange == 'binance':
            if 'data' in data:
                d = data['data']
                sym = d.get('s', '')[:-4] if d.get('s', '').endswith('USDT') else d.get('s', '')
                return sym, d.get('b', []), d.get('a', [])
            elif 's' in data:
                sym = data.get('s', '')[:-4] if data.get('s', '').endswith('USDT') else data.get('s', '')
                return sym, data.get('b', []), data.get('a', [])

        elif exchange == 'bybit':
            topic = data.get('topic', '')
            if 'orderbook' in topic and data.get('type') == 'delta':
                sym = topic.split('.')[2][:-4] if topic.split('.')[2].endswith('USDT') else topic.split('.')[2]
                d = data.get('data', {})
                return sym, d.get('b', []), d.get('a', [])

        return None, [], []
    except Exception:
        return None, [], []

async def process_ws_updates(exchange, market, symbol, bids_delta, asks_delta):
    """Обновляет локальный стакан и триггерит синхронизацию в Redis"""
    try:
        if exchange not in order_books or market not in order_books[exchange] or symbol not in order_books[exchange][market]:
            return

        book = order_books[exchange][market][symbol]
        ts_dict = density_timestamps[exchange][market][symbol]
        changed = False

        for price_str, qty_str in bids_delta:
            price, qty = float(price_str), float(qty_str)
            if qty == 0:
                if price in book['bids']: del book['bids'][price]; ts_dict.pop(price, None); changed = True
            else:
                book['bids'][price] = qty
                if price not in ts_dict: ts_dict[price] = time.time()
                changed = True

        for price_str, qty_str in asks_delta:
            price, qty = float(price_str), float(qty_str)
            if qty == 0:
                if price in book['asks']: del book['asks'][price]; ts_dict.pop(price, None); changed = True
            else:
                book['asks'][price] = qty
                if price not in ts_dict: ts_dict[price] = time.time()
                changed = True

        if changed:
            key = f"{exchange}:{market}:{symbol}"
            now = time.time()
            if key not in last_sync_time or (now - last_sync_time[key]) >= 2: # 2 сек кулдаун
                await sync_to_cache_async(symbol, exchange, market)

    except Exception as e:
        log(f"❌ process_ws_updates ({exchange} {market} {symbol}): {e}")

async def websocket_handler(exchange, market, symbols):
    """Основной асинхронный цикл WebSocket"""
    ws_url = EXCHANGE_CONFIG[exchange][f'{market}_ws']
    log(f"🔌 {exchange} {market}: Подключение к {len(symbols)} символам...")

    while not shutdown_event.is_set():
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                log(f"✅ {exchange} {market}: WebSocket соединён")

                if exchange == 'binance':
                    streams = [f"{s.lower()}usdt@depth@100ms" for s in symbols]
                    subscribe_msg = {"method": "SUBSCRIBE", "params": streams, "id": 1}
                else:
                    args = [f"orderbook.50.{s}USDT" for s in symbols]
                    subscribe_msg = {"op": "subscribe", "args": args}

                await ws.send(json.dumps(subscribe_msg))

                async for raw_message in ws:
                    if shutdown_event.is_set():
                        break

                    symbol, bids, asks = parse_ws_message(exchange, market, raw_message)
                    if symbol and (bids or asks):
                        asyncio.create_task(process_ws_updates(exchange, market, symbol, bids, asks))

        except websockets.exceptions.ConnectionClosed:
            log(f"⚠️ {exchange} {market}: Соединение закрыто. Переподключение...")
        except Exception as e:
            log(f"❌ {exchange} {market}: Ошибка WebSocket: {e}")

        if not shutdown_event.is_set():
            await asyncio.sleep(3)

async def main_loop():
    log("🚀 Async Scalp Monitor запущен!")

    futures_symbols = await get_top_symbols_async('futures')
    spot_symbols = await get_top_symbols_async('spot')

    if not futures_symbols and not spot_symbols:
        log("⚠️ Не удалось получить символы.")
        return

    async with aiohttp.ClientSession() as session:
        tasks = []
        for exch in ACTIVE_EXCHANGES:
            if futures_symbols:
                for sym in futures_symbols:
                    tasks.append(init_order_book_async(exch, 'futures', sym, session))
            if spot_symbols:
                for sym in spot_symbols:
                    tasks.append(init_order_book_async(exch, 'spot', sym, session))

        log(f"⏳ Инициализация {len(tasks)} стаканов...")
        semaphore = asyncio.Semaphore(20) # Не более 20 одновременных REST запросов
        async def bounded_init(coro):
            async with semaphore:
                return await coro

        results = await asyncio.gather(*[bounded_init(t) for t in tasks])
        log(f"✅ Инициализация завершена. Успешно: {sum(1 for r in results if r)}")

    ws_tasks = []
    for exch in ACTIVE_EXCHANGES:
        if futures_symbols:
            ws_tasks.append(asyncio.create_task(websocket_handler(exch, 'futures', futures_symbols)))
        if spot_symbols:
            ws_tasks.append(asyncio.create_task(websocket_handler(exch, 'spot', spot_symbols)))

    log("🎯 Все WebSocket потоки запущены.")

    while not shutdown_event.is_set():
        await asyncio.sleep(30)
        log(f"💓 Heartbeat: Активных бирж: {len(ACTIVE_EXCHANGES)}")

def start_scalp_monitor():
    """Точка входа для запуска в фоне"""
    log("🔧 Запуск Async Scalp Monitor...")
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    asyncio.ensure_future(main_loop(), loop=loop)
    log("✅ Async Scalp Monitor успешно запущен в фоновом режиме")

def stop_scalp_monitor():
    log("🛑 Остановка Async Scalp Monitor...")
    shutdown_event.set()