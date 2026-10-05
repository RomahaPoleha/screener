import ccxt.async_support as ccxt
from channels.db import database_sync_to_async
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.core.cache import cache
from django.shortcuts import render
from django.http import FileResponse, Http404
from pathlib import Path
import time
from . import coin_selection

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# --- Асинхронные хелперы для кэша ---
@database_sync_to_async
def get_cache(key):
    return cache.get(key)


@database_sync_to_async
def set_cache(key, value, timeout):
    cache.set(key, value, timeout)


# --- Глобальная сессия для requests ---
_http_session = None


def get_http_session():
    global _http_session
    if _http_session is None:
        _http_session = requests.Session()
        retry = Retry(total=1, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=40, max_retries=retry)
        _http_session.mount('http://', adapter)
        _http_session.mount('https://', adapter)
    return _http_session


# --- Глобальный exchange объект ---
_binance_exchange_future_async = None
_binance_exchange_future_sync = None
_volume_poller_started = False


async def get_binance_exchange_async():
    """Асинхронная ленивая инициализация exchange"""
    global _binance_exchange_future_async
    if _binance_exchange_future_async is None:
        _binance_exchange_future_async = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'},
            'timeout': 10000
        })
    return _binance_exchange_future_async


def get_binance_exchange_sync():
    """Синхронная ленивая инициализация exchange"""
    global _binance_exchange_future_sync
    if _binance_exchange_future_sync is None:
        import ccxt as sync_ccxt
        _binance_exchange_future_sync = sync_ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'},
        })
    return _binance_exchange_future_sync


# --- Константы ---
MAX_CACHE_AGE = {
    '1m': 120, '5m': 360, '15m': 1080, '30m': 2160,
    '1h': 4320, '4h': 17280, '1d': 86400,
}
BASE_DIR = Path(__file__).resolve().parent.parent
MIN_VOLUME = 100_000


# ==============================================================================
# СИНХРОННЫЕ VIEWS
# ==============================================================================

def index(request):
    return render(request, 'screener/index.html')


def get_sound(request, filename):
    sound_file = BASE_DIR / 'sounds' / filename
    try:
        return FileResponse(open(sound_file, 'rb'))
    except FileNotFoundError:
        raise Http404("Sound not found")


@require_http_methods(["GET"])
def api_data(request):
    global _volume_poller_started

    if not _volume_poller_started:
        _volume_poller_started = True

        def fetch_fn():
            exchange = get_binance_exchange_sync()
            return exchange.fetch_tickers(params={'type': 'future'})

        coin_selection.start_volume_poller('binance_future', fetch_fn, coin_selection.clean_swap)

    cache_key = "coins_future"
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse(cached, safe=False)

    coins = get_symbols_from_tickers_sync()
    cache.set(cache_key, coins, 60)

    return JsonResponse(coins, safe=False)


def get_symbols_from_tickers_sync():
    exchange = get_binance_exchange_sync()
    tickers = exchange.fetch_tickers(params={'type': 'future'})

    symbols_with_volume = []
    for symbol, data in tickers.items():
        if not symbol.endswith(':USDT'): continue
        volume = data.get('quoteVolume') or 0
        if volume < MIN_VOLUME: continue
        clean_symbol = symbol.split(':')[0].replace('/USDT', '')
        if '-' in clean_symbol or not (1 < len(clean_symbol) < 15) or not clean_symbol.replace('_',
                                                                                               '').isalnum(): continue
        try:
            rvol = coin_selection.get_rvol(clean_symbol, volume)
        except Exception:
            rvol = 0.0
        price = float(data.get('last') or data.get('close') or 0)
        symbols_with_volume.append({'symbol': clean_symbol, 'rvol': rvol, 'price': price, 'volume': volume})

    sorted_symbols = sorted(symbols_with_volume, key=lambda x: x['rvol'], reverse=True)
    return {'symbols': sorted_symbols}


@require_http_methods(["GET"])
def api_gate_depth(request):
    market = request.GET.get('market', 'futures')
    symbol = request.GET.get('symbol', '').upper()
    if not symbol or market not in ['futures', 'spot']:
        return JsonResponse({'error': 'bad params'}, status=400)
    cache_key = f"gate:depth:{market}:{symbol}"
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)
    limit = 50
    if market == 'futures':
        url = f"https://api.gateio.ws/api/v4/futures/usdt/order_book?contract={symbol}_USDT&limit={limit}"
    else:
        url = f"https://api.gateio.ws/api/v4/spot/order_book?currency_pair={symbol}_USDT&limit={limit}"
    try:
        res = get_http_session().get(url, timeout=3, headers={'User-Agent': 'Mozilla/5.0'})
        if not res.ok: return JsonResponse({'bids': [], 'asks': []})
        data = res.json()
    except Exception:
        return JsonResponse({'bids': [], 'asks': []})

    def norm(levels):
        out = []
        for row in levels:
            try:
                p = float(row.get('p') or row[0])
                q = abs(float(row.get('s') or row.get('size') or row[1]))
                if p > 0 and q > 0: out.append([p, q])
            except (ValueError, TypeError, IndexError):
                continue
        return out

    result = {'bids': norm(data.get('bids') or []), 'asks': norm(data.get('asks') or [])}
    cache.set(cache_key, result, 1)
    return JsonResponse(result)


@require_http_methods(["GET"])
def api_impulse_alerts(request):
    try:
        since = float(request.GET.get('since', 0))
    except (ValueError, TypeError):
        since = 0
    alerts = cache.get('impulse:recent') or []
    if since > 0:
        alerts = [a for a in alerts if a.get('timestamp', 0) > since]
    return JsonResponse({'alerts': alerts, 'server_time': time.time(), 'count': len(alerts)})


# ==============================================================================
# АСИНХРОННЫЕ VIEWS (ОПТИМИЗИРОВАННЫЕ)
# ==============================================================================

@require_http_methods(["GET"])
async def api_klines(request):
    symbol = request.GET.get('symbol', '').upper()
    tf = request.GET.get('tf', '15m')
    if not symbol or not tf or tf not in MAX_CACHE_AGE:
        return JsonResponse({'error': 'bad params'}, status=400)
    cache_key = f"klines:{symbol}:{tf}"
    cached = await get_cache(cache_key)
    if cached:
        return JsonResponse(cached, safe=False)
    limit = 500
    exchange = await get_binance_exchange_async()
    try:
        klines = await exchange.fetch_ohlcv(f"{symbol}/USDT", tf, limit=limit)
        await set_cache(cache_key, klines, MAX_CACHE_AGE[tf])
        return JsonResponse(klines, safe=False)
    except Exception as e:
        if cached: return JsonResponse(cached, safe=False)
        return JsonResponse({'error': str(e)}, status=500)
    finally:
        if exchange: await exchange.close()


@require_http_methods(["GET"])
async def api_binance_depth(request):
    symbol = request.GET.get('symbol', '').upper()
    if not symbol:
        return JsonResponse({'error': 'bad params'}, status=400)
    cache_key = f"depth:{symbol}"
    cached = await get_cache(cache_key)
    if cached:
        return JsonResponse(cached)
    limit = 100
    exchange = await get_binance_exchange_async()
    try:
        depth = await exchange.fetch_l2_order_book(f"{symbol}/USDT", limit)
        bids = [[float(p), float(v)] for p, v in depth['bids']]
        asks = [[float(p), float(v)] for p, v in depth['asks']]
        result = {'bids': bids, 'asks': asks}
        await set_cache(cache_key, result, 1)
        return JsonResponse(result)
    except Exception as e:
        if cached: return JsonResponse(cached)
        return JsonResponse({'error': str(e)}, status=500)
    finally:
        if exchange: await exchange.close()