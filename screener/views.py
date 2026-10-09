import ccxt
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache
from django.shortcuts import render
from django.http import FileResponse, Http404
from pathlib import Path
import time
from . import coin_selection

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Глобальная сессия для переиспользования TCP-соединений (Keep-Alive)
# Это главный секрет ускорения повторных запросов к одним и тем же биржам
_http_session = None

def get_http_session():
    global _http_session
    if _http_session is None:
        _http_session = requests.Session()
        # Настройка пула соединений и быстрых повторных попыток при сбоях
        retry = Retry(total=1, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=40, max_retries=retry)
        _http_session.mount('http://', adapter)
        _http_session.mount('https://', adapter)
    return _http_session




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
            # ✅ НОВОЕ: Оценка количества сделок за 24ч
            # Приблизительно: deals ≈ volume / (price * 10)
            # Предполагаем средний размер сделки ~10 USDT
            estimated_deals = int(volume / max(price, 1) / 10) if price > 0 else 0

            symbols_with_volume.append({
                'symbol': clean_symbol,
                'volume': volume,
                'change': round(data.get('percentage') or 0, 2),
                'rvol': round(rvol, 2),
                'price': price,
                'deals': estimated_deals  # 💡 Количество сделок за 24ч (оценка)
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


@require_http_methods(["GET"])
def api_trades_count(request):
    """API: точный count сделок за 24ч для топ монет"""
    global _volume_poller_started
    
    # Ленивый старт - запускаем один раз
    if not _volume_poller_started:
        _volume_poller_started = True
    
    exchange = get_binance_exchange()
    
    # Берем только топ монет для экономии запросов
    # Получаем тикеры
    tickers = exchange.fetch_tickers(params={'type': 'future'})
    
    trades_data = {}
    for symbol, data in tickers.items():
        if not symbol.endswith(':USDT'):
            continue
        
        # Получаем count сделок из ticker info
        # у Binance есть поле 'count' в дополнительных данных (количество сделок за 24ч)
        info = data.get('info', {})
        trade_count = info.get('count', 0) if info else 0
        
        # Также можно использовать len(ticker['trades']) но это дорого
        # trade_count = len(data.get('trades', []))  # Too expensive
        
        if trade_count > 0:
            #_clean symbol
            clean_symbol = symbol.split(':')[0].replace('/USDT', '')
            if '-' not in clean_symbol and len(clean_symbol) <= 15:
                trades_data[clean_symbol] = trade_count
    
    return JsonResponse(trades_data)


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
def api_candles_history(request, symbol):
    """API: глубокая история свечей (до 1500 свечей)"""
    tf = request.GET.get('tf', '1m')
    
    # Определяем лимит в зависимости от таймфрейма (TradingView.подобные интервалы)
    limits = {
        '1m': 4320,   # 3 дня (1440 × 3)
        '5m': 2016,   # 1 неделя
        '15m': 2880,  # 1 месяц
        '30m': 1440,  # 1 месяц  
        '1h': 2160,   # 3 месяца
        '4h': 1080,   # 6 месяцев
        '1d': 365,    # 1 год
        '1w': 260,    # 5 лет
    }
    limit = limits.get(tf, 1000)
    
    cache_key = f"candles_hist_v2_{symbol}_{tf}_future"  # v2: для 1m теперь 3 дня (4320 свечей)
    cached = cache.get(cache_key)
    
    # Используем кэш, если он свежий (30 минут для истории)
    if cached:
        try:
            # Проверяем свежесть последней свечи
            now_ts = int(time.time())
            last_candle_ts = cached[-1]['time']
            if now_ts - last_candle_ts < 1800:  # 30 минут
                return JsonResponse(cached, safe=False)
        except (KeyError, IndexError, TypeError):
            pass  # Кэш повреждён
    
    try:
        exchange = get_binance_exchange()
        pair = f"{symbol}/USDT:USDT"
        
        # Binance имеет ограничение на количество свечей за один запрос
        # Для futures, максимальный лимит обычно 1000 свечей
        # Если запросили больше, делаем несколько запросов
        ohlcv = []
        
        if tf == '1m' and limit > 1000:
            # Для 1m делаем несколько запросов чтобы получить 3 дня (4320 свечей)
            # Binance Futures ограничивает 1000 свечей за запрос
            # Рассчитываем timestamp для 3 дней назад (в миллисекундах)
            import time
            now_ms = int(time.time() * 1000)
            three_days_ago_ms = now_ms - (3 * 24 * 60 * 60 * 1000)  # 3 дня назад
            
            # Получаем свечи порциями по 1000
            since = three_days_ago_ms
            batch_size = 1000
            
            while len(ohlcv) < limit:
                try:
                    batch = exchange.fetch_ohlcv(pair, timeframe=tf, since=since, limit=batch_size)
                    if not batch:
                        break
                    
                    ohlcv.extend(batch)
                    
                    # Если получили меньше чем запросили, значит данные закончились
                    if len(batch) < batch_size:
                        break
                    
                    # Устанавливаем since для следующего батча как время последней свечи + 1 минута
                    since = batch[-1][0] + 60 * 1000
                    
                    # Не зацикливаться
                    if len(ohlcv) >= limit or since > now_ms:
                        break
                        
                except Exception as e:
                    print(f"Ошибка при получении батча для {symbol} {tf}: {e}")
                    break
            
            # Ограничиваем лимитом и сортируем по времени (от старых к новым)
            ohlcv = ohlcv[:limit]
            ohlcv.sort(key=lambda x: x[0])  # Сортировка по timestamp
        else:
            # Для других таймфреймов один запрос
            ohlcv = exchange.fetch_ohlcv(pair, timeframe=tf, limit=limit)
        
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
        
        # Кэшируем на 1 час для истории
        cache.set(cache_key, candles, 3600)
        return JsonResponse(candles, safe=False)
    except Exception as e:
        # В случае ошибки попробуем отдать старый кэш, если он есть
        if cached:
            return JsonResponse(cached, safe=False)
        return JsonResponse({'error': str(e)}, status=500)
            
            
@require_http_methods(["GET"])
def api_scalp_active(request):
    """Возвращает монеты, у которых сейчас есть плотности"""
    from . import binance_monitor_async as binance_monitor
    from . import bybit_monitor_async as bybit_monitor

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
def api_mexc_depth(request):
    """Прокси для MEXC стаканов (обход CORS) - ОПТИМИЗИРОВАНО"""
    market = request.GET.get('market', 'futures')
    symbol = request.GET.get('symbol', '').upper()

    if not symbol or market not in ['futures', 'spot']:
        return JsonResponse({'error': 'bad params'}, status=400)

    cache_key = f"mexc:depth:{market}:{symbol}"

    # ✅ УМЕНЬШЕННЫЙ КЭШ: 1 секунда вместо 2.
    # Если данные есть и они свежее 1 сек, отдаем мгновенно.
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)

    # ✅ УМЕНЬШЕННЫЙ LIMIT: 50 вместо 100. Меньше данных по сети = быстрее парсинг.
    # Если вам нужно только ближайшее окружение, поставьте limit=20
    limit = 50

    if market == 'futures':
        url = f"https://contract.mexc.com/api/v1/contract/depth/{symbol}_USDT?limit={limit}"
    else:
        url = f"https://api.mexc.com/api/v3/depth?symbol={symbol}USDT&limit={limit}"

    try:
        # ✅ ИСПОЛЬЗУЕМ СЕССИЮ И ЖЕСТКИЙ ТАЙМАУТ (3 сек вместо 8)
        res = get_http_session().get(url, timeout=3, headers={'User-Agent': 'Mozilla/5.0'})
        if not res.ok:
            return JsonResponse({'bids': [], 'asks': []})
        data = res.json()
    except Exception as e:
        print(f"⚠️ api_mexc_depth({market} {symbol}): {e}")
        # При ошибке можно вернуть старый кэш, если он есть (даже протухший), чтобы не ломать фронт
        if cached:
            return JsonResponse(cached)
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
            except:
                continue
        return out

    result = {'bids': norm(raw_bids), 'asks': norm(raw_asks)}

    # ✅ Кэшируем на 1 секунду. Для скальпинга это оптимальный баланс.
    cache.set(cache_key, result, 1)
    return JsonResponse(result)


@require_http_methods(["GET"])
def api_gate_depth(request):
    """Прокси для Gate.io стаканов (обход CORS) - ОПТИМИЗИРОВАНО"""
    market = request.GET.get('market', 'futures')
    symbol = request.GET.get('symbol', '').upper()

    if not symbol or market not in ['futures', 'spot']:
        return JsonResponse({'error': 'bad params'}, status=400)

    cache_key = f"gate:depth:{market}:{symbol}"
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)

    limit = 50  # ✅ Уменьшено с 100 для ускорения

    if market == 'futures':
        url = f"https://api.gateio.ws/api/v4/futures/usdt/order_book?contract={symbol}_USDT&limit={limit}"
    else:
        url = f"https://api.gateio.ws/api/v4/spot/order_book?currency_pair={symbol}_USDT&limit={limit}"

    try:
        # ✅ ИСПОЛЬЗУЕМ СЕССИЮ И ТАЙМАУТ 3 сек
        res = get_http_session().get(url, timeout=3, headers={'User-Agent': 'Mozilla/5.0'})
        if not res.ok:
            print(f"⚠️ api_gate_depth({market} {symbol}): HTTP {res.status_code}")
            if cached:
                return JsonResponse(cached)
            return JsonResponse({'bids': [], 'asks': []})
        data = res.json()
    except Exception as e:
        print(f"⚠️ api_gate_depth({market} {symbol}): {e}")
        if cached:
            return JsonResponse(cached)
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
            except Exception:
                continue
        return out

    result = {'bids': norm(raw_bids), 'asks': norm(raw_asks)}

    # ✅ Кэшируем на 1 секунду
    cache.set(cache_key, result, 1)
    return JsonResponse(result)


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


@require_http_methods(["GET"])
def api_exchanges(request, symbol):
    """API: проверка доступности символа на биржах"""
    symbol = symbol.upper().strip()
    
    # Импортируем списки символов из всех мониторов
    from . import binance_monitor_async as binance_monitor
    from . import bybit_monitor_async as bybit_monitor
    from . import okx_monitor_async as okx_monitor
    from . import gate_monitor_async as gate_monitor
    from . import mexc_monitor_async as mexc_monitor
    from . import bitget_monitor_async as bitget_monitor
    
    # Нормализуем списки мониторов к верхнему регистру для сравнения
    def normalize_list(lst):
        return set(s.upper().strip() for s in (lst or []))
    
    binance_fut = normalize_list(binance_monitor.futures_symbols)
    binance_spot = normalize_list(binance_monitor.spot_symbols)
    bybit_fut = normalize_list(bybit_monitor.bybit_futures_symbols)
    bybit_spot = normalize_list(bybit_monitor.bybit_spot_symbols)
    okx_fut = normalize_list(okx_monitor.okx_futures_symbols)
    okx_spot = normalize_list(okx_monitor.okx_spot_symbols)
    gate_fut = normalize_list(gate_monitor.gate_futures_symbols)
    gate_spot = normalize_list(gate_monitor.gate_spot_symbols)
    mexc_fut = normalize_list(mexc_monitor.mexc_futures_symbols)
    mexc_spot = normalize_list(mexc_monitor.mexc_spot_symbols)
    bitget_fut = normalize_list(bitget_monitor.bitget_futures_symbols)
    bitget_spot = normalize_list(bitget_monitor.bitget_spot_symbols)
    
    result = {
        'binance': {
            'futures': symbol in binance_fut,
            'spot': symbol in binance_spot,
        },
        'bybit': {
            'futures': symbol in bybit_fut,
            'spot': symbol in bybit_spot,
        },
        'okx': {
            'futures': symbol in okx_fut,
            'spot': symbol in okx_spot,
        },
        'gate': {
            'futures': symbol in gate_fut,
            'spot': symbol in gate_spot,
        },
        'mexc': {
            'futures': symbol in mexc_fut,
            'spot': symbol in mexc_spot,
        },
        'bitget': {
            'futures': symbol in bitget_fut,
            'spot': symbol in bitget_spot,
        },
    }
    
    return JsonResponse(result)


# ==========================================
# КЭШИРОВАННЫЕ ПРОВЕРКИ СИМВОЛОВ НА ВСЕХ БИРЖАХ
# ==========================================
CACHE_TTL = 86400  # 24 часа (списки символов меняются редко)

def _get_cached(key, fetch_func, ttl=CACHE_TTL):
    """Универсальный хелпер: читает из кэша или вызывает fetch_func"""
    cached = cache.get(key)
    if cached is not None:
        return cached
    data = fetch_func()
    cache.set(key, data, ttl)
    return data

# --- Binance ---
def _fetch_binance_all():
    exchange = get_binance_exchange()
    fut_tickers = exchange.fetch_tickers(params={'type': 'future'})
    fut = set()
    for s, d in fut_tickers.items():
        if s.endswith(':USDT'):
            c = s.split(':')[0].replace('/USDT', '')
            if c and '-' not in c:
                fut.add(c)
    spot_tickers = exchange.fetch_tickers(params={'type': 'spot'})
    spot = set()
    for s, d in spot_tickers.items():
        if s.endswith('/USDT'):
            c = s.replace('/USDT', '')
            if c and '-' not in c:
                spot.add(c)
    return fut, spot

def get_binance_symbols():
    return _get_cached('exchange:binance:all', _fetch_binance_all)

# --- Bybit ---
def _fetch_bybit_all():
    ex = ccxt.bybit({'enableRateLimit': True, 'timeout': 10000})
    fut_tickers = ex.fetch_tickers(params={'type': 'linear'})
    fut = set()
    for s, d in fut_tickers.items():
        if ':USDT' in s:
            c = s.split(':')[0].replace('/USDT', '')
            if c and '-' not in c:
                fut.add(c)
    spot_tickers = ex.fetch_tickers(params={'type': 'spot'})
    spot = set()
    for s, d in spot_tickers.items():
        if '/USDT' in s:
            c = s.replace('/USDT', '')
            if c and '-' not in c:
                spot.add(c)
    return fut, spot

def get_bybit_symbols():
    return _get_cached('exchange:bybit:all', _fetch_bybit_all)

# --- OKX ---
def _fetch_okx_all():
    ex = ccxt.okx({'enableRateLimit': True, 'timeout': 10000})
    fut_tickers = ex.fetch_tickers(params={'instType': 'SWAP'})
    fut = set()
    for s, d in fut_tickers.items():
        if '/USDT:USDT' in s:
            c = s.replace('/USDT:USDT', '')
            if c and '-' not in c:
                fut.add(c)
    spot_tickers = ex.fetch_tickers(params={'instType': 'SPOT'})
    spot = set()
    for s, d in spot_tickers.items():
        if '/USDT' in s and ':USDT' not in s:
            c = s.replace('/USDT', '')
            if c and '-' not in c:
                spot.add(c)
    return fut, spot

def get_okx_symbols():
    return _get_cached('exchange:okx:all', _fetch_okx_all)

# --- Gate.io ---
def _fetch_gate_all():
    ex = ccxt.gate({'enableRateLimit': True, 'timeout': 10000})
    fut_tickers = ex.fetch_tickers(params={'settle': 'usdt'})
    fut = set()
    for s, d in fut_tickers.items():
        if '/USDT' in s and not s.startswith('_'):
            c = s.replace('/USDT', '')
            if c and '-' not in c:
                fut.add(c)
    spot_tickers = ex.fetch_tickers()
    spot = set()
    for s, d in spot_tickers.items():
        if '/USDT' in s:
            c = s.replace('/USDT', '')
            if c and '-' not in c:
                spot.add(c)
    return fut, spot

def get_gate_symbols():
    return _get_cached('exchange:gate:all', _fetch_gate_all)

# --- MEXC ---
def _fetch_mexc_all():
    import requests
    fut = set()
    spot = set()
    try:
        r = requests.get('https://contract.mexc.com/api/v1/contract/detail', timeout=10)
        for item in r.json().get('data', []):
            if item.get('quoteCoin') == 'USDT':
                c = item.get('baseCoin', '').upper()
                if c and '-' not in c:
                    fut.add(c)
    except: pass
    try:
        r = requests.get('https://api.mexc.com/api/v3/exchangeInfo', timeout=10)
        for item in r.json().get('symbols', []):
            if item.get('quoteAsset') == 'USDT' and item.get('status') == 'TRADING':
                c = item.get('baseAsset', '').upper()
                if c and '-' not in c:
                    spot.add(c)
    except: pass
    return fut, spot

def get_mexc_symbols():
    return _get_cached('exchange:mexc:all', _fetch_mexc_all)

# --- Bitget ---
def _fetch_bitget_all():
    ex = ccxt.bitget({'enableRateLimit': True, 'timeout': 10000})
    fut_tickers = ex.fetch_tickers(params={'productType': 'USDT-FUTURES'})
    fut = set()
    for s, d in fut_tickers.items():
        if 'USDT' in s:
            c = s.replace('USDT', '').replace('_', '').replace('/', '')
            if c and '-' not in c:
                fut.add(c)
    spot_tickers = ex.fetch_tickers(params={'productType': 'SPOT'})
    spot = set()
    for s, d in spot_tickers.items():
        if 'USDT' in s:
            c = s.replace('USDT', '').replace('_', '').replace('/', '')
            if c and '-' not in c:
                spot.add(c)
    return fut, spot

def get_bitget_symbols():
    return _get_cached('exchange:bitget:all', _fetch_bitget_all)


# --- Объединённый чек ---
def check_symbol_all_exchanges(symbol):
    """Возвращает dict {exchange: {futures: bool, spot: bool}} для символа"""
    symbol = symbol.upper().strip()
    
    binance_fut, binance_spot = get_binance_symbols()
    bybit_fut, bybit_spot = get_bybit_symbols()
    okx_fut, okx_spot = get_okx_symbols()
    gate_fut, gate_spot = get_gate_symbols()
    mexc_fut, mexc_spot = get_mexc_symbols()
    bitget_fut, bitget_spot = get_bitget_symbols()
    
    return {
        'binance': {'futures': symbol in binance_fut, 'spot': symbol in binance_spot},
        'bybit': {'futures': symbol in bybit_fut, 'spot': symbol in bybit_spot},
        'okx': {'futures': symbol in okx_fut, 'spot': symbol in okx_spot},
        'gate': {'futures': symbol in gate_fut, 'spot': symbol in gate_spot},
        'mexc': {'futures': symbol in mexc_fut, 'spot': symbol in mexc_spot},
        'bitget': {'futures': symbol in bitget_fut, 'spot': symbol in bitget_spot},
    }


@require_http_methods(["GET"])
def api_binance_check_symbol(request, symbol):
    """API: проверка символа на Binance (кэшированная)"""
    symbol = symbol.upper().strip()
    binance_fut, binance_spot = get_binance_symbols()
    return JsonResponse({
        'symbol': symbol,
        'binance': {
            'futures': symbol in binance_fut,
            'spot': symbol in binance_spot,
            'futures_total': len(binance_fut),
            'spot_total': len(binance_spot),
        }
    })


@require_http_methods(["GET"])
def api_exchanges(request, symbol):
    """API: проверка символа на всех биржах (кэшированная)"""
    symbol = symbol.upper().strip()
    result = check_symbol_all_exchanges(symbol)
    return JsonResponse(result)


@require_http_methods(["GET"])
def api_exchanges_cache_debug(request):
    """DEBUG: кэшированные полные списки символов всех бирж"""
    return JsonResponse({
        'binance': {'futures': sorted(get_binance_symbols()[0]), 'spot': sorted(get_binance_symbols()[1])},
        'bybit': {'futures': sorted(get_bybit_symbols()[0]), 'spot': sorted(get_bybit_symbols()[1])},
        'okx': {'futures': sorted(get_okx_symbols()[0]), 'spot': sorted(get_okx_symbols()[1])},
        'gate': {'futures': sorted(get_gate_symbols()[0]), 'spot': sorted(get_gate_symbols()[1])},
        'mexc': {'futures': sorted(get_mexc_symbols()[0]), 'spot': sorted(get_mexc_symbols()[1])},
        'bitget': {'futures': sorted(get_bitget_symbols()[0]), 'spot': sorted(get_bitget_symbols()[1])},
    })


@require_http_methods(["GET"])
def api_exchange_check_debug(request, symbol):
    """DEBUG: детальная проверка символа на конкретной бирже"""
    symbol = symbol.upper().strip()
    
    # Проверяем каждый парсер по отдельности
    binance_fut, binance_spot = get_binance_symbols()
    bybit_fut, bybit_spot = get_bybit_symbols()
    okx_fut, okx_spot = get_okx_symbols()
    gate_fut, gate_spot = get_gate_symbols()
    mexc_fut, mexc_spot = get_mexc_symbols()
    bitget_fut, bitget_spot = get_bitget_symbols()
    
    # Ищем похожие символы (substring match)
    def find_similar(symbol_set, target):
        return [s for s in symbol_set if target in s or s in target]
    
    return JsonResponse({
        'symbol': symbol,
        'exact_match': {
            'binance': {'futures': symbol in binance_fut, 'spot': symbol in binance_spot},
            'bybit': {'futures': symbol in bybit_fut, 'spot': symbol in bybit_spot},
            'okx': {'futures': symbol in okx_fut, 'spot': symbol in okx_spot},
            'gate': {'futures': symbol in gate_fut, 'spot': symbol in gate_spot},
            'mexc': {'futures': symbol in mexc_fut, 'spot': symbol in mexc_spot},
            'bitget': {'futures': symbol in bitget_fut, 'spot': symbol in bitget_spot},
        },
        'similar_in_bybit_futures': find_similar(bybit_fut, symbol),
        'similar_in_bybit_spot': find_similar(bybit_spot, symbol),
        'bybit_futures_sample': sorted(list(bybit_fut))[:20],
        'bybit_spot_sample': sorted(list(bybit_spot))[:20],
    })


@require_http_methods(["GET"])
def api_exchange_raw_debug(request):
    """DEBUG: сырой формат тикеров с каждой биржи (первые 20)"""
    try:
        # Binance
        ex_b = get_binance_exchange()
        fut_b = ex_b.fetch_tickers(params={'type': 'future'})
        spot_b = ex_b.fetch_tickers(params={'type': 'spot'})
        
        # Bybit
        ex_by = ccxt.bybit({'enableRateLimit': True, 'timeout': 10000})
        fut_by = ex_by.fetch_tickers(params={'type': 'linear'})
        spot_by = ex_by.fetch_tickers(params={'type': 'spot'})
        
        # OKX
        ex_okx = ccxt.okx({'enableRateLimit': True, 'timeout': 10000})
        fut_okx = ex_okx.fetch_tickers(params={'instType': 'SWAP'})
        spot_okx = ex_okx.fetch_tickers(params={'instType': 'SPOT'})
        
        # Gate
        ex_gate = ccxt.gate({'enableRateLimit': True, 'timeout': 10000})
        fut_gate = ex_gate.fetch_tickers(params={'settle': 'usdt'})
        spot_gate = ex_gate.fetch_tickers()
        
        # MEXC
        mexc_fut = {}
        mexc_spot = {}
        try:
            import requests
            r = requests.get('https://contract.mexc.com/api/v1/contract/detail', timeout=10)
            mexc_fut = {item.get('symbol', ''): item for item in r.json().get('data', [])}
            r = requests.get('https://api.mexc.com/api/v3/exchangeInfo', timeout=10)
            mexc_spot = {item.get('symbol', ''): item for item in r.json().get('symbols', [])}
        except: pass
        
        # Bitget
        ex_bg = ccxt.bitget({'enableRateLimit': True, 'timeout': 10000})
        fut_bg = ex_bg.fetch_tickers(params={'productType': 'USDT-FUTURES'})
        spot_bg = ex_bg.fetch_tickers(params={'productType': 'SPOT'})
        
        return JsonResponse({
            'binance': {
                'futures_keys': list(fut_b.keys())[:20],
                'spot_keys': list(spot_b.keys())[:20],
            },
            'bybit': {
                'futures_keys': list(fut_by.keys())[:20],
                'spot_keys': list(spot_by.keys())[:20],
            },
            'okx': {
                'futures_keys': list(fut_okx.keys())[:20],
                'spot_keys': list(spot_okx.keys())[:20],
            },
            'gate': {
                'futures_keys': list(fut_gate.keys())[:20],
                'spot_keys': list(spot_gate.keys())[:20],
            },
            'mexc': {
                'futures_keys': list(mexc_fut.keys())[:20],
                'spot_keys': list(mexc_spot.keys())[:20],
            },
            'bitget': {
                'futures_keys': list(fut_bg.keys())[:20],
                'spot_keys': list(spot_bg.keys())[:20],
            },
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_clear_exchange_cache(request):
    """Очистить кэш символов всех бирж"""
    from django.core.cache import cache
    keys = [
        'exchange:binance:all',
        'exchange:bybit:all',
        'exchange:okx:all',
        'exchange:gate:all',
        'exchange:mexc:all',
        'exchange:bitget:all',
    ]
    for key in keys:
        cache.delete(key)
    return JsonResponse({'cleared': keys})

