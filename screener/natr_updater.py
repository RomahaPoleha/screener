"""
Фоновый обновлятор NATR (только Futures)
Параллельный расчёт для 1m и 5m таймфреймов
"""
import ccxt
import time
import threading
import traceback
import sys
import os
from logging.handlers import RotatingFileHandler
from django.core.cache import cache
from datetime import datetime

# Минимальный объём за 24ч
MIN_VOLUME = 200000

# Таймфреймы для NATR
NATR_TIMEFRAMES = {
    '5m14': {'tf': '5m', 'period': 14, 'limit': 20},
    '1m30': {'tf': '1m', 'period': 30, 'limit': 35}
}

# Интервалы обновления (разные для каждого таймфрейма)
UPDATE_INTERVALS = {
    '1m30': 180,   # 3 минуты для 1m
    '5m14': 900,   # 15 минут для 5m
}

# Время последнего обновления для каждого таймфрейма
last_update_times = {
    '1m30': 0,
    '5m14': 0,
}

# TTL кэша (20 минут)
CACHE_TTL = 1200

# Настройка ротации логов
LOG_DIR = '/app/data'
LOG_FILE = os.path.join(LOG_DIR, 'natr_updater.log')

os.makedirs(LOG_DIR, exist_ok=True)

_natr_logger = __import__('logging').getLogger('natr_updater')
_natr_logger.setLevel(__import__('logging').INFO)

_rotating_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding='utf-8'
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


def is_valid_symbol(symbol, market_type):
    if '-' in symbol:
        return False
    if len(symbol) < 2 or len(symbol) > 15:
        return False
    if not symbol.replace('_', '').isalnum():
        return False
    return True


def get_symbols_from_tickers(market_type='future'):
    log(f"🔥 get_symbols_from_tickers({market_type}) СТАРТ")

    try:
        exchange_config = {
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': 'future'}
        }

        exchange = ccxt.binance(exchange_config)
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


def update_natr_for_timeframe(symbols, natr_key, config, current_time):
    """Обновляет NATR для одного таймфрейма (отдельный поток)"""
    log(f"🔄 [{natr_key}] Начало расчёта для {len(symbols)} монет...")

    try:
        exchange = ccxt.binance({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': 'future'}
        })

        success_count = 0
        error_count = 0
        total = len(symbols)

        for idx, symbol in enumerate(symbols, 1):
            try:
                pair = f"{symbol}/USDT:USDT"

                ohlcv = exchange.fetch_ohlcv(
                    pair,
                    timeframe=config['tf'],
                    limit=config['limit']
                )

                natr_value = calculate_natr(ohlcv, config['period'])

                if natr_value is not None:
                    # Сохраняем только этот таймфрейм
                    cache_key = f"natr_{symbol}_future"
                    old_data = cache.get(cache_key) or {'ts': current_time}
                    old_data[f'natr_{natr_key}'] = natr_value
                    old_data['ts'] = current_time
                    cache.set(cache_key, old_data, CACHE_TTL)
                    success_count += 1
                else:
                    error_count += 1

                time.sleep(0.05)  # Минимальная задержка

                if idx % 100 == 0:
                    log(f"  [{natr_key}] Прогресс: {idx}/{total} ({idx*100//total}%) | ✅ {success_count} | ❌ {error_count}")

            except ccxt.RateLimitExceeded:
                log(f"⚠️ [{natr_key}] Rate limit на {symbol}, жду 30 сек...")
                time.sleep(30)
                error_count += 1
            except Exception as e:
                error_count += 1
                if '418' in str(e) or 'ban' in str(e).lower():
                    log(f"⛔ [{natr_key}] БАН! Останавливаем на 5 минут")
                    time.sleep(300)
                    return
                continue

        # Обновляем время последнего обновления
        last_update_times[natr_key] = current_time
        # Сохраняем в кэш для фронтенда
        from datetime import datetime
        cache.set(
            f"natr_last_update_times_future",
            {
                '1m30': datetime.fromtimestamp(last_update_times.get('1m30', 0)).isoformat() if last_update_times.get(
                    '1m30') else None,
                '5m14': datetime.fromtimestamp(last_update_times.get('5m14', 0)).isoformat() if last_update_times.get(
                    '5m14') else None,
            },
            CACHE_TTL
        )

        log(f"✅ [{natr_key}] Завершено: {success_count}/{total} успешно, {error_count} ошибок")

    except Exception as e:
        log(f"❌ [{natr_key}] Критическая ошибка: {e}")
        log(traceback.format_exc())


def update_natr_futures():
    """Запускает параллельное обновление для всех таймфреймов"""
    current_time = time.time()

    # Определяем, какие таймфреймы нужно обновить
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

        # Сохраняем метаданные
        queue_key = "natr_queue_future"
        cache.set(queue_key, {
            'symbols': symbols,
            'pointer': len(symbols),
            'last_update': datetime.now().isoformat()
        }, CACHE_TTL)

        # Запускаем параллельные потоки для каждого таймфрейма
        threads = []
        for tf_key in timeframes_to_update:
            config = NATR_TIMEFRAMES[tf_key]
            thread = threading.Thread(
                target=update_natr_for_timeframe,
                args=(symbols, tf_key, config, current_time),
                name=f'NATR-{tf_key}',
                daemon=True
            )
            threads.append(thread)
            thread.start()

        # Ждём завершения всех потоков
        for thread in threads:
            thread.join()

        log(f"🎉 Все таймфреймы обновлены!")

    except Exception as e:
        log(f"❌ Критическая ошибка обновления NATR: {e}")
        log(traceback.format_exc())


shutdown_event = threading.Event()


def natr_updater_loop():
    setup_excepthook()
    log("🚀 NATR Updater запущен (параллельный режим)!")
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

            log(f"💤 NATR Updater: сон 60 секунд...")

            if shutdown_event.wait(timeout=60):
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