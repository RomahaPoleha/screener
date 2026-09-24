"""
Фоновый прогрев кэша свечей.
Гарантирует, что при переключении графика данные отдаются из Redis мгновенно.
"""
import time
import threading
from django.core.cache import cache
import ccxt

TIMEFRAMES = ['1m', '5m', '15m', '1h']
MAX_CACHE_AGE = {
    '1m': 120, '5m': 360, '15m': 1080, '1h': 4320,
}

_exchange = None


def _get_exchange():
    global _exchange
    if _exchange is None:
        _exchange = ccxt.binance({
            'enableRateLimit': True,
            'timeout': 15000,
            'options': {'defaultType': 'future'}
        })
    return _exchange


def warm_candles_for_symbols(symbols, log_func=print):
    if not symbols:
        return

    exchange = _get_exchange()
    warmed_count = 0

    for symbol in symbols:
        pair = f"{symbol}/USDT:USDT"
        for tf in TIMEFRAMES:
            cache_key = f"candles_{symbol}_{tf}_future"

            # Проверяем, нужно ли обновлять
            cached = cache.get(cache_key)
            needs_warm = True
            if cached:
                try:
                    now_ts = int(time.time())
                    last_candle_ts = cached[-1]['time']
                    age = now_ts - last_candle_ts
                    max_age = MAX_CACHE_AGE.get(tf, 120)
                    # Если кэш свежий (меньше половины TTL), не трогаем его
                    if age < max_age * 0.5:
                        needs_warm = False
                except (KeyError, IndexError, TypeError):
                    pass

            if not needs_warm:
                continue

            # Загружаем с биржи и сохраняем в кэш
            try:
                ohlcv = exchange.fetch_ohlcv(pair, timeframe=tf, limit=500)
                candles = [
                    {
                        'time': int(ts / 1000),
                        'open': float(o), 'high': float(h),
                        'low': float(l), 'close': float(c), 'volume': float(v)
                    }
                    for ts, o, h, l, c, v in ohlcv
                ]
                # Сохраняем с запасом по времени
                cache.set(cache_key, candles, MAX_CACHE_AGE[tf] * 2)
                warmed_count += 1
            except Exception as e:
                log_func(f"⚠️ candles_warmer {symbol} {tf}: {e}")
                continue

    if warmed_count > 0:
        log_func(f"🔥 candles_warmer: прогрето {warmed_count} кэшей для {len(symbols)} монет")


def start_candles_warmer(log_func=print):
    def run():
        time.sleep(30)  # Даем Django 30 сек на полный старт
        log_func("🔥 Candles warmer запущен в фоне")

        while True:
            try:
                # Берем топ-монеты из кэша (их заполняет api_data или поллер)
                top_coins = cache.get('coins_future', [])
                if not top_coins:
                    time.sleep(60)
                    continue

                # Прогреваем свечи только для топ-40 монет по объему
                symbols = [c['symbol'] for c in top_coins[:40]]
                warm_candles_for_symbols(symbols, log_func)

            except Exception as e:
                log_func(f"❌ candles_warmer error: {e}")

            # Повторяем каждые 2 минуты
            time.sleep(120)

    threading.Thread(target=run, daemon=True, name='candles-warmer').start()