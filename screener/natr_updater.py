"""
Фоновый обновлятор NATR
Считает для ВСЕХ ликвидных монет (объём > $200K)
"""
import ccxt
import time
import logging
import threading
import traceback
import sys
from django.core.cache import cache
from datetime import datetime
import os
from logging.handlers import RotatingFileHandler

# Минимальный объём за 24ч
MIN_VOLUME = 200000

# Таймфреймы для NATR
NATR_TIMEFRAMES = {
    '5m14': {'tf': '5m', 'period': 14, 'limit': 20},
    '1m30': {'tf': '1m', 'period': 30, 'limit': 35}
}

# Интервал обновления (20 минут)
UPDATE_INTERVAL = 1200

# TTL кэша (45 минут)
CACHE_TTL = 2700

# Настройка ротации логов
LOG_DIR = '/app/data'
LOG_FILE = os.path.join(LOG_DIR, 'natr_updater.log')

os.makedirs(LOG_DIR, exist_ok=True)

# Создаём logger с ротацией
_natr_logger = __import__('logging').getLogger('natr_updater')
_natr_logger.setLevel(__import__('logging').INFO)

# RotatingFileHandler: макс 10 МБ, храним 5 архивных файлов
_rotating_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=10 * 1024 * 1024,  # 10 МБ
    backupCount=5,
    encoding='utf-8'
)
_rotating_handler.setFormatter(__import__('logging').Formatter('%(asctime)s - %(message)s'))
_natr_logger.addHandler(_rotating_handler)

# Также выводим в stdout (видно в логах Amvera)
_console_handler = __import__('logging').StreamHandler()
_console_handler.setFormatter(__import__('logging').Formatter('%(message)s'))
_natr_logger.addHandler(_console_handler)


def log(msg):
    """Логирует через RotatingFileHandler (stdout + файл с ротацией)"""
    _natr_logger.info(msg)


def setup_excepthook():
    """Перехватывает необработанные исключения"""
    def excepthook(exc_type, exc_value, exc_tb):
        log(f"❌ НЕОБРАБОТАННОЕ ИСКЛЮЧЕНИЕ: {exc_type.__name__}: {exc_value}")
        log(''.join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    sys.excepthook = excepthook


def calculate_natr(ohlcv, period=14):
    """Рассчитывает NATR в процентах с 4 знаками"""
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
    """Фильтрует невалидные символы"""
    # Пропускаем фьючерсы с датой экспирации (BTC-260925, ETH-260626 и т.д.)
    if '-' in symbol:
        return False

    # Пропускаем слишком короткие или странные символы
    if len(symbol) < 2 or len(symbol) > 15:
        return False

    # Только буквы и цифры
    if not symbol.replace('_', '').isalnum():
        return False

    return True


def get_symbols_from_tickers(market_type='spot'):
    """Получает список живых монет из тикеров Binance"""
    log(f"🔥 get_symbols_from_tickers({market_type}) СТАРТ")

    try:
        exchange_config = {
            'enableRateLimit': True,
            'timeout': 10000
        }
        if market_type == 'future':
            exchange_config['options'] = {'defaultType': 'future'}

        exchange = ccxt.binance(exchange_config)
        tickers = exchange.fetch_tickers()

        log(f"✅ Получено {len(tickers)} тикеров для {market_type}")

        symbols_with_volume = []
        for symbol, data in tickers.items():
            if market_type == 'future':
                if ':USDT' not in symbol and not symbol.endswith('/USDT'):
                    continue
            else:
                if not symbol.endswith('/USDT') or ':USDT' in symbol:
                    continue

            volume = data.get('quoteVolume') or 0
            if volume < MIN_VOLUME:
                continue

            clean_symbol = symbol.replace('/USDT', '').replace(':USDT', '')

            # ← ФИЛЬТРАЦИЯ невалидных символов
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


def update_natr_for_market(market_type='spot'):
    """Обновляет NATR для всех ликвидных монет одного рынка"""
    log(f"🔄 Начинаю расчёт NATR для {market_type}...")

    try:
        symbols = get_symbols_from_tickers(market_type)
        if not symbols:
            log(f"⚠️ Нет монет для расчёта NATR ({market_type})")
            return

        # Проверяем, есть ли уже свежий natr_queue
        queue_key = f"natr_queue_{market_type}"
        existing_queue = cache.get(queue_key)

        if existing_queue and existing_queue.get('symbols') == symbols:
            # Список не изменился — НЕ перезаписываем метаданные!
            log(f"ℹ️ {market_type.upper()}: список не изменился, метаданные не перезаписываем")
        else:
            # Список изменился или его нет — сохраняем
            cache.set(queue_key, {
                'symbols': symbols,
                'pointer': len(symbols),
                'last_update': datetime.now().isoformat()
            }, CACHE_TTL)
            log(f"💾 {market_type.upper()}: сохранили новые метаданные ({len(symbols)} монет)")

        exchange_config = {
            'enableRateLimit': True,
            'timeout': 10000
        }
        if market_type == 'future':
            exchange_config['options'] = {'defaultType': 'future'}

        exchange = ccxt.binance(exchange_config)

        success_count = 0
        error_count = 0
        total = len(symbols)

        for idx, symbol in enumerate(symbols, 1):
            try:
                natr_results = {'ts': time.time()}
                symbol_ok = True

                for natr_key, config in NATR_TIMEFRAMES.items():
                    try:
                        if market_type == 'future':
                            pair = f"{symbol}/USDT:USDT"
                        else:
                            pair = f"{symbol}/USDT"

                        ohlcv = exchange.fetch_ohlcv(
                            pair,
                            timeframe=config['tf'],
                            limit=config['limit']
                        )

                        natr_value = calculate_natr(ohlcv, config['period'])

                        if natr_value is not None:
                            natr_results[f'natr_{natr_key}'] = natr_value
                        else:
                            natr_results[f'natr_{natr_key}'] = None

                        time.sleep(0.1)

                    except ccxt.RateLimitExceeded:
                        log(f"⚠️ Rate limit на {symbol}, жду 60 сек...")
                        time.sleep(60)
                        symbol_ok = False
                        break
                    except Exception as e:
                        error_count += 1
                        if '418' in str(e) or 'ban' in str(e).lower():
                            log(f"⛔ БАН! Останавливаем на 10 минут")
                            time.sleep(600)
                            return
                        # Тихо пропускаем проблемные монеты
                        natr_results[f'natr_{natr_key}'] = None

                if symbol_ok and (natr_results.get('natr_5m14') is not None or natr_results.get('natr_1m30') is not None):
                    cache_key = f"natr_{symbol}_{market_type}"
                    cache.set(cache_key, natr_results, CACHE_TTL)
                    success_count += 1

                if idx % 50 == 0:
                    log(f"  Прогресс {market_type}: {idx}/{total} ({idx*100//total}%) | ✅ {success_count} | ❌ {error_count}")

            except Exception as e:
                log(f"❌ Критическая ошибка для монеты {symbol}: {e}")
                error_count += 1
                continue

        log(f"✅ NATR для {market_type}: {success_count}/{total} успешно, {error_count} ошибок")

    except Exception as e:
        log(f"❌ Критическая ошибка обновления NATR {market_type}: {e}")
        log(traceback.format_exc())


# Глобальное событие для корректной остановки потока
shutdown_event = threading.Event()


def natr_updater_loop():
    """Бесконечный цикл обновления NATR с graceful shutdown"""
    setup_excepthook()
    log("🚀 NATR Updater запущен!")
    log(f"📝 Лог-файл: {LOG_FILE}")
    time.sleep(10)

    heartbeat_counter = 0

    # ← ИЗМЕНЕНО: проверяем флаг остановки
    while not shutdown_event.is_set():
        try:
            heartbeat_counter += 1
            if heartbeat_counter % 6 == 0:
                log(f"💓 NATR Updater: heartbeat (цикл {heartbeat_counter})")

            # Обработка SPOT
            try:
                update_natr_for_market('spot')
            except Exception as e:
                log(f" SPOT упал: {e}")
                log(traceback.format_exc())

            if shutdown_event.is_set():
                break

            time.sleep(5)

            # Обработка FUTURE
            try:
                update_natr_for_market('future')
            except Exception as e:
                log(f"❌ FUTURE упал: {e}")
                log(traceback.format_exc())

            if shutdown_event.is_set():
                break

            log(f"💤 NATR Updater: сон {UPDATE_INTERVAL} секунд...")

            # ← ИЗМЕНЕНО: ждём либо таймаут, либо сигнал остановки
            if shutdown_event.wait(timeout=UPDATE_INTERVAL):
                log("🛑 Получен сигнал остановки, завершаем цикл...")
                break

        except Exception as e:
            log(f"❌ Ошибка в цикле NATR Updater: {e}")
            log(traceback.format_exc())
            if shutdown_event.wait(timeout=60):
                break


def start_natr_updater():
    """Запускает фоновый поток NATR Updater"""
    thread = threading.Thread(
        target=natr_updater_loop,
        name='NATR-Updater',
        daemon=True  # ← ИЗМЕНЕНО: было False. Теперь не блокирует выход из программы
    )
    thread.start()
    log(f"✅ NATR Updater поток запущен (daemon=True, PID: {thread.ident})")


def stop_natr_updater():
    """Отправляет сигнал остановки потоку"""
    log("📤 Отправка сигнала остановки NATR Updater...")
    shutdown_event.set()