"""
Отбор монет по схеме популярных скринеров (Finviz / CoinGlass / Velo Data)

Ярус 1: лёгкий скан всего юниверса каждые 60 сек → копим историю объёма для RVOL
Ярус 2: отбор кандидатов по Score = 0.6*RVOL + 0.4*NATR (аномалии) + добор ликвидной базы

RVOL = объём за последние 5 мин / средний объём за 5 мин из 24ч
RVOL >= 2  → «начинается движение» (то, что ищут скринеры)
"""
import time
import threading
from collections import deque
from django.core.cache import cache

# ==========================================
# КОНСТАНТЫ КАК У ПОПУЛЯРНЫХ СКРИНЕРОВ
# ==========================================
UNIVERSE_MIN_VOLUME = 5_000_000   # порог входа в юниверс (у профи $5-10M, не $100K)
MIN_NATR = 0.2                    # NATR — фильтр, а не единственный ранжир
RVOL_TRIGGER = 2.0                # RVOL >= 2 = аномальная активность
RVOL_WEIGHT = 0.6
NATR_WEIGHT = 0.4
POLL_INTERVAL = 60                # лёгкий скан раз в 60 сек

STABLECOINS = {'USDT', 'USDC', 'FDUSD', 'DAI', 'TUSD', 'BUSD', 'USDP', 'EURC'}

# ==========================================
# ИСТОРИЯ ОБЪЁМА (кольцевой буфер)
# ==========================================
volume_history = {}   # clean_symbol -> deque([(ts, quoteVolume)])
_history_lock = threading.Lock()
_pollers_started = set()


def is_valid_symbol(symbol):
    if '-' in symbol:
        return False
    if len(symbol) < 2 or len(symbol) > 15:
        return False
    if not symbol.replace('_', '').isalnum():
        return False
    return True


def clean_swap(symbol):
    """Binance/Gate/MEXC swap: BTC/USDT:USDT -> BTC"""
    if not symbol.endswith(':USDT'):
        return None
    clean = symbol.replace('/USDT:USDT', '')
    if clean in STABLECOINS or not is_valid_symbol(clean):
        return None
    return clean


def clean_spot(symbol):
    """Spot: BTC/USDT -> BTC"""
    if not symbol.endswith('/USDT'):
        return None
    clean = symbol.replace('/USDT', '')
    if clean in STABLECOINS or not is_valid_symbol(clean):
        return None
    return clean


def update_volume_history(tickers, clean_fn):
    """Ярус 1: снимаем снапшот quoteVolume (вызывается каждые 60 сек)"""
    now = time.time()
    with _history_lock:
        for symbol, data in tickers.items():
            clean = clean_fn(symbol)
            if not clean:
                continue
            qv = float(data.get('quoteVolume') or 0)
            dq = volume_history.setdefault(clean, deque(maxlen=120))
            dq.append((now, qv))


def get_rvol(clean, quote_vol_24h):
    """RVOL: фактический объём за 5 мин / ожидаемый из 24ч"""
    with _history_lock:
        dq = volume_history.get(clean)
        if not dq or len(dq) < 6:
            return 0.0
        now_ts, now_v = dq[-1]
        # самый свежий снапшот возрастом >= 5 минут
        old_v = None
        for ts, v in dq:
            if now_ts - ts >= 300:
                old_v = v
            else:
                break
    if old_v is None:
        return 0.0
    vol_5m = max(0.0, now_v - old_v)
    expected = quote_vol_24h / 288.0   # средний объём за 5 мин из 24ч
    return vol_5m / expected if expected > 0 else 0.0


def start_volume_poller(name, fetch_tickers_fn, clean_fn, log_func=print):
    """Фоновый поток яруса 1 — один на биржу"""
    if name in _pollers_started:
        return
    _pollers_started.add(name)

    def run():
        while True:
            try:
                tickers = fetch_tickers_fn()
                if tickers:
                    update_volume_history(tickers, clean_fn)
            except Exception as e:
                log_func(f"⚠️ volume poller {name}: {e}")
            time.sleep(POLL_INTERVAL)

    threading.Thread(target=run, daemon=True, name=f'vol-poller-{name}').start()


# ==========================================
# ЯРУС 2: ОТБОР КАНДИДАТОВ
# ==========================================
def select_candidates(tickers, clean_fn, limit=60, log_func=print):
    """
    1. Юниверс: объём 24ч > $5M
    2. Аномалии: RVOL >= 2 ИЛИ NATR >= 0.3 → ранжируем по Score
    3. Если аномалий мало — добираем ликвидной базой (топ по объёму)
    """
    rows = []
    for symbol, data in tickers.items():
        clean = clean_fn(symbol)
        if not clean:
            continue
        volume = float(data.get('quoteVolume') or 0)
        if volume < UNIVERSE_MIN_VOLUME:
            continue

        natr_data = cache.get(f"natr_{clean}_future") or {}
        natr = float(natr_data.get('natr_5m14') or 0)
        rvol = get_rvol(clean, volume)

        rows.append({'symbol': clean, 'volume': volume, 'natr': natr, 'rvol': rvol})

    # Активная часть — «что происходит СЕЙЧАС»
    active = [r for r in rows if r['rvol'] >= RVOL_TRIGGER or r['natr'] >= 0.3]
    for r in active:
        rvol_norm = min(r['rvol'], 10) / 10     # RVOL 10x = максимум
        natr_norm = min(r['natr'], 2) / 2       # NATR 2% = максимум
        r['score'] = RVOL_WEIGHT * rvol_norm + NATR_WEIGHT * natr_norm
    active.sort(key=lambda r: r['score'], reverse=True)

    result = [r['symbol'] for r in active]

    # Ликвидная база — чтобы стаканы не пустовали
    if len(result) < limit:
        by_vol = sorted(rows, key=lambda r: r['volume'], reverse=True)
        for r in by_vol:
            if len(result) >= limit:
                break
            if r['symbol'] not in result:
                result.append(r['symbol'])

    if rows:
        top = active[:5]
        log_func(
            f"📊 отбор: юниверс {len(rows)}, аномалий {len(active)} | "
            + ", ".join(f"{r['symbol']}(RVOL {r['rvol']:.1f} NATR {r['natr']:.2f})" for r in top)
        )

    return result[:limit]