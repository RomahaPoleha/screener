import ccxt
import time
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.core.cache import cache
from django.shortcuts import render
from datetime import datetime

# Минимальный объём для фильтрации
MIN_VOLUME = 200000


def get_symbols_from_tickers(market_type='future'):
    """Получает список монет с Binance"""
    try:
        exchange_config = {
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': market_type}
        }

        exchange = ccxt.binance(exchange_config)
        tickers = exchange.fetch_tickers()

        symbols_with_volume = []
        for symbol, data in tickers.items():
            if ':USDT' not in symbol and not symbol.endswith('/USDT'):
                continue

            volume = data.get('quoteVolume') or 0
            if volume < MIN_VOLUME:
                continue

            clean_symbol = symbol.replace('/USDT', '').replace(':USDT', '')

            # Фильтр невалидных символов
            if '-' in clean_symbol:
                continue
            if len(clean_symbol) < 2 or len(clean_symbol) > 15:
                continue
            if not clean_symbol.replace('_', '').isalnum():
                continue

            symbols_with_volume.append({
                'symbol': clean_symbol,
                'volume': volume,
                'change': round(data.get('percentage') or 0, 2)
            })

        # Сортировка по объёму
        symbols_with_volume.sort(key=lambda x: x['volume'], reverse=True)

        return symbols_with_volume

    except Exception as e:
        print(f"❌ Ошибка get_symbols_from_tickers: {e}")
        return []


@require_http_methods(["GET"])
def api_data(request):
    """API: список монет"""
    market = request.GET.get('market', 'future')

    # Кэш на 60 секунд
    cache_key = f"coins_{market}"
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse(cached, safe=False)

    coins = get_symbols_from_tickers(market)
    cache.set(cache_key, coins, 60)

    return JsonResponse(coins, safe=False)


@require_http_methods(["GET"])
def api_candles(request, symbol):
    """API: история свечей для графика"""
    tf = request.GET.get('tf', '1m')
    market = request.GET.get('market', 'future')

    # Проверяем кэш
    cache_key = f"candles_{symbol}_{tf}_{market}"
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse(cached, safe=False)

    try:
        if market == 'future':
            exchange = ccxt.binance({
                'enableRateLimit': True,
                'options': {'defaultType': 'future'},
                'timeout': 10000
            })
            pair = f"{symbol}/USDT:USDT"
        else:
            exchange = ccxt.binance({
                'enableRateLimit': True,
                'timeout': 10000
            })
            pair = f"{symbol}/USDT"

        ohlcv = exchange.fetch_ohlcv(pair, timeframe=tf, limit=500)

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

        # Кэш на 30 секунд
        cache.set(cache_key, candles, 30)

        return JsonResponse(candles, safe=False)

    except Exception as e:
        print(f"❌ Ошибка api_candles {symbol}: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["GET"])
def api_natr(request):
    """API: NATR данные"""
    market = request.GET.get('market', 'future')

    # Получаем список монет
    cache_key = f"coins_{market}"
    coins = cache.get(cache_key)
    if not coins:
        coins = get_symbols_from_tickers(market)
        cache.set(cache_key, coins, 60)

    # Собираем NATR для каждой монеты
    natr_data = {}
    for coin in coins:
        symbol = coin['symbol']
        cache_key = f"natr_{symbol}_{market}"
        data = cache.get(cache_key)
        if data:
            natr_data[symbol] = data

    # Время последнего обновления
    last_update_times = cache.get(f"natr_last_update_times_{market}", {})

    return JsonResponse({
        'natr': natr_data,
        'last_update_times': last_update_times
    })


def index(request):
    return render(request, 'screener/index.html')  # ← Добавь префикс