"""
Отбор монет по гибридной схеме (Finviz + RVOL + NATR)

Score = 0.5*RVOL + 0.3*NATR + 0.2*|%24h|

RVOL = объём за 5 мин / средний объём за 5 мин (24ч)
NATR = Normalized ATR (волатильность в %)
%24h = процент изменения за 24ч (capped на 5%)

Порог входа: $10M volume (как в текущем binance_monitor)
"""
import time
import threading
from collections import deque
from django.core.cache import cache

# ==========================================
# КОНСТАНТЫ
# ==========================================
MIN_LIQUIDITY_VOLUME = 10_000_000   # порог входа (из текущего binance_monitor)
RVOL_CAP = 10.0                      # RVOL >= 10 = максимум
NATR_CAP = 2.0                       # NATR >= 2% = максимум
PCT_CAP = 5.0                        # |%| >= 5 = максимум

RVOL_WEIGHT = 0.5
NATR_WEIGHT = 0.3
PCT_WEIGHT = 0.2

POLL_INTERVAL = 60                   # лёгкий скан каждые 60 сек

STABLECOINS = {'USDT', 'USDC', 'FDUSD', 'DAI', 'TUSD', 'BUSD', 'USDP', 'EURC'}

# ==========================================
# ИСТОРИЯ ОБЪЁМА (для RVOL)
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
    """Ярус 1: снимаем снапшот quoteVolume (каждые 60 сек)"""
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
        old_v = None
        for ts, v in dq:
            if now_ts - ts >= 300:
                old_v = v
            else:
                break
    if old_v is None:
        return 0.0
    vol_5m = max(0.0, now_v - old_v)
    expected = quote_vol_24h / 288.0
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
# ОТБОР КАНДИДАТОВ (гибрид)
# ==========================================
def select_candidates(tickers, clean_fn, limit=60, log_func=print):
    """Гибридный отбор с оптимизированным чтением NATR"""
    rows = []

    # Batch-чтение NATR из Redis (один запрос вместо N)
    natr_batch = {}
    try:
        # Собираем все ключи NATR которые нам нужны
        natr_keys = []
        for symbol, data in tickers.items():
            clean = clean_fn(symbol)
            if clean:
                natr_keys.append(f"natr_{clean}_future")

        # Читаем все за раз (если Redis поддерживает pipeline)
        if natr_keys:
            # Fallback: читаем по одному, но с кэшированием
            for key in natr_keys:
                natr_batch[key] = cache.get(key) or {}
    except Exception:
        pass

    for symbol, data in tickers.items():
        clean = clean_fn(symbol)
        if not clean:
            continue

        volume = float(data.get('quoteVolume') or 0)
        if volume < MIN_LIQUIDITY_VOLUME:
            continue

        pct = float(data.get('percentage') or 0)
        pct_capped = min(abs(pct), PCT_CAP) / PCT_CAP

        # Используем batch вместо cache.get для каждой монеты
        natr_data = natr_batch.get(f"natr_{clean}_future", {})
        natr = float(natr_data.get('natr_5m14') or 0)
        rvol = get_rvol(clean, volume)

        rvol_norm = min(rvol, RVOL_CAP) / RVOL_CAP
        natr_norm = min(natr, NATR_CAP) / NATR_CAP

        score = (RVOL_WEIGHT * rvol_norm +
                 NATR_WEIGHT * natr_norm +
                 PCT_WEIGHT * pct_capped)

        rows.append({
            'symbol': clean, 'volume': volume,
            'natr': natr, 'rvol': rvol, 'pct': pct, 'score': score
        })

    active = sorted(rows, key=lambda r: r['score'], reverse=True)
    result = [r['symbol'] for r in active]

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
            f"📊 отбор: юниверс {len(rows)} | "
            + ", ".join(
                f"{r['symbol']}(RVOL {r['rvol']:.1f} NATR {r['natr']:.2f} %{r['pct']:+.1f} score {r['score']:.2f})"
                for r in top
            )
        )

    return result[:limit]