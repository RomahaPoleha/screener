"""
Фоновый обновлятор NATR (только Futures)
ОПТИМИЗИРОВАНО: asyncio + aiohttp, batch-запись, один exchange.
"""
import ccxt
import time
import asyncio
import aiohttp
import json
import threading
import traceback
import sys
import os
from logging.handlers import RotatingFileHandler
from django.core.cache import cache
from datetime import datetime

MIN_VOLUME = 200000

NATR_TIMEFRAMES = {
    '5m14': {'tf': '5m', 'period': 14, 'limit': 20, 'interval': '5m'},
    '1m30': {'tf': '1m', 'period': 30, 'limit': 35, 'interval': '1m'},
}

UPDATE_INTERVALS = {
    '1m30': 180,
    '5m14': 900,
}

last_update_times = {
    '1m30': 0,
    '5m14': 0,
}

CACHE_TTL = 1200

# ==========================================
# ЛОГИРОВАНИЕ
# ==========================================
LOG_DIR = '/app/data'
LOG_FILE = os.path.join(LOG_DIR, 'natr_updater.log')
os.makedirs(LOG_DIR, exist_ok=True)

_natr_logger = __import__('logging').getLogger('natr_updater')
_natr_logger.setLevel(__import__('logging').INFO)

_rotating_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8'
)
_rotating_handler.setFormatter(__import__('logging').Formatter('%(asctime)s - %(message)s'))
_natr_logger.addHandler(_rotating_handler)

_console_handler = __import__('logging').StreamHandler()
_console_handler.setFormatter(__import__('logging').Formatter('%(message)s'))
_natr_logger.addHandler(_console_handler)


def log(msg):
    _natr_logger.info(msg)


def setup_excepthook():
    def excepthook(exc_type, exc_value, exc_tb):
        log(f"❌ НЕОБРАБОТАННОЕ ИСКЛЮЧЕНИЕ: {exc_type.__name__}: {exc_value}")
        log(''.join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    sys.excepthook = excepthook


# ==========================================
# ГЛОБАЛЬНЫЙ EXCHANGE (создаётся ОДИН раз)
# ✅ ИСПРАВЛЕНО: один экземпляр для всех
# ==========================================
_exchange = None

def get_exchange():
    global _exchange
    if _exchange is None:
        _exchange = ccxt.binance({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': 'future'}
        })
    return _exchange


def calculate_natr(ohlcv, period=14):
    try:
        if len(ohlcv) < period + 1:
            return None
        tr_values = []
        for i in range(1, len(ohlcv)):
            _, _, h, l, c, _ = ohlcv[i]
            _, _, _, _, c_prev, _ = ohlcv[i - 1]
            tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
            tr_values.append(tr)
        atr = sum(tr_values[-period:]) / period
        last_close = ohlcv[-1][4]
        if last_close == 0:
            return None
        return round(atr / last_close * 100, 4)
    except Exception as e:
        log(f"❌ Ошибка calculate_natr: {e}")
        return None


def calculate_natr_from_raw(raw_klines, period=14):
    """Рассчитывает NATR из сырых данных Binance API"""
    try:
        if len(raw_klines) < period + 1:
            return None
        tr_values = []
        for i in range(1, len(raw_klines)):
            h = float(raw_klines[i][2])
            l = float(raw_klines[i][3])
            c_prev = float(raw_klines[i - 1][4])
            tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
            tr_values.append(tr)
        atr = sum(tr_values[-period:]) / period
        last_close = float(raw_klines[-1][4])
        if last_close == 0:
            return None
        return round(atr / last_close * 100, 4)
    except Exception:
        return None


def is_valid_symbol(symbol, market_type):
    if '-' in symbol:
        return False
    if len(symbol) < 1 or len(symbol) > 15:
        return False
    if not symbol.replace('_', '').isalnum():
        return False
    return True


def get_symbols_from_tickers(market_type='future'):
    """Получает список монет — использует глобальный exchange"""
    log(f"🔥 get_symbols_from_tickers({market_type}) СТАРТ")
    try:
        # ✅ ИСПРАВЛЕНО: переиспользуем глобальный exchange
        exchange = get_exchange()
        tickers = exchange.fetch_tickers()
        log(f"✅ Получено {len(tickers)} тикеров для {market_type}")

        symbols_with_volume = []
        for symbol, data in tickers.items():
            if ':USDT' not in symbol and not symbol.endswith('/USDT'):
                continue
            volume = data.get('quoteVolume') or 0
            if volume < MIN_VOLUME:
                continue
            clean_symbol = symbol.replace('/USDT', '').replace(':USDT', '')
            if not is_valid_symbol(clean_symbol, market_type):
                continue
            symbols_with_volume.append((clean_symbol, volume))

        symbols_with_volume.sort(key=lambda x: x[1], reverse=True)
        symbols = [s[0] for s in symbols_with_volume]
        log(f"✅ {market_type.upper()}: найдено {len(symbols)} монет с объёмом > ${MIN_VOLUME / 1000:.0f}K")
        return symbols
    except Exception as e:
        log(f"❌ Ошибка в get_symbols_from_tickers({market_type}): {e}")
        log(traceback.format_exc())
        return []


# ==========================================
# ✅ НОВОЕ: asyncio batch-fetch через aiohttp
# ==========================================
async def fetch_natr_batch_async(symbols, tf_config, period):
    """Параллельная загрузка OHLCV и расчёт NATR через aiohttp"""
    interval = tf_config['interval']
    limit = tf_config['limit']
    semaphore = asyncio.Semaphore(5)  # Не более 5 параллельных запросов

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=15),
        headers={'User-Agent': 'Mozilla/5.0'}
    ) as session:
        async def fetch_one(symbol):
            async with semaphore:
                try:
                    url = (
                        f"https://fapi.binance.com/fapi/v1/klines"
                        f"?symbol={symbol}USDT&interval={interval}&limit={limit}"
                    )
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            return symbol, None
                        data = await resp.json()
                        natr = calculate_natr_from_raw(data, period)
                        return symbol, natr
                except Exception:
                    return symbol, None

        tasks = [fetch_one(s) for s in symbols]
        results = await asyncio.gather(*tasks)
        return dict(results)


def update_natr_for_timeframe_sync(symbols, natr_key, config, current_time):
    """Синхронная обёртка — запускает asyncio batch"""
    log(f"🔄 [{natr_key}] Начало расчёта для {len(symbols)} монет (async batch)...")

    try:
        # ✅ Запускаем asyncio batch в отдельном event loop
        loop = asyncio.new_event_loop()
        try:
            natr_results = loop.run_until_complete(
                fetch_natr_batch_async(symbols, config, config['period'])
            )
        finally:
            loop.close()

        # ✅ Batch-запись в Redis через pipeline
        success_count = 0
        error_count = 0

        try:
            from django_redis import get_redis_connection
            conn = get_redis_connection('default')
            pipe = conn.pipeline()

            for symbol, natr_value in natr_results.items():
                if natr_value is not None:
                    cache_key = f"natr_{symbol}_future"
                    old_data = cache.get(cache_key) or {'ts': current_time}
                    old_data[f'natr_{natr_key}'] = natr_value
                    old_data['ts'] = current_time
                    pipe.setex(cache_key, CACHE_TTL, json.dumps(old_data))
                    success_count += 1
                else:
                    error_count += 1

            pipe.execute()
        except Exception as e:
            log(f"⚠️ Pipeline ошибка: {e}, fallback на поэлементную запись")
            # Fallback: поэлементная запись
            for symbol, natr_value in natr_results.items():
                if natr_value is not None:
                    try:
                        cache_key = f"natr_{symbol}_future"
                        old_data = cache.get(cache_key) or {'ts': current_time}
                        old_data[f'natr_{natr_key}'] = natr_value
                        old_data['ts'] = current_time
                        cache.set(cache_key, old_data, CACHE_TTL)
                        success_count += 1
                    except Exception:
                        error_count += 1
                else:
                    error_count += 1

        last_update_times[natr_key] = time.time()

        cache.set(
            f"natr_last_update_times_future",
            {
                '1m30': datetime.fromtimestamp(last_update_times.get('1m30', 0)).isoformat() if last_update_times.get('1m30') else None,
                '5m14': datetime.fromtimestamp(last_update_times.get('5m14', 0)).isoformat() if last_update_times.get('5m14') else None,
            },
            CACHE_TTL
        )

        log(f"✅ [{natr_key}] Завершено: {success_count}/{len(symbols)} успешно, {error_count} ошибок")

    except Exception as e:
        log(f"❌ [{natr_key}] Критическая ошибка: {e}")
        log(traceback.format_exc())


def update_natr_futures():
    """Запускает обновление для всех таймфреймов"""
    current_time = time.time()

    timeframes_to_update = []
    for tf_key, interval in UPDATE_INTERVALS.items():
        if current_time - last_update_times[tf_key] >= interval:
            timeframes_to_update.append(tf_key)

    if not timeframes_to_update:
        log(f"ℹ️ Все таймфреймы актуальны, пропускаем цикл")
        return

    log(f"🔄 Обновляем таймфреймы: {timeframes_to_update}")

    try:
        symbols = get_symbols_from_tickers('future')
        if not symbols:
            log(f"⚠️ Нет монет для расчёта NATR")
            return

        queue_key = "natr_queue_future"
        cache.set(queue_key, {
            'symbols': symbols,
            'pointer': len(symbols),
            'last_update': datetime.now().isoformat()
        }, CACHE_TTL)

        # ✅ Запускаем потоки для каждого таймфрейма
        threads = []
        for tf_key in timeframes_to_update:
            config = NATR_TIMEFRAMES[tf_key]
            thread = threading.Thread(
                target=update_natr_for_timeframe_sync,
                args=(symbols, tf_key, config, current_time),
                name=f'NATR-{tf_key}',
                daemon=True
            )
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        log(f"🎉 Все таймфреймы обновлены!")

    except Exception as e:
        log(f"❌ Критическая ошибка обновления NATR: {e}")
        log(traceback.format_exc())


shutdown_event = threading.Event()


def natr_updater_loop():
    setup_excepthook()
    log("🚀 NATR Updater запущен (async batch режим)!")
    log(f"📝 Лог-файл: {LOG_FILE}")
    time.sleep(10)

    heartbeat_counter = 0

    while not shutdown_event.is_set():
        try:
            heartbeat_counter += 1
            if heartbeat_counter % 6 == 0:
                log(f"💓 NATR Updater: heartbeat (цикл {heartbeat_counter})")

            try:
                update_natr_futures()
            except Exception as e:
                log(f"❌ FUTURES упал: {e}")
                log(traceback.format_exc())

            if shutdown_event.is_set():
                break

            log(f"💤 NATR Updater: сон 10 секунд...")
            if shutdown_event.wait(timeout=10):
                log("🛑 Получен сигнал остановки, завершаем цикл...")
                break

        except Exception as e:
            log(f"❌ Ошибка в цикле NATR Updater: {e}")
            log(traceback.format_exc())
            if shutdown_event.wait(timeout=60):
                break


def start_natr_updater():
    thread = threading.Thread(
        target=natr_updater_loop,
        name='NATR-Updater',
        daemon=True
    )
    thread.start()
    log(f"✅ NATR Updater поток запущен (daemon=True, PID: {thread.ident})")


def stop_natr_updater():
    log("📤 Отправка сигнала остановки NATR Updater...")
    shutdown_event.set()
