"""
Async Scalp Monitor — стабильная версия на базе рабочего кода
WebSocket + Django cache. Использует asyncio + orjson для скорости.
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
TOP_SYMBOLS_COUNT = 100
CACHE_TTL = 900

FUTURES_WS_URL = "wss://fstream.binance.com/ws"
FUTURES_REST_URL = "https://fapi.binance.com/fapi/v1/depth"
SPOT_WS_URL = "wss://stream.binance.com:9443/ws"
SPOT_REST_URL = "https://api.binance.com/api/v3/depth"

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

# === ГЛОБАЛЬНОЕ СОСТОЯНИЕ (Плоская структура, как в рабочем коде) ===
futures_order_books = {}
futures_density_timestamps = {}
spot_order_books = {}
spot_density_timestamps = {}
last_sync_time = {}
shutdown_event = asyncio.Event()

def is_valid_symbol(symbol):
    if '-' in symbol or len(symbol) < 2 or len(symbol) > 15:
        return False
    return symbol.replace('_', '').isalnum()

def get_top_symbols(limit=TOP_SYMBOLS_COUNT, market='futures'):
    """Синхронная функция (вызывается через executor), точно как в рабочем коде"""
    try:
        ccxt_market = 'future' if market == 'futures' else 'spot'
        exchange = ccxt.binance({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': ccxt_market}
        })
        tickers = exchange.fetch_tickers()

        symbols_with_volume = []
        for symbol, data in tickers.items():
            if ':USDT' not in symbol and not symbol.endswith('/USDT'):
                continue
            volume = data.get('quoteVolume') or 0
            if volume < 1000000:
                continue

            clean_symbol = symbol.replace('/USDT', '').replace(':USDT', '')
            if not is_valid_symbol(clean_symbol):
                continue

            symbols_with_volume.append((clean_symbol, volume))

        symbols_with_volume.sort(key=lambda x: x[1], reverse=True)
        symbols = [s[0] for s in symbols_with_volume[:limit]]
        log(f"✅ {market}: Найдено {len(symbols)} монет для мониторинга")
        return symbols
    except Exception as e:
        log(f"❌ Ошибка get_top_symbols({market}): {e}")
        return []

def init_order_book_sync(symbol, market='futures'):
    """Синхронная инициализация с задержкой, чтобы не получить бан от REST API"""
    try:
        url = f"{FUTURES_REST_URL if market == 'futures' else SPOT_REST_URL}?symbol={symbol}USDT&limit=1000"
        res = __import__('requests').get(url, timeout=10)
        if not res.ok:
            log(f"⚠️ {market} {symbol}: HTTP {res.status_code}")
            return False

        data = res.json()
        bids = {float(price): float(qty) for price, qty in data.get('bids', [])}
        asks = {float(price): float(qty) for price, qty in data.get('asks', [])}

        order_books = futures_order_books if market == 'futures' else spot_order_books
        timestamps = futures_density_timestamps if market == 'futures' else spot_density_timestamps

        order_books[symbol] = {'bids': bids, 'asks': asks}
        timestamps[symbol] = {}

        sync_to_cache_sync(symbol, market)
        return True
    except Exception as e:
        log(f"❌ init_order_book({market} {symbol}): {e}")
        return False

def sync_to_cache_sync(symbol, market='futures'):
    """Синхронная запись в кэш"""
    try:
        order_books = futures_order_books if market == 'futures' else spot_order_books
        timestamps = futures_density_timestamps if market == 'futures' else spot_density_timestamps

        book = order_books.get(symbol, {})
        ts = timestamps.get(symbol, {})
        if not book:
            return

        key = f"scalp:{market}:{symbol}"
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

                densities.append({'price': price, 'volume': volume, 'side': side_name, 'timestamp': ts[price]})

        cache.set(key, densities, CACHE_TTL)
        timestamps[symbol] = ts
    except Exception as e:
        log(f"❌ sync_to_cache({market} {symbol}): {e}")

def update_order_book_sync(symbol, bids_delta, asks_delta, market='futures'):
    """Обновление стакана (без очереди, так как asyncio и так асинхронный)"""
    try:
        order_books = futures_order_books if market == 'futures' else spot_order_books
        timestamps = futures_density_timestamps if market == 'futures' else spot_density_timestamps

        if symbol not in order_books:
            # Это нормально для первых сообщений, если инициализация еще идет
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
            key = f"{market}:{symbol}"
            now = time.time()
            if key not in last_sync_time or (now - last_sync_time[key]) >= 2: # 2 сек кулдаун
                sync_to_cache_sync(symbol, market)
                last_sync_time[key] = now

    except Exception as e:
        log(f"❌ update_order_book({market} {symbol}): {e}")

async def run_websocket(market='futures'):
    """Асинхронный обработчик WebSocket"""
    url = FUTURES_WS_URL if market == 'futures' else SPOT_WS_URL
    symbols = futures_symbols if market == 'futures' else spot_symbols

    if not symbols:
        log(f"⚠️ {market}: Нет символов для подписки! Пропускаем.")
        return

    streams = [f"{s.lower()}usdt@depth@100ms" for s in symbols]
    log(f"🔌 {market}: Подключаемся к {len(symbols)} символам...")

    while not shutdown_event.is_set():
        try:
            # Увеличен ping_timeout до 30 сек для стабильности
            async with websockets.connect(url, ping_interval=20, ping_timeout=30) as ws:
                log(f"✅ {market}: WebSocket соединён")

                subscribe_msg = {"method": "SUBSCRIBE", "params": streams, "id": 1}
                await ws.send(json.dumps(subscribe_msg).decode('utf-8'))
                log(f"📤 {market}: Подписка отправлена ({len(streams)} стримов)")

                async for message in ws:
                    if shutdown_event.is_set():
                        break

                    # Парсинг сообщения (orjson работает мгновенно)
                    data = json.loads(message)
                    symbol = None
                    bids, asks = [], []

                    if 'data' in data:
                        d = data['data']
                        sym = d.get('s', '')
                        symbol = sym[:-4] if sym.endswith('USDT') else sym
                        bids, asks = d.get('b', []), d.get('a', [])
                    elif 's' in data:
                        sym = data.get('s', '')
                        symbol = sym[:-4] if sym.endswith('USDT') else sym
                        bids, asks = data.get('b', []), data.get('a', [])

                    if symbol and (bids or asks):
                        # Вызываем синхронную функцию обновления.
                        # В asyncio это безопасно, так как нет await внутри функции, и GIL не переключается.
                        update_order_book_sync(symbol, bids, asks, market)

        except websockets.exceptions.ConnectionClosed as e:
            log(f"⚠️ {market}: Соединение закрыто (code={e.code}). Переподключение через 3 сек...")
        except Exception as e:
            log(f"❌ {market}: Ошибка WebSocket: {e}")
            log(traceback.format_exc())

        if not shutdown_event.is_set():
            await asyncio.sleep(3)

# Глобальные переменные для символов (заполняются в main_loop)
futures_symbols = []
spot_symbols = []

async def main_loop():
    global futures_symbols, spot_symbols
    log("🚀 Async Scalp Monitor запущен!")

    # 1. Получаем символы (в executor, чтобы не блокировать asyncio)
    loop = asyncio.get_event_loop()
    futures_symbols = await loop.run_in_executor(None, get_top_symbols, TOP_SYMBOLS_COUNT, 'futures')
    spot_symbols = await loop.run_in_executor(None, get_top_symbols, TOP_SYMBOLS_COUNT, 'spot')

    if not futures_symbols and not spot_symbols:
        log("⚠️ Не удалось получить символы. Завершение.")
        return

    # 2. Инициализируем стаканы ПОСЛЕДОВАТЕЛЬНО с задержкой (как в рабочем коде)
    log("🔄 Инициализация стаканов...")
    for market, symbols in [('futures', futures_symbols), ('spot', spot_symbols)]:
        if not symbols: continue
        for idx, symbol in enumerate(symbols, 1):
            success = await loop.run_in_executor(None, init_order_book_sync, symbol, market)
            if success and idx % 20 == 0:
                log(f"  {market} прогресс: {idx}/{len(symbols)}")
            await asyncio.sleep(0.05) # Защита от REST банов
    log("✅ Инициализация стаканов завершена")

    # 3. Запускаем WebSocket задачи
    ws_tasks = []
    if futures_symbols:
        ws_tasks.append(asyncio.create_task(run_websocket('futures')))
    if spot_symbols:
        ws_tasks.append(asyncio.create_task(run_websocket('spot')))

    log("🎯 WebSocket потоки запущены и работают в фоне")

    # 4. Heartbeat цикл
    while not shutdown_event.is_set():
        await asyncio.sleep(30)
        log(f"💓 Heartbeat: Futures символов: {len(futures_symbols)}, Spot: {len(spot_symbols)}")

def start_scalp_monitor():
    """Точка входа"""
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