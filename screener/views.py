import time
from pathlib import Path
from django.http import JsonResponse, FileResponse, Http404
from django.views.decorators.http import require_http_methods
from django.shortcuts import render
from django.core.cache import cache
from . import coin_selection



BASE_DIR = Path(__file__).resolve().parent.parent
SOUNDS_DIR = BASE_DIR / 'sounds'

MIN_VOLUME = 100_000

# Максимальный возраст кэша по таймфреймам (в секундах)
MAX_CACHE_AGE = {
    '1m': 120, '5m': 360, '15m': 1080, '30m': 2160,
    '1h': 4320, '4h': 17280, '1d': 86400,
}


def get_symbols_from_tickers_fallback():
    """
    ⚠️ ТОЛЬКО ДЛЯ АВАРИЙНЫХ СЛУЧАЕВ.
    В идеале эти данные должны приходить из фонового поллера, а не вычисляться во вьюхе.
    """
    try:
        import ccxt
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
            if '-' in clean_symbol or not (1 <= len(clean_symbol) <= 15) or not clean_symbol.replace('_', '').isalnum():
                continue

            try:
                rvol = coin_selection.get_rvol(clean_symbol, volume)
            except Exception:
                rvol = 0.0

            symbols_with_volume.append({
                'symbol': clean_symbol,
                'volume': volume,
                'change': round(data.get('percentage') or 0, 2),
                'rvol': round(rvol, 2),
                'price': float(data.get('last') or data.get('close') or 0)
            })

        symbols_with_volume.sort(key=lambda x: x['volume'], reverse=True)
        return symbols_with_volume
    except Exception as e:
        print(f"❌ Ошибка get_symbols_from_tickers_fallback: {e}")
        return []


@require_http_methods(["GET"])
def api_data(request):
    """API: список монет. Строго читает из кэша, не блокирует воркер."""
    cache_key = "coins_future"
    cached = cache.get(cache_key)

    if cached:
        return JsonResponse(cached, safe=False)

    # 🔧 Если кэша нет, делаем аварийный запрос (но лучше, чтобы его заполнял поллер из apps.py)
    coins = get_symbols_from_tickers_fallback()
    if coins:
        cache.set(cache_key, coins, 60)

    return JsonResponse(coins, safe=False)


@require_http_methods(["GET"])
def api_candles(request, symbol):
    tf = request.GET.get('tf', '1m')
    cache_key = f"candles_{symbol}_{tf}_future"
    cached = cache.get(cache_key)

    if cached:
        try:
            now_ts = int(time.time())
            last_candle_ts = cached[-1]['time']
            age = now_ts - last_candle_ts
            max_age = MAX_CACHE_AGE.get(tf, 120)
            if age < max_age:
                return JsonResponse(cached, safe=False)
        except (KeyError, IndexError, TypeError):
            pass

    # Синхронный запрос (как было изначально)
    try:
        import ccxt
        exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'},
            'timeout': 10000
        })
        pair = f"{symbol}/USDT:USDT"
        ohlcv = exchange.fetch_ohlcv(pair, timeframe=tf, limit=500)
        candles = [
            {'time': int(ts / 1000), 'open': float(o), 'high': float(h),
             'low': float(l), 'close': float(c), 'volume': float(v)}
            for ts, o, h, l, c, v in ohlcv
        ]
        cache.set(cache_key, candles, 300)
        return JsonResponse(candles, safe=False)
    except Exception as e:
        print(f"❌ Ошибка api_candles {symbol}: {e}")
        if cached:
            return JsonResponse(cached, safe=False)
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["GET"])
def api_natr(request):
    """API: NATR данные. 🔧 ОПТИМИЗИРОВАНО через get_many"""
    cache_key = "coins_future"
    coins = cache.get(cache_key)
    if not coins:
        coins = get_symbols_from_tickers_fallback()
        if coins:
            cache.set(cache_key, coins, 60)

    if not coins:
        return JsonResponse({'natr': {}, 'last_update_times': {}})

    # 🔧 Собираем все ключи и делаем ОДИН запрос к Redis вместо цикла
    natr_keys = [f"natr_{coin['symbol']}_future" for coin in coins]
    natr_results = cache.get_many(natr_keys)

    natr_data = {}
    for key, data in natr_results.items():
        if data:
            # key выглядит как "natr_BTC_future", извлекаем символ
            symbol = key.replace('natr_', '').replace('_future', '')
            natr_data[symbol] = data

    last_update_times = cache.get("natr_last_update_times_future", {})

    return JsonResponse({
        'natr': natr_data,
        'last_update_times': last_update_times
    })


@require_http_methods(["GET"])
def api_scalp(request, symbol):
    """API: плотности из Redis. 🔧 ОПТИМИЗИРОВАНО через get_many"""
    try:
        min_volume = int(request.GET.get('min_volume', 10000))
    except ValueError:
        min_volume = 10000

    try:
        limit_per_exchange = max(1, min(int(request.GET.get('limit', 50)), 50))
    except ValueError:
        limit_per_exchange = 50

    market = request.GET.get('market', 'futures')
    if market not in ['futures', 'spot']:
        market = 'futures'

    symbol_upper = symbol.upper()
    exchanges = ['binance', 'bybit', 'okx', 'gate', 'mexc', 'bitget']

    # 🔧 Формируем список всех ключей и делаем ОДИН запрос к Redis
    keys_to_fetch = [f"scalp:{market}:{ex}:{symbol_upper}" for ex in exchanges]
    cached_data = cache.get_many(keys_to_fetch)

    result_by_exchange = {ex: [] for ex in exchanges}
    now = time.time()

    for ex in exchanges:
        key = f"scalp:{market}:{ex}:{symbol_upper}"
        data = cached_data.get(key)
        if not data:
            continue

        exchange_densities = []
        for item in data:
            try:
                volume = item['volume']
                if volume < min_volume:
                    continue

                exchange_densities.append({
                    'price': item['price'],
                    'volume': volume,
                    'side': item['side'],
                    'age_seconds': round(now - item['timestamp'], 1),
                    'market': market,
                    'exchange': item.get('exchange', ex)
                })
            except (KeyError, TypeError):
                continue

        exchange_densities.sort(key=lambda x: x['volume'], reverse=True)
        result_by_exchange[ex] = exchange_densities[:limit_per_exchange]

    # Собираем общий список
    densities = []
    for ex in exchanges:
        densities.extend(result_by_exchange[ex])
    densities.sort(key=lambda x: x['volume'], reverse=True)

    return JsonResponse({
        'version': 'api_scalp_v3',
        'symbol': symbol_upper,
        'densities': densities,
        'market': market,
        'server_time': now,
        'counts': {ex: len(result_by_exchange[ex]) for ex in exchanges} | {'total': len(densities)},
        'by_exchange': result_by_exchange,
    })


@require_http_methods(["GET"])
def api_scalp_active(request):
    """Возвращает монеты, у которых сейчас есть плотности"""
    # Вместо импорта мониторов, читаем список активных монет из кэша
    active_coins = cache.get('active_scalp_coins', [])

    if not active_coins:
        # Фоллбэк: берём топ-монеты из coins_future
        coins = cache.get('coins_future', [])
        active_coins = [c['symbol'] for c in coins[:50]] if coins else []

    if not active_coins:
        return JsonResponse({'active': {}})

    # Формируем ключи и делаем один запрос
    keys_to_check = []
    for sym in active_coins:
        for market in ['futures', 'spot']:
            for ex in ['binance', 'bybit', 'okx', 'gate', 'mexc', 'bitget']:
                keys_to_check.append(f"scalp:{market}:{ex}:{sym}")

    cache_results = cache.get_many(keys_to_check)

    active = {}
    for key, data in cache_results.items():
        if data:
            symbol = key.split(':')[-1]
            active[symbol] = active.get(symbol, 0) + len(data)

    sorted_active = dict(sorted(active.items(), key=lambda item: item[1], reverse=True))
    return JsonResponse({'active': sorted_active})


def api_sound(request, filename):
    if '..' in filename or '/' in filename or '\\' in filename:
        raise Http404
    filepath = SOUNDS_DIR / filename
    if not filepath.exists():
        raise Http404(f'Звук не найден: {filename}')
    return FileResponse(open(filepath, 'rb'), content_type='audio/mpeg', as_attachment=False)


def api_logo(request):
    filepath = BASE_DIR / 'logo.png'
    if not filepath.exists():
        raise Http404('Логотип не найден')
    return FileResponse(open(filepath, 'rb'), content_type='image/png')


def index(request):
    return render(request, 'screener/index.html')


# ==============================================================================
# ПРОКСИ ДЛЯ СТАКАНОВ (CORS)
# ==============================================================================
@require_http_methods(["GET"])
def api_mexc_depth(request):
    import requests as req
    market = request.GET.get('market', 'futures')
    symbol = request.GET.get('symbol', '').upper()

    if not symbol or market not in ['futures', 'spot']:
        return JsonResponse({'error': 'bad params'}, status=400)

    cache_key = f"mexc:depth:{market}:{symbol}"
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)

    url = (f"https://contract.mexc.com/api/v1/contract/depth/{symbol}_USDT?limit=100"
           if market == 'futures' else f"https://api.mexc.com/api/v3/depth?symbol={symbol}USDT&limit=100")

    try:
        res = req.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
        if not res.ok:
            return JsonResponse({'bids': [], 'asks': []})
        data = res.json()
    except Exception:
        return JsonResponse({'bids': [], 'asks': []})

    inner = data.get('data') or data or {}

    def norm(levels):
        out = []
        for row in levels:
            try:
                if isinstance(row, dict):
                    p, q = float(row.get('p') or row.get('price') or 0), abs(float(row.get('v') or row.get('vol') or 0))
                else:
                    p, q = float(row[0]), abs(float(row[1]))
                if p > 0 and q > 0:
                    out.append([p, q])
            except:
                continue
        return out

    result = {'bids': norm(inner.get('bids') or []), 'asks': norm(inner.get('asks') or [])}
    cache.set(cache_key, result, 2)
    return JsonResponse(result)


@require_http_methods(["GET"])
def api_gate_depth(request):
    import requests as req
    market = request.GET.get('market', 'futures')
    symbol = request.GET.get('symbol', '').upper()

    if not symbol or market not in ['futures', 'spot']:
        return JsonResponse({'error': 'bad params'}, status=400)

    cache_key = f"gate:depth:{market}:{symbol}"
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)

    url = (f"https://api.gateio.ws/api/v4/futures/usdt/order_book?contract={symbol}_USDT&limit=100"
           if market == 'futures' else f"https://api.gateio.ws/api/v4/spot/order_book?currency_pair={symbol}_USDT&limit=100")

    try:
        res = req.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
        if not res.ok:
            return JsonResponse({'bids': [], 'asks': []})
        data = res.json()
    except Exception:
        return JsonResponse({'bids': [], 'asks': []})

    def norm(levels):
        out = []
        for row in levels:
            try:
                if isinstance(row, dict):
                    p, q = float(row.get('p') or 0), abs(float(row.get('s') or 0))
                else:
                    p, q = float(row[0]), abs(float(row[1]))
                if p > 0 and q > 0:
                    out.append([p, q])
            except:
                continue
        return out

    result = {'bids': norm(data.get('bids') or []), 'asks': norm(data.get('asks') or [])}
    cache.set(cache_key, result, 2)
    return JsonResponse(result)