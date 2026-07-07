import ccxt
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.core.cache import cache
from django.shortcuts import render

# Минимальный объём для фильтрации
MIN_VOLUME = 200000


def get_symbols_from_tickers():
    """Получает список монет с Binance Futures"""
    try:
        exchange = ccxt.binance({
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': 'future'}
        })
        tickers = exchange.fetch_tickers()

        symbols_with_volume = []
        for symbol, data in tickers.items():
            if ':USDT' not in symbol:
                continue

            volume = data.get('quoteVolume') or 0
            if volume < MIN_VOLUME:
                continue

            clean_symbol = symbol.replace('/USDT', '').replace(':USDT', '')

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

        symbols_with_volume.sort(key=lambda x: x['volume'], reverse=True)
        return symbols_with_volume

    except Exception as e:
        print(f"❌ Ошибка get_symbols_from_tickers: {e}")
        return []


@require_http_methods(["GET"])
def api_data(request):
    """API: список монет (только Futures)"""
    cache_key = "coins_future"
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse(cached, safe=False)

    coins = get_symbols_from_tickers()
    cache.set(cache_key, coins, 60)

    return JsonResponse(coins, safe=False)


@require_http_methods(["GET"])
def api_candles(request, symbol):
    """API: история свечей (только Futures)"""
    tf = request.GET.get('tf', '1m')

    cache_key = f"candles_{symbol}_{tf}_future"
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse(cached, safe=False)

    try:
        exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'},
            'timeout': 10000
        })
        pair = f"{symbol}/USDT:USDT"

        ohlcv = exchange.fetch_ohlcv(pair, timeframe=tf, limit=500)

        candles = [
            {
                'time': int(ts / 1000),
                'open': float(o),
                'high': float(h),
                'low': float(l),
                'close': float(c),
                'volume': float(v)  # ← Добавляем объём
            }
            for ts, o, h, l, c, v in ohlcv
        ]

        cache.set(cache_key, candles, 30)
        return JsonResponse(candles, safe=False)

    except ccxt.BadSymbol as e:
        print(f"⚠️ {symbol} не найден: {e}")
        return JsonResponse({'error': f'{symbol} недоступен'}, status=404)
    except Exception as e:
        print(f"❌ Ошибка api_candles {symbol}: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["GET"])
def api_natr(request):
    """API: NATR данные (только Futures)"""
    cache_key = "coins_future"
    coins = cache.get(cache_key)
    if not coins:
        coins = get_symbols_from_tickers()
        cache.set(cache_key, coins, 60)

    natr_data = {}
    for coin in coins:
        symbol = coin['symbol']
        natr_cache_key = f"natr_{symbol}_future"
        data = cache.get(natr_cache_key)
        if data:
            natr_data[symbol] = data

    last_update_times = cache.get("natr_last_update_times_future", {})

    return JsonResponse({
        'natr': natr_data,
        'last_update_times': last_update_times
    })


@require_http_methods(["GET"])
def api_scalp(request, symbol):
    """API: плотности с временем жизни (из Django cache)"""
    from django.core.cache import cache

    min_future = int(request.GET.get('min_future', 200000))

    key = f"scalp:{symbol.upper()}"

    # Получаем все плотности из cache
    # Django cache использует тот же Redis что и NATR
    data = cache.get(key)

    if not data:
        return JsonResponse({
            'symbol': symbol.upper(),
            'densities': [],
            'server_time': time.time()
        })

    densities = []
    now = time.time()

    for item in data:
        try:
            price = item['price']
            volume = item['volume']
            timestamp = item['timestamp']
            side = item['side']
        except (KeyError, TypeError):
            continue

        age_seconds = now - timestamp

        if volume < min_future:
            continue

        densities.append({
            'price': price,
            'volume': volume,
            'side': side,
            'age_seconds': round(age_seconds, 1),
            'market': 'future'
        })

    densities.sort(key=lambda x: x['volume'], reverse=True)

    return JsonResponse({
        'symbol': symbol.upper(),
        'densities': densities[:20],
        'server_time': now
    })


def index(request):
    """Главная страница"""
    return render(request, 'screener/index.html')
