import ccxt
import logging
from django.shortcuts import render
from django.http import JsonResponse
from django.core.cache import cache
from django.views.decorators.http import require_http_methods
from django.conf import settings


logger = logging.getLogger(__name__)

EXCHANGE_NAME = 'binance'
CACHE_TTL = 120


def get_raw_tickers(market_type='spot'):
    cache_key = f"{EXCHANGE_NAME}_{market_type}_raw_tickers"
    raw_data = cache.get(cache_key)
    if raw_data is not None:
        logger.info(f"[{market_type}] Взял из кэша: {len(raw_data)} пар")
        return raw_data

    try:
        exchange_config = {
            'enableRateLimit': True,
            'timeout': 10000
        }
        if market_type == 'future':
            exchange_config['options'] = {'defaultType': 'future'}
            logger.info("Подключаюсь к Binance Futures...")
        else:
            logger.info("Подключаюсь к Binance Spot...")

        exchange = getattr(ccxt, EXCHANGE_NAME)(exchange_config)
        logger.info(f"Запрашиваю тикеры ({market_type})...")
        raw_data = exchange.fetch_tickers()
        logger.info(f"Binance вернул: {len(raw_data)} пар")

        cache.set(cache_key, raw_data, CACHE_TTL)
        return raw_data
    except Exception as e:
        logger.error(f"Ошибка API {EXCHANGE_NAME} ({market_type}): {e}")
        return {}


def filter_data(raw_data, filters, market_type='spot'):
    results = []

    if market_type == 'future':
        usdt_pairs = {k: v for k, v in raw_data.items() if ':USDT' in k or k.endswith('/USDT')}
    else:
        usdt_pairs = {k: v for k, v in raw_data.items() if k.endswith('/USDT') and ':USDT' not in k}

    for symbol, data in usdt_pairs.items():
        if not data or data.get('last') is None:
            continue

        price = data.get('last')
        change = data.get('percentage') or 0
        volume = data.get('quoteVolume') or 0
        trades = int(data.get('info', {}).get('count', 0))

        if market_type == 'spot':
            if not data.get('bid') or not data.get('ask'):
                continue
            if volume is None or volume < 10000:
                continue
            if trades is None or trades < 50:
                continue
        else:
            if volume is not None and volume < 1000:
                continue
            if trades is not None and trades < 5:
                continue

        if 'min_change' in filters and change < filters['min_change']:
            continue
        if 'max_change' in filters and change > filters['max_change']:
            continue
        if 'min_volume' in filters and volume < filters['min_volume']:
            continue
        if 'search' in filters and filters['search']:
            if filters['search'].upper() not in symbol:
                continue

        clean_symbol = symbol.replace('/USDT', '').replace(':USDT', '')
        results.append({
            'symbol': clean_symbol,
            'price': price,
            'change': round(change, 2),
            'volume': round(volume, 2)
        })

    results.sort(key=lambda x: abs(x['change']), reverse=True)
    return results


@require_http_methods(["GET"])
def api_screener(request):
    logger.info("=== ЗАПРОС /api/data/ ===")
    market_type = request.GET.get('market', 'spot')
    if market_type not in ['spot', 'future']:
        market_type = 'spot'

    filters = {}
    if request.GET.get('search'):
        filters['search'] = request.GET['search']
    if request.GET.get('min_change'):
        try:
            filters['min_change'] = float(request.GET['min_change'])
        except:
            pass
    if request.GET.get('min_volume'):
        try:
            filters['min_volume'] = float(request.GET['min_volume'])
        except:
            pass

    raw = get_raw_tickers(market_type)
    if not raw:
        logger.error("RAW ДАННЫЕ ПУСТЫЕ!")
        return JsonResponse([], safe=False)

    data = filter_data(raw, filters, market_type)
    return JsonResponse(data, safe=False)


def index(request):
    return render(request, 'screener/index.html')


@require_http_methods(["GET"])
def api_candles(request, symbol: str):
    tf = request.GET.get('tf', '1m')
    market_type = request.GET.get('market', 'spot')

    valid_tfs = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w', '1M']
    if tf not in valid_tfs:
        tf = '1m'

    if market_type == 'future':
        pair_symbol = f"{symbol}/USDT:USDT"
    else:
        pair_symbol = f"{symbol}/USDT"

    cache_key = f"candles_{symbol}_{tf}_{market_type}"

    # 1. Добавляем монету в список активных (для фонового обновления)
    if market_type == 'future' and tf == '1m':
        from .candle_updater import add_active_symbol
        add_active_symbol(symbol)

    # 2. Пытаемся взять из кэша
    candles = cache.get(cache_key)
    if candles is not None:
        return JsonResponse(candles, safe=False)

    # 3. Если нет в кэше — запрашиваем напрямую (первый запуск)
    try:
        exchange_config = {
            'enableRateLimit': True,
            'timeout': 5000,
            'rateLimit': 100
        }
        if market_type == 'future':
            exchange_config['options'] = {'defaultType': 'future'}

        exchange = ccxt.binance(exchange_config)
        ohlcv = exchange.fetch_ohlcv(pair_symbol, timeframe=tf, limit=150)

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

        cache.set(cache_key, candles, 10)
        return JsonResponse(candles, safe=False)

    except ccxt.RateLimitExceeded:
        logger.warning(f"⚠️ Rate limit для {pair_symbol}")
        return JsonResponse({'error': 'Слишком много запросов'}, status=429)
    except Exception as e:
        logger.error(f"Ошибка {pair_symbol}: {e}")
        return JsonResponse({'error': 'Ошибка биржи'}, status=500)



@require_http_methods(["GET"])
def api_natr(request):
    """Просто читает NATR из кэша (фоновый процесс уже всё посчитал)"""
    market_type = request.GET.get('market', 'spot')

    # Берём метаданные
    queue_data = cache.get(f"natr_queue_{market_type}")
    if not queue_data:
        return JsonResponse({
            'natr': {},
            'progress': {'current': 0, 'total': 0},
            'status': 'initializing'
        }, safe=False)

    # Собираем NATR из кэша
    results = {}
    for symbol in queue_data['symbols']:
        data = cache.get(f"natr_{symbol}_{market_type}")
        if data:
            results[symbol] = data

    total = len(queue_data['symbols'])
    current = len(results)

    return JsonResponse({
        'natr': results,
        'progress': {
            'current': current,
            'total': total
        },
        'status': 'ready' if current == total else 'updating',
        'last_update': queue_data.get('last_update')
    }, safe=False)


@require_http_methods(["GET"])
def debug_cache(request):
    """Проверка кэша"""
    from django.core.cache import cache

    # Проверка записи/чтения
    cache.set('test_key', 'test_value', 60)
    value = cache.get('test_key')

    # Проверка NATR
    natr_spot = cache.get('natr_queue_spot')
    natr_future = cache.get('natr_queue_future')

    return JsonResponse({
        'test_write_read': value,
        'natr_spot_exists': natr_spot is not None,
        'natr_future_exists': natr_future is not None,
        'cache_backend': settings.CACHES['default']['BACKEND'],
    })