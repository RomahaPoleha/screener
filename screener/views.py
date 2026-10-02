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
_volume_poller_started = False

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
    """Получает список монет с Binance Futures + RVOL и цена для алертов"""
    try:
        exchange = get_binance_exchange()

        # ✅ ЯВНО запрашиваем только фьючерсы (USDT-M).
        # Это предотвращает возврат спотовых тикеров в некоторых версиях CCXT.
        tickers = exchange.fetch_tickers(params={'type': 'future'})

        symbols_with_volume = []
        for symbol, data in tickers.items():
            # ✅ Жесткая фильтрация: оставляем только строки, заканчивающиеся на ':USDT'
            if not symbol.endswith(':USDT'):
                continue

            volume = data.get('quoteVolume') or 0
            if volume < MIN_VOLUME:
                continue

            # ✅ Более надежная очистка: "BTC/USDT:USDT" -> "BTC/USDT" -> "BTC"
            clean_symbol = symbol.split(':')[0].replace('/USDT', '')

            # Ваши существующие фильтры
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


@require_http_methods(["GET"])
def api_data(request):
    """API: список монет (только Futures) + ленивый старт RVOL поллера"""
    global _volume_poller_started

    # Ленивый старт поллера при первом запросе
    if not _volume_poller_started:
        _volume_poller_started = True

        def fetch_fn():
            exchange = get_binance_exchange()
            # ✅ Явно указываем тип рынка и здесь
            return exchange.fetch_tickers(params={'type': 'future'})

        coin_selection.start_volume_poller('binance_future', fetch_fn, coin_selection.clean_swap)

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
        # Единый формат ключей для ВСЕХ бирж: scalp:{market}:{exchange}:{symbol}
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
def api_impulse_alerts(request):
    """API: последние импульсы из Redis"""
    import time

    try:
        since = float(request.GET.get('since', 0))
    except (ValueError, TypeError):
        since = 0

    alerts = cache.get('impulse:recent') or []

    # Фильтруем только новые (после since)
    if since > 0:
        alerts = [a for a in alerts if a.get('timestamp', 0) > since]

    return JsonResponse({
        'alerts': alerts,
        'server_time': time.time(),
        'count': len(alerts),
    })

