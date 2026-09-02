import ccxt
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.core.cache import cache
from django.shortcuts import render
from django.http import FileResponse, Http404
from pathlib import Path
import time
from . import coin_selection

# Глобальный exchange объект — создаётся один раз
_binance_exchange_future = None

def get_binance_exchange():
    """Ленивая инициализация exchange (экономит 50-100мс на запрос)"""
    global _binance_exchange_future
    if _binance_exchange_future is None:
        _binance_exchange_future = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'},
            'timeout': 10000
        })
    return _binance_exchange_future


# Максимальный возраст кэша по таймфреймам (в секундах)
MAX_CACHE_AGE = {
    '1m':  120,     # 2 минуты
    '5m':  360,     # 6 минут
    '15m': 1080,    # 18 минут
    '30m': 2160,    # 36 минут
    '1h':  4320,    # 72 минуты
    '4h':  17280,   # 4.8 часа
    '1d':  86400,   # 24 часа
}


BASE_DIR = Path(__file__).resolve().parent.parent

# Минимальный объём для фильтрации
MIN_VOLUME = 100_000


def get_symbols_from_tickers():
    """Получает список монет с Binance Futures + RVOL для алертов"""
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

            # Считаем RVOL — использует историю, которую собирает coin_selection
            try:
                rvol = coin_selection.get_rvol(clean_symbol, volume)
            except Exception:
                rvol = 0.0

            symbols_with_volume.append({
                'symbol': clean_symbol,
                'volume': volume,
                'change': round(data.get('percentage') or 0, 2),
                'rvol': round(rvol, 2)
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



# ==========================================
# API ФУНКЦИЯ
# ==========================================
@require_http_methods(["GET"])
def api_candles(request, symbol):
    """API: история свечей с умным кэшированием по таймфрейму"""
    tf = request.GET.get('tf', '1m')
    cache_key = f"candles_{symbol}_{tf}_future"
    cached = cache.get(cache_key)

    # УМНЫЙ КЭШ: проверяем не только наличие, но и свежесть последней свечи
    if cached:
        try:
            now_ts = int(time.time())
            last_candle_ts = cached[-1]['time']
            age = now_ts - last_candle_ts
            max_age = MAX_CACHE_AGE.get(tf, 120)

            # age < 0 = свеча из будущего (рассинхронизация часов) — считаем свежей
            if age < max_age:
                return JsonResponse(cached, safe=False)
            # Иначе кэш устарел — идём за новыми данными
        except (KeyError, IndexError, TypeError):
            pass  # Кэш повреждён — идём за новыми данными

    # FETCH С БИРЖИ
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
        return JsonResponse(candles, safe=False)

    except ccxt.BadSymbol as e:
        print(f"⚠️ {symbol} не найден: {e}")
        return JsonResponse({'error': f'{symbol} недоступен'}, status=404)
    except Exception as e:
        print(f"❌ Ошибка api_candles {symbol}: {e}")
        # Если есть старый кэш — отдаём его даже устаревший (лучше чем 500)
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
    """API: плотности из Redis (Binance + Bybit + OKX)"""
    import time

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

    EXCHANGES = ['binance', 'bybit', 'okx', 'gate', 'mexc', 'bitget']
    result_by_exchange = {ex: [] for ex in EXCHANGES}

    for exchange in EXCHANGES:
        if exchange == 'binance':
            key = f"scalp:{market}:{symbol_upper}"
        else:
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
    for ex in EXCHANGES:
        densities += result_by_exchange[ex]
    densities.sort(key=lambda x: x['volume'], reverse=True)

    return JsonResponse({
        'version': 'api_scalp_v3',
        'symbol': symbol_upper,
        'densities': densities,
        'market': market,
        'server_time': now,
        'counts': {ex: len(result_by_exchange[ex]) for ex in EXCHANGES} | {'total': len(densities)},
        'by_exchange': {ex: result_by_exchange[ex] for ex in EXCHANGES},
    })



# Путь к папке со звуками (рядом с manage.py)
SOUNDS_DIR = BASE_DIR / 'sounds'


def api_sound(request, filename):
    """Отдаёт аудиофайл из папки sounds"""
    # Защита от path traversal
    if '..' in filename or '/' in filename or '\\' in filename:
        raise Http404

    filepath = SOUNDS_DIR / filename

    if not filepath.exists():
        raise Http404(f'Звук не найден: {filename}')

    return FileResponse(
        open(filepath, 'rb'),
        content_type='audio/mpeg',
        as_attachment=False
    )

def index(request):
    """Главная страница"""
    return render(request, 'screener/index.html')

@require_http_methods(["GET"])
def api_scalp_debug(request, symbol):
    """Временная диагностика кэша для scalp"""
    symbol_upper = symbol.upper()

    keys = {
        'binance_futures': f"scalp:futures:{symbol_upper}",
        'bybit_futures': f"scalp:futures:bybit:{symbol_upper}",
        'binance_spot': f"scalp:spot:{symbol_upper}",
        'bybit_spot': f"scalp:spot:bybit:{symbol_upper}",
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
    from . import binance_monitor
    from . import bybit_monitor

    active = {}

    # Собираем все мониторимые символы
    all_symbols = set()
    all_symbols.update(binance_monitor.futures_symbols or [])
    all_symbols.update(binance_monitor.spot_symbols or [])
    all_symbols.update(bybit_monitor.bybit_futures_symbols or [])
    all_symbols.update(bybit_monitor.bybit_spot_symbols or [])

    for symbol in all_symbols:
        keys = [
            f"scalp:futures:{symbol}",
            f"scalp:futures:bybit:{symbol}",
            f"scalp:spot:{symbol}",
            f"scalp:spot:bybit:{symbol}",
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

    # MEXC futures: {success:true, data:{bids:[{p,v}], asks:[{p,v}]}}
    # MEXC spot: {bids:[[p,q]], asks:[[p,q]]}
    inner = data.get('data') or data or {}
    raw_bids = inner.get('bids') or []
    raw_asks = inner.get('asks') or []

    # Нормализация в формат [[price, qty], ...]
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
            except:
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
            print(f"⚠️ api_gate_depth({market} {symbol}): HTTP {res.status_code}")
            return JsonResponse({'bids': [], 'asks': []})
        data = res.json()
    except Exception as e:
        print(f"⚠️ api_gate_depth({market} {symbol}): {e}")
        return JsonResponse({'bids': [], 'asks': []})

    # Gate отдаёт {current: timestamp, asks: [...], bids: [...]}
    raw_bids = data.get('bids') or []
    raw_asks = data.get('asks') or []

    # Нормализация в формат [[price, qty], ...]
    def norm(levels):
        out = []
        for row in levels:
            try:
                if isinstance(row, dict):
                    # Gate futures: {"p": "...", "s": "..."}
                    p = float(row.get('p') or row.get('price') or 0)
                    q = abs(float(row.get('s') or row.get('size') or 0))
                elif isinstance(row, (list, tuple)):
                    # Gate spot: ["price", "size"]
                    p = float(row[0])
                    q = abs(float(row[1]))
                else:
                    continue
                if p > 0 and q > 0:
                    out.append([p, q])
            except Exception:
                continue
        return out

    result = {'bids': norm(raw_bids), 'asks': norm(raw_asks)}
    cache.set(cache_key, result, 2)
    return JsonResponse(result)