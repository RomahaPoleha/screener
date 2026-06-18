"""
Фоновый обновлятор свечей для Futures
Обновляет только те монеты, которые сейчас смотрит пользователь
"""
import ccxt
import time
import threading
from django.core.cache import cache

# Глобальный словарь: {symbol: last_access_time}
active_symbols = {}

# TTL для активной монеты (10 минут бездействия)
SYMBOL_TTL = 600

# Интервал обновления свечей (2 секунды)
UPDATE_INTERVAL = 2

# Очистка старых символов (раз в 60 секунд)
CLEANUP_INTERVAL = 60
last_cleanup = time.time()


def add_active_symbol(symbol):
    """Добавляет монету в список или обновляет время последнего доступа"""
    active_symbols[symbol] = time.time()


def cleanup_old_symbols():
    """Удаляет символы, к которым не обращались больше SYMBOL_TTL"""
    global last_cleanup
    current_time = time.time()

    # Проверяем, пора ли делать очистку
    if current_time - last_cleanup < CLEANUP_INTERVAL:
        return

    to_remove = []
    for symbol, last_access in active_symbols.items():
        if current_time - last_access > SYMBOL_TTL:
            to_remove.append(symbol)

    for symbol in to_remove:
        del active_symbols[symbol]

    if to_remove:
        print(f"🧹 Candle Updater: удалено {len(to_remove)} старых символов (осталось {len(active_symbols)})")

    last_cleanup = current_time


def update_active_candles():
    """Бесконечный цикл обновления свечей"""
    print("🚀 Candle Updater запущен!")
    time.sleep(5)  # Даём Django запуститься

    exchange = ccxt.binance({
        'enableRateLimit': True,
        'options': {'defaultType': 'future'},
        'timeout': 5000
    })

    while True:
        try:
            cleanup_old_symbols()  # ← Чистим старые символы

            symbols_to_update = list(active_symbols.keys())
            if not symbols_to_update:
                time.sleep(1)
                continue

            for symbol in symbols_to_update:
                try:
                    pair = f"{symbol}/USDT:USDT"
                    ohlcv = exchange.fetch_ohlcv(pair, '1m', limit=100)

                    # Форматируем в тот же вид, что и views.py
                    candles = [
                        {
                            'time': int(ts / 1000),
                            'open': float(o),
                            'high': float(h),
                            'low': float(l),
                            'close': float(c)
                        }
                        for ts, o, h, l, c, v in ohlcv
                    ]

                    # Сохраняем в кэш на 10 секунд
                    cache_key = f"candles_{symbol}_1m_future"
                    cache.set(cache_key, candles, 10)

                    time.sleep(0.1)  # Пауза между запросами

                except Exception as e:
                    print(f"❌ Ошибка {symbol}: {e}")

            time.sleep(UPDATE_INTERVAL)

        except Exception as e:
            print(f"❌ Ошибка в Candle Updater: {e}")
            time.sleep(5)


def start_candle_updater():
    """Запускает фоновый поток"""
    thread = threading.Thread(
        target=update_active_candles,
        name='Candle-Updater',
        daemon=True
    )
    thread.start()
    print("✅ Candle Updater поток запущен")