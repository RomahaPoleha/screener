"""
Фоновый прогрев кэша свечей для топ-монет.
Запускается из apps.py, чтобы при переключении графика на фронте
свечи отдавались мгновенно из кэша.
"""
import time
import threading
from django.core.cache import cache
import ccxt

# Таймфреймы, которые нужно прогревать
TIMEFRAMES = ['1m', '5m', '15m', '1h']

# Максимальный возраст кэша по таймфреймам (синхронизирован с views.py)
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
    """Прогревает кэш свечей для списка монет по всем таймфреймам"""
    if not symbols:
        return

    exchange = _get_exchange()
    warmed = 0

    for symbol in symbols:
        pair = f"{symbol}/USDT:USDT"
        for tf in TIMEFRAMES:
            cache_key = f"candles_{symbol}_{tf}_future"

            # Проверяем, нужен ли прогрев
            cached = cache.get(cache_key)
            needs_warm = True
            if cached:
                try:
                    now_ts = int(time.time())
                    last_candle_ts = cached[-1]['time']
                    age = now_ts - last_candle_ts
                    max_age = MAX_CACHE_AGE.get(tf, 120)
                    # Если кэш свежий (меньше половины TTL), не трогаем
                    if age < max_age * 0.5:
                        needs_warm = False
                except (KeyError, IndexError, TypeError):
                    pass

            if not needs_warm:
                continue

            # Загружаем с биржи
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
                # TTL с запасом
                cache.set(cache_key, candles, MAX_CACHE_AGE[tf] * 2)
                warmed += 1
            except Exception as e:
                log_func(f"⚠️ candles_warmer {symbol} {tf}: {e}")
                continue

    if warmed > 0:
        log_func(f"🔥 candles_warmer: прогрето {warmed} кэшей свечей для {len(symbols)} монет")


def start_candles_warmer(log_func=print):
    """Запускает фоновый поток прогрева кэша"""

    def run():
        # Даём серверу 30 секунд на старт
        time.sleep(30)
        log_func("🔥 Candles warmer запущен")

        while True:
            try:
                # Берём топ-монеты из кэша (их заполняет api_data / поллер)
                top_coins = cache.get('coins_future', [])
                if not top_coins:
                    time.sleep(60)
                    continue

                # Берём топ-40 по объёму
                symbols = [c['symbol'] for c in top_coins[:40]]
                warm_candles_for_symbols(symbols, log_func)

            except Exception as e:
                log_func(f"❌ candles_warmer error: {e}")

            # Прогреваем каждые 2 минуты
            time.sleep(120)

    threading.Thread(target=run, daemon=True, name='candles-warmer').start()