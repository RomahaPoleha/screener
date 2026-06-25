import ccxt
import time
from django.core.cache import cache
import threading


def update_futures_candle(symbol, tf):
    """Обновляет одну свечу для Futures"""
    try:
        exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'},
            'timeout': 10000
        })

        pair = f"{symbol}/USDT:USDT"
        ohlcv = exchange.fetch_ohlcv(pair, tf, limit=100)

        # Кэшируем на 5 секунд
        cache.set(f"futures_{symbol}_{tf}", ohlcv, 5)

        return ohlcv
    except Exception as e:
        print(f"Error {symbol}: {e}")
        return None


def get_futures_candle_cached(symbol, tf):
    """Получает свечи из кэша или обновляет"""
    cache_key = f"futures_{symbol}_{tf}"
    data = cache.get(cache_key)

    if data is None:
        # Если нет в кэше — обновляем
        data = update_futures_candle(symbol, tf)

    return data