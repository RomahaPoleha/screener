import ccxt
import time
import json
import hashlib
from django.http import JsonResponse, HttpResponse, FileResponse, Http404
from django.views.decorators.http import require_http_methods
from django.core.cache import cache
from django.shortcuts import render
from pathlib import Path
from . import coin_selection

# ==========================================
# ГЛОБАЛЬНЫЙ EXCHANGE (создаётся ОДИН раз)
# ==========================================
_binance_exchange = None

def get_binance_exchange():
    """Ленивая инициализация exchange — создаётся ОДИН раз"""
    global _binance_exchange
    if _binance_exchange is None:
        _binance_exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'},
            'timeout': 10000
        })
    return _binance_exchange


# Максимальный возраст кэша по таймфреймам (в секундах)
MAX_CACHE_AGE = {
    '1m':  120,
    '5m':  360,
    '15m': 1080,
    '30m': 2160,
    '1h':  4320,
    '4h':  17280,
    '1d':  86400,
}

BASE_DIR = Path(__file__).resolve().parent.parent
MIN_VOLUME = 100_000

# ✅ ИСПРАВЛЕНО: активные биржи (MEXC отключён)
ACTIVE_EXCHANGES = ['binance', 'bybit', 'okx', 'gate', 'bitget']


def get_symbols_from_tickers():
    """Получает список монет с Binance Futures + RVOL и цена для алертов"""
    try:
        # ✅ ИСПРАВЛЕНО: переиспользуем глобальный exchange
        exchange = get_binance_exchange()
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
            if len(clean_symbol) < 1 or len(clean_symbol) > 15:
                continue
            if not clean_symbol.replace('_', '').isalnum():
                continue

            try:
                rvol = coin_selection.get_rvol(clean_symbol, volume)
            except Exception:
                rvol = 0.0

            price = float(data.get('last') or data.get('close') or 0)

            symbols_with_volume.append({
                'symbol': clean_symbol,
                'volume': volume,
                'change': round(data.get('percentage') or 0, 2),
                'rvol': round(rvol, 2),
                'price': price
            })

        symbols_with_volume.sort(key=lambda x: x['volume'], reverse=True)
        return symbols_with_volume

    except Exception as e:
        print(f"❌ Ошибка get_symbols_from_tickers: {e}")
        return []


_volume_poller_started = False

@require_http_methods(["GET"])
def api_data(request):
    """API: список монет (только Futures) + ленивый старт RVOL поллера"""
    global _volume_poller_started

    if not _volume_poller_started:
        _volume_poller_started = True

        def fetch_fn():
            exchange = get_binance_exchange()
            return exchange.fetch_tickers()

        coin_selection.start_volume_poller('binance_future', fetch_fn, coin_selection.clean_swap)

    cache_key = "coins_future"
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse(cached, safe=False)

    coins = get_symbols_from_tickers()
    cache.set(cache_key, coins, 60)

    return JsonResponse(coins, safe=False)


@require_http_methods(["GET"])
def api_candles(request, symbol):
    """API: история свечей с умным кэшированием по таймфрейму"""
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
                # ✅ Добавлены заголовки кэширования
                max_age_client = max_age // 2
                response = JsonResponse(cached, safe=False)
                response['Cache-Control'] = f'public, max-age={max_age_client}'
                return response
        except (KeyError, IndexError, TypeError):
            pass

    try:
        exchange = get_binance_exchange()
        pair = f"{symbol}/USDT:USDT"
        ohlcv = exchange.fetch_ohlcv(pair, timeframe=tf, limit=500)

        candles = [
            {
                'time': int(ts / 1000),
                'open': float(o),
                'high': float(h),
                'low': float(l),
                'close': float(c),
                'volume': float(v)
            }
            for ts, o, h, l, c, v in ohlcv
        ]

        cache.set(cache_key, candles, 300)

        max_age_client = MAX_CACHE_AGE.get(tf, 120) // 2
        response = JsonResponse(candles, safe=False)
        response['Cache-Control'] = f'public, max-age={max_age_client}'
        return response

    except ccxt.BadSymbol as e:
        print(f"⚠️ {symbol} не найден: {e}")
        return JsonResponse({'error': f'{symbol} недоступен'}, status=404)
    except Exception as e:
        print(f"❌ Ошибка api_candles {symbol}: {e}")
        if cached:
            return JsonResponse(cached, safe=False)
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["GET"])
def api_natr(request):
    """API: NATR данные (только Futures)"""
    cache_key = "coins_future"
    coins = cache.get(cache_key)
    if not coins:
        coins = get_symbols_from_tickers()
        cache.set(cache_key, coins, 60)

    # ✅ ИСПРАВЛЕНО: batch-чтение через get_many (один запрос к Redis вместо N)
    natr_cache_keys = [f"natr_{coin['symbol']}_future" for coin in coins]
    symbol_keys_map = {f"natr_{coin['symbol']}_future": coin['symbol'] for coin in coins}

    natr_batch = cache.get_many(natr_cache_keys)

    natr_data = {}
    for cache_key_natr, data in natr_batch.items():
        symbol = symbol_keys_map.get(cache_key_natr)
        if symbol and data:
            natr_data[symbol] = data

    last_update_times = cache.get("natr_last_update_times_future", {})

    return JsonResponse({
        'natr': natr_data,
        'last_update_times': last_update_times
    })


@require_http_methods(["GET"])
def api_scalp(request, symbol):
    """API: плотности из Redis"""
    try:
        min_volume = int(request.GET.get('min_volume', 10000))
    except ValueError:
        min_volume = 10000

    try:
        limit_per_exchange = int(request.GET.get('limit', 50))
    except ValueError:
        limit_per_exchange = 50

    limit_per_exchange = max(1, min(limit_per_exchange, 50))

    market = request.GET.get('market', 'futures')
    if market not in ['futures', 'spot']:
        market = 'futures'

    now = time.time()
    symbol_upper = symbol.upper()

    # ✅ ИСПРАВЛЕНО: используем ACTIVE_EXCHANGES вместо захардкоженного списка
    result_by_exchange = {ex: [] for ex in ACTIVE_EXCHANGES}

    for exchange in ACTIVE_EXCHANGES:
        key = f"scalp:{market}:{exchange}:{symbol_upper}"
        data = cache.get(key)
        if not data:
            continue

        exchange_densities = []
        for item in data:
            try:
                price = item['price']
                volume = item['volume']
                timestamp = item['timestamp']
                side = item['side']
            except (KeyError, TypeError):
                continue

            if volume < min_volume:
                continue

            exchange_densities.append({
                'price': price,
                'volume': volume,
                'side': side,
                'age_seconds': round(now - timestamp, 1),
                'market': market,
                'exchange': item.get('exchange', exchange)
            })

        exchange_densities.sort(key=lambda x: x['volume'], reverse=True)
        result_by_exchange[exchange] = exchange_densities[:limit_per_exchange]

    densities = []
    for ex in ACTIVE_EXCHANGES:
        densities += result_by_exchange[ex]
    densities.sort(key=lambda x: x['volume'], reverse=True)

    return JsonResponse({
        'version': 'api_scalp_v3',
        'symbol': symbol_upper,
        'densities': densities,
        'market': market,
        'server_time': now,
        'counts': {ex: len(result_by_exchange[ex]) for ex in ACTIVE_EXCHANGES} | {'total': len(densities)},
        'by_exchange': {ex: result_by_exchange[ex] for ex in ACTIVE_EXCHANGES},
    })


SOUNDS_DIR = BASE_DIR / 'sounds'


def api_sound(request, filename):
    """Отдаёт аудиофайл из папки sounds"""
    # ✅ ИСПРАВЛЕНО: надёжная проверка path traversal через resolve()
    if '..' in filename or '/' in filename or '\\' in filename:
        raise Http404

    filepath = (SOUNDS_DIR / filename).resolve()
    sounds_dir_resolved = SOUNDS_DIR.resolve()

    # Проверяем что файл действительно внутри SOUNDS_DIR
    if not str(filepath).startswith(str(sounds_dir_resolved)):
        raise Http404

    if not filepath.exists() or not filepath.is_file():
        raise Http404(f'Звук не найден: {filename}')

    return FileResponse(
        open(filepath, 'rb'),
        content_type='audio/mpeg',
        as_attachment=False
    )


def api_logo(request):
    """Отдаёт картинку логотипа"""
    filepath = BASE_DIR / 'logo.png'
    if not filepath.exists():
        raise Http404('Логотип не найден')
    return FileResponse(open(filepath, 'rb'), content_type='image/png')


def index(request):
    """Главная страница"""
    return render(request, 'screener/index.html')


@require_http_methods(["GET"])
def api_scalp_debug(request, symbol):
    """Временная диагностика кэша для scalp"""
    symbol_upper = symbol.upper()

    keys = {
        'binance_futures': f"scalp:futures:binance:{symbol_upper}",
        'bybit_futures': f"scalp:futures:bybit:{symbol_upper}",
        'okx_futures': f"scalp:futures:okx:{symbol_upper}",
        'binance_spot': f"scalp:spot:binance:{symbol_upper}",
        'bybit_spot': f"scalp:spot:bybit:{symbol_upper}",
        'okx_spot': f"scalp:spot:okx:{symbol_upper}",
    }

    result = {}
    for name, key in keys.items():
        data = cache.get(key)
        result[name] = {
            'key': key,
            'exists': data is not None,
            'count': len(data) if data else 0,
            'sample': data[:3] if data else []
        }

    return JsonResponse(result)


@require_http_methods(["GET"])
def api_scalp_active(request):
    """Возвращает монеты, у которых сейчас есть плотности"""
    # ✅ ИСПРАВЛЕНО: правильные импорты (_async суффикс)
    from . import binance_monitor_async as binance_monitor
    from . import bybit_monitor_async as bybit_monitor

    active = {}

    all_symbols = set()
    all_symbols.update(binance_monitor.futures_symbols or [])
    all_symbols.update(binance_monitor.spot_symbols or [])
    all_symbols.update(bybit_monitor.bybit_futures_symbols or [])
    all_symbols.update(bybit_monitor.bybit_spot_symbols or [])

    for symbol in all_symbols:
        keys = [
            f"scalp:futures:binance:{symbol}",
            f"scalp:futures:bybit:{symbol}",
            f"scalp:futures:okx:{symbol}",
            f"scalp:spot:binance:{symbol}",
            f"scalp:spot:bybit:{symbol}",
            f"scalp:spot:okx:{symbol}",
        ]

        total_count = 0
        for key in keys:
            data = cache.get(key)
            if data:
                total_count += len(data)

        if total_count > 0:
            active[symbol] = total_count

    return JsonResponse({'active': active})


@require_http_methods(["GET"])
def api_mexc_depth(request):
    """Прокси для MEXC стаканов (обход CORS)"""
    import requests as req

    market = request.GET.get('market', 'futures')
    symbol = request.GET.get('symbol', '').upper()

    if not symbol or market not in ['futures', 'spot']:
        return JsonResponse({'error': 'bad params'}, status=400)

    cache_key = f"mexc:depth:{market}:{symbol}"
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)

    if market == 'futures':
        url = f"https://contract.mexc.com/api/v1/contract/depth/{symbol}_USDT?limit=100"
    else:
        url = f"https://api.mexc.com/api/v3/depth?symbol={symbol}USDT&limit=100"

    try:
        res = req.get(url, timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
        if not res.ok:
            return JsonResponse({'bids': [], 'asks': []})
        data = res.json()
    except Exception as e:
        print(f"⚠️ api_mexc_depth({market} {symbol}): {e}")
        return JsonResponse({'bids': [], 'asks': []})

    inner = data.get('data') or data or {}
    raw_bids = inner.get('bids') or []
    raw_asks = inner.get('asks') or []

    def norm(levels):
        out = []
        for row in levels:
            try:
                if isinstance(row, dict):
                    p = float(row.get('p') or row.get('price') or 0)
                    q = abs(float(row.get('v') or row.get('vol') or 0))
                else:
                    p = float(row[0])
                    q = abs(float(row[1]))
                if p > 0 and q > 0:
                    out.append([p, q])
            except (ValueError, TypeError, IndexError):
                continue
        return out

    result = {'bids': norm(raw_bids), 'asks': norm(raw_asks)}
    cache.set(cache_key, result, 2)
    return JsonResponse(result)


@require_http_methods(["GET"])
def api_gate_depth(request):
    """Прокси для Gate.io стаканов (обход CORS)"""
    import requests as req

    market = request.GET.get('market', 'futures')
    symbol = request.GET.get('symbol', '').upper()

    if not symbol or market not in ['futures', 'spot']:
        return JsonResponse({'error': 'bad params'}, status=400)

    cache_key = f"gate:depth:{market}:{symbol}"
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)

    if market == 'futures':
        url = f"https://api.gateio.ws/api/v4/futures/usdt/order_book?contract={symbol}_USDT&limit=100"
    else:
        url = f"https://api.gateio.ws/api/v4/spot/order_book?currency_pair={symbol}_USDT&limit=100"

    try:
        res = req.get(url, timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
        if not res.ok:
            return JsonResponse({'bids': [], 'asks': []})
        data = res.json()
    except Exception as e:
        print(f"⚠️ api_gate_depth({market} {symbol}): {e}")
        return JsonResponse({'bids': [], 'asks': []})

    raw_bids = data.get('bids') or []
    raw_asks = data.get('asks') or []

    def norm(levels):
        out = []
        for row in levels:
            try:
                if isinstance(row, dict):
                    p = float(row.get('p') or row.get('price') or 0)
                    q = abs(float(row.get('s') or row.get('size') or 0))
                elif isinstance(row, (list, tuple)):
                    p = float(row[0])
                    q = abs(float(row[1]))
                else:
                    continue
                if p > 0 and q > 0:
                    out.append([p, q])
            except (ValueError, TypeError, IndexError):
                continue
        return out

    result = {'bids': norm(raw_bids), 'asks': norm(raw_asks)}
    cache.set(cache_key, result, 2)
    return JsonResponse(result)


# ==========================================
# МЕТРИКИ (для мониторинга нагрузки)
# ==========================================
@require_http_methods(["GET"])
def api_metrics(request):
    """Эндпоинт для мониторинга нагрузки сервера"""
    import os
    try:
        from . import binance_monitor_async as bm
        from . import bybit_monitor_async as bym

        metrics = {
            'binance_futures_symbols': len(getattr(bm, 'futures_symbols', [])),
            'binance_spot_symbols': len(getattr(bm, 'spot_symbols', [])),
            'binance_futures_books': sum(
                len(b.get('bids', {})) + len(b.get('asks', {}))
                for b in getattr(bm, 'binance_futures_order_books', {}).values()
            ),
            'binance_futures_queue': getattr(bm, 'binance_futures_message_queue', None),
            'bybit_futures_symbols': len(getattr(bym, 'bybit_futures_symbols', [])),
            'bybit_spot_symbols': len(getattr(bym, 'bybit_spot_symbols', [])),
        }

        # Размер очереди (безопасно)
        q = metrics.get('binance_futures_queue')
        if q is not None:
            metrics['binance_futures_queue_size'] = q.qsize()
        del metrics['binance_futures_queue']

        # RAM usage (Linux)
        try:
            import resource
            mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            metrics['memory_mb'] = round(mem_mb, 1)
        except Exception:
            pass

        return JsonResponse(metrics)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
