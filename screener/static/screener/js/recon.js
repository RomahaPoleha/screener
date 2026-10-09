// ==========================================
// recon.js — RECON (ПЛОТНОСТИ ОРДЕРОВ)
// Загрузка стаканов с бирж, отрисовка линий, панель
// ==========================================

// --- Переменные состояния ---
let densityLines = [], densityEnabled = false;
let densityMarkets = { future: false, spot: false };
let densityMinVolumeFuture = 50000, densityMinVolumeSpot = 10000;
let densityUpdateTimer = null, previousDensities = { future: [], spot: [] };

let reconEnabled = localStorage.getItem('reconEnabled') === 'true';
let reconUpdateTimer = null;
let reconPanelEl = null;
let reconLines = [];
let reconMarkets = {
    binance: { spot: false, futures: true },
    bybit:   { spot: false, futures: false },
    okx:     { spot: false, futures: false },
    gate:    { spot: false, futures: false },
    mexc:    { spot: false, futures: false },
    bitget:  { spot: false, futures: false },
    binance_alpha: { spot: true, futures: false },
};
let reconMinVolumes = {
    binance: { spot: 10000, futures: 10000 },
    bybit:   { spot: 10000, futures: 10000 },
    okx:     { spot: 10000, futures: 10000},
    gate:    { spot: 10000, futures: 10000 },
    mexc:    { spot: 10000, futures: 10000 },
    bitget:  { spot: 10000, futures: 10000 },
    binance_alpha: { spot: 1000, futures: 1000 }
};

// --- НОВОЕ: Кэш для мультипликаторов контрактов Gate.io Futures ---
let gateFuturesMultipliers = {};
let isGateMultipliersLoading = false;

if (localStorage.getItem('densityMinVolumeFuture')) densityMinVolumeFuture = parseInt(localStorage.getItem('densityMinVolumeFuture'));
if (localStorage.getItem('densityMinVolumeSpot')) densityMinVolumeSpot = parseInt(localStorage.getItem('densityMinVolumeSpot'));

try {
    const savedRecon = JSON.parse(localStorage.getItem('reconMarkets') || 'null');
    if (savedRecon) for (const id of Object.keys(reconMarkets)) {
        if (savedRecon[id]) {
            reconMarkets[id].spot = !!savedRecon[id].spot;
            reconMarkets[id].futures = !!savedRecon[id].futures;
        }
    }
    const savedVol = JSON.parse(localStorage.getItem('reconMinVolumes') || 'null');
    if (savedVol) for (const id of Object.keys(reconMinVolumes)) {
        if (savedVol[id]) {
            if (Number(savedVol[id].spot) > 0) reconMinVolumes[id].spot = Number(savedVol[id].spot);
            if (Number(savedVol[id].futures) > 0) reconMinVolumes[id].futures = Number(savedVol[id].futures);
        }
    }
    // Сброс дефолта для Alpha если сохранено старое значение 10000
    if (reconMinVolumes.binance_alpha.spot === 10000) reconMinVolumes.binance_alpha.spot = 1000;
    if (reconMinVolumes.binance_alpha.futures === 10000) reconMinVolumes.binance_alpha.futures = 1000;
} catch (e) {}

const RECON_EXCHANGES = [
    { id: 'binance', label: 'BI', color: '#f59e0b', domain: 'binance.com' },
    { id: 'bybit',   label: 'BY', color: '#f59e0b', domain: 'bybit.com' },
    { id: 'okx',     label: 'OKX', color: '#f59e0b', domain: 'okx.com' },
    { id: 'gate',    label: 'GT',  color: '#f59e0b', domain: 'gate.io' },
    { id: 'mexc',    label: 'MEX', color: '#f59e0b', domain: 'mexc.com' },
    { id: 'bitget',  label: 'BGB', color: '#f59e0b', domain: 'bitget.com' },
    { id: 'binance_alpha', label: 'BA', color: '#8b5cf6', domain: 'binance.com' },
];

// ==========================================
// СТАРЫЙ DENSITY (legacy, оставлен для совместимости)
// ==========================================
async function loadDensities(symbol) {
    if (!densityEnabled || !candleSeries) return;
    let hasChanges = false;
    const marketsToLoad = [];
    if (densityMarkets.future) marketsToLoad.push('future');
    if (densityMarkets.spot) marketsToLoad.push('spot');
    const allNewData = {};
    for (const market of marketsToLoad) {
        try {
            const url = market === 'future'
                ? `https://fapi.binance.com/fapi/v1/depth?symbol=${symbol}USDT&limit=1000`
                : `https://api.binance.com/api/v3/depth?symbol=${symbol}USDT&limit=1000`;
            const res = await fetch(url);
            if (!res.ok) continue;
            const data = await res.json();
            const densities = [];
            const minVolume = market === 'future' ? densityMinVolumeFuture : densityMinVolumeSpot;
            const processSide = (sideArr, sideType) => {
                for (const [priceStr, qtyStr] of sideArr) {
                    const price = parseFloat(priceStr);
                    const qty = parseFloat(qtyStr);
                    const val = price * qty;
                    if (val >= minVolume) densities.push({ price, volume: val, side: sideType });
                }
            };
            if (data.bids) processSide(data.bids, 'buy');
            if (data.asks) processSide(data.asks, 'sell');
            densities.sort((a, b) => b.volume - a.volume);
            allNewData[market] = densities.slice(0, 30);
        } catch (e) {
            console.error(`Densities error (${market}):`, e);
            allNewData[market] = previousDensities[market] || [];
        }
    }
    for (const market of marketsToLoad) {
        const newData = allNewData[market] || [];
        const currentData = JSON.stringify(newData.map(d => ({price: d.price, volume: d.volume, side: d.side})));
        const prevData = JSON.stringify((previousDensities[market] || []).map(d => ({price: d.price, volume: d.volume, side: d.side})));
        if (currentData !== prevData) { hasChanges = true; previousDensities[market] = newData; }
    }
    if (!hasChanges) return;
    clearDensityLines();
    for (const market of marketsToLoad) {
        const data = previousDensities[market] || [];
        data.forEach(d => {
            const line = candleSeries.createPriceLine({
                price: d.price, color: 'rgba(255, 255, 255, 0.5)', lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true,
                axisLabelColor: '#ffffff', axisLabelBackgroundColor: 'rgba(100, 100, 100, 0.7)',
                title: `${market === 'future' ? 'BI-F' : 'BI-S'} ${d.volume >= 1000 ? (d.volume/1000).toFixed(1)+'K' : d.volume}`
            });
            densityLines.push(line);
        });
    }
}

function clearDensityLines() {
    if (!candleSeries) return;
    densityLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch(e){} });
    densityLines = [];
}

function startDensityUpdates(symbol) {
    if (densityUpdateTimer) clearInterval(densityUpdateTimer);
    loadDensities(symbol);
    densityUpdateTimer = setInterval(() => {
        if (currentSymbol === symbol && densityEnabled) loadDensities(symbol);
    }, 3000);
}

// Кэш маппинга Alpha: baseSymbol -> ALPHA_{tokenId}USDT
let alphaSymbolMap = null;

async function fetchAlphaSymbolMap() {
    if (alphaSymbolMap) return alphaSymbolMap;
    try {
        const res = await fetch('/api/exchange-alpha-map/');
        if (res.ok) {
            const data = await res.json();
            alphaSymbolMap = data.map || {};
            console.log('Alpha map loaded:', alphaSymbolMap);
        }
    } catch (e) {
        console.error('Alpha map fetch error:', e);
        alphaSymbolMap = {};
    }
    return alphaSymbolMap;
}

function getAlphaSymbol(symbol) {
    if (!alphaSymbolMap) return null;
    return alphaSymbolMap[symbol.toUpperCase()] || null;
}

// ==========================================
// RECON — НОВАЯ МУЛЬТИ-БИРЖЕВАЯ ЛОГИКА
// ==========================================
function getReconUrl(exId, symbol, market) {
    if (exId === 'binance') return market === 'futures'
        ? `https://fapi.binance.com/fapi/v1/depth?symbol=${symbol}USDT&limit=1000`
        : `https://api.binance.com/api/v3/depth?symbol=${symbol}USDT&limit=1000`;
    if (exId === 'bybit') return market === 'futures'
        ? `https://api.bybit.com/v5/market/orderbook?category=linear&symbol=${symbol}USDT&limit=200`
        : `https://api.bybit.com/v5/market/orderbook?category=spot&symbol=${symbol}USDT&limit=200`;
    if (exId === 'okx') return market === 'futures'
        ? `https://www.okx.com/api/v5/market/books?instId=${symbol}-USDT-SWAP&sz=200`
        : `https://www.okx.com/api/v5/market/books?instId=${symbol}-USDT&sz=200`;
    if (exId === 'bitget') return market === 'futures'
        ? `https://api.bitget.com/api/v2/mix/market/merge-depth?symbol=${symbol}USDT&productType=USDT-FUTURES&limit=100`
        : `https://api.bitget.com/api/v2/spot/market/merge-depth?symbol=${symbol}USDT&limit=100`;
    if (exId === 'gate') return `/api/gate-depth/?market=${market}&symbol=${symbol}`;
    if (exId === 'mexc') return `/api/mexc-depth/?market=${market}&symbol=${symbol}`;
    if (exId === 'binance_alpha') {
        // Alpha — спотовая торговля на BSC/Base/Robinhood, символ ALPHA_{tokenId}USDT
        if (market !== 'spot') return null;
        const alphaSym = getAlphaSymbol(symbol);
        console.log('Alpha lookup:', symbol, '->', alphaSym);
        if (!alphaSym) return null;
        const url = `https://www.binance.com/bapi/defi/v1/public/alpha-trade/fullDepth?symbol=${alphaSym}&limit=500`;
        console.log('Alpha fullDepth URL:', url);
        return url;
    }
    return null;
}

function parseReconLevels(exId, data) {
    let rawBids = [], rawAsks = [];
    if (exId === 'binance') {
        rawBids = data.bids || []; rawAsks = data.asks || [];
    } else if (exId === 'bybit') {
        rawBids = (data.result && data.result.b) || [];
        rawAsks = (data.result && data.result.a) || [];
    } else if (exId === 'okx') {
        const d = (data.data || [])[0] || {};
        rawBids = d.bids || []; rawAsks = d.asks || [];
    } else if (exId === 'gate') {
        rawBids = data.bids || []; rawAsks = data.asks || [];
    } else if (exId === 'mexc') {
        rawBids = data.bids || []; rawAsks = data.asks || [];
    } else if (exId === 'bitget') {
        const inner = data.data || {};
        rawBids = inner.bids || []; rawAsks = inner.asks || [];
    } else if (exId === 'binance_alpha') {
        // Alpha response: {code: "000000", data: {bids: [[price, qty],...], asks: [...]}}
        const d = data.data || {};
        rawBids = d.bids || [];
        rawAsks = d.asks || [];
    }
    const toLevel = (row) => {
        if (Array.isArray(row)) return [parseFloat(row[0]), Math.abs(parseFloat(row[1]))];
        if (row && typeof row === 'object') return [parseFloat(row.p || row.price), Math.abs(parseFloat(row.v || row.vol))];
        return [NaN, NaN];
    };
    return { rawBids, rawAsks, toLevel };
}

// --- НОВОЕ: Функция для загрузки мультипликаторов контрактов Gate.io ---
async function loadGateFuturesMultipliers() {
    // Если уже загружено или идет загрузка, ничего не делаем
    if (Object.keys(gateFuturesMultipliers).length > 0 || isGateMultipliersLoading) return;

    isGateMultipliersLoading = true;
    try {
        // Прямой запрос к публичному API Gate.io (обычно не имеет CORS-ограничений для GET)
        const res = await fetch('https://api.gateio.ws/api/v4/futures/usdt/contracts');
        if (res.ok) {
            const contracts = await res.json();
            contracts.forEach(c => {
                // Сохраняем мультипликатор (quanto_multiplier) для каждой пары
                gateFuturesMultipliers[c.name] = parseFloat(c.quanto_multiplier);
            });
        }
    } catch (e) {
        console.warn('Не удалось загрузить мультипликаторы Gate.io futures. Будет использован запасной вариант (x1).', e);
    } finally {
        isGateMultipliersLoading = false;
    }
}

async function fetchReconMarket(exId, symbol, market) {
    // --- НОВОЕ: Гарантируем загрузку мультипликаторов перед обработкой фьючерсов Gate ---
    if (exId === 'gate' && market === 'futures') {
        await loadGateFuturesMultipliers();
    }

    let data;
    try {
        if (exId === 'mexc') {
            const res = await fetch(`/api/mexc-depth/?market=${market}&symbol=${symbol}`);
            if (!res.ok) return [];
            data = await res.json();
        } else if (exId === 'gate') {
            const res = await fetch(`/api/gate-depth/?market=${market}&symbol=${symbol}`);
            if (!res.ok) return [];
            data = await res.json();
        } else {
            const url = getReconUrl(exId, symbol, market);
            if (!url) return [];
            const res = await fetch(url);
            if (!res.ok) {
                console.log('Alpha fetch failed:', res.status);
                return [];
            }
            data = await res.json();
            console.log('Alpha response:', data);
        }
    } catch (e) { return []; }

    if (data) {
        if (exId === 'okx') {
            if (data.code !== undefined && data.code !== '0' && data.code !== 0) return [];
        } else if (exId === 'bitget') {
            if (data.code !== undefined && data.code !== '00000' && data.code !== 0) return [];
        } else if (exId === 'bybit') {
            if (data.retCode !== undefined && data.retCode !== 0) return [];
        } else if (exId === 'binance_alpha') {
            // Alpha возвращает code: "000000"
            if (data.code !== undefined && data.code !== '000000') return [];
        } else {
            if (data.code !== undefined && data.code !== 0 && data.code !== '0') return [];
            if (data.retCode !== undefined && data.retCode !== 0) return [];
        }
        if (data.msg && typeof data.msg === 'string' && data.msg.includes('not found')) return [];
    }

    const { rawBids, rawAsks, toLevel } = parseReconLevels(exId, data);
    const minVolume = reconMinVolumes[exId][market];
    const out = [];

    const push = (arr) => {
        for (const row of arr) {
            const [p, q] = toLevel(row);
            if (!isFinite(p) || !isFinite(q) || p <= 0) continue;

            // --- НОВОЕ: Исправление расчета объема для Gate.io Futures ---
            let vol = p * q;
            if (exId === 'gate' && market === 'futures') {
                // Берем мультипликатор из кэша, если его нет (ошибка загрузки), используем 1 как безопасный fallback
                const multiplier = gateFuturesMultipliers[symbol] || 1;
                vol = p * q * multiplier;
            }

            if (vol >= minVolume) {
                out.push({ price: p, volume: vol });
            }
        }
    };

    push(rawBids);
    push(rawAsks);
    console.log('Alpha fetch result:', exId, market, 'minVol', minVolume, 'levels', out.length, 'sample', out.slice(0,3));
    return out;
}

async function loadReconDensities(symbol) {
    if (!reconEnabled || !candleSeries || isReconLoading) return;
    isReconLoading = true;
    try {
        const tasks = [];
        for (const ex of RECON_EXCHANGES) {
            for (const market of ['spot', 'futures']) {
                if (!reconMarkets[ex.id][market]) continue;
                tasks.push(fetchReconMarket(ex.id, symbol, market)
                    .then(d => ({ ex: ex.id, market, data: d }))
                    .catch(() => ({ ex: ex.id, market, data: null })));
            }
        }
        if (tasks.length === 0) { clearReconLines(); return; }
        const results = await Promise.all(tasks);
        const newLines = [];
        for (const r of results) {
            if (!r.data) continue;
            const ex = RECON_EXCHANGES.find(e => e.id === r.ex);
            const suffix = r.market === 'futures' ? 'F' : 'S';
            const top = r.data.slice().sort((a, b) => b.volume - a.volume).slice(0, 20);
            top.forEach(d => {
                const line = candleSeries.createPriceLine({
                    price: d.price, color: 'rgba(255, 255, 255, 0.5)', lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true,
                    axisLabelColor: '#ffffff', axisLabelBackgroundColor: 'rgba(100, 100, 100, 0.7)',
                    title: `${ex.label}-${suffix} ${d.volume >= 1000 ? (d.volume/1000).toFixed(1)+'K' : d.volume}`
                });
                newLines.push(line);
            });
        }
        clearReconLines();
        reconLines = newLines;
    } finally { isReconLoading = false; }
}

function clearReconLines() {
    if (!candleSeries) return;
    reconLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch(e){} });
    reconLines = [];
}

function ensureReconPanel() {
    const container = document.getElementById('reconPanelContainer');
    if (!container) return;
    if (!reconEnabled) {
        container.style.display = 'none';
        container.innerHTML = '';
        return;
    }
    container.style.display = 'flex';
    if (!container.dataset.hasClickListener) {
        container.addEventListener('click', (e) => {
            const t = e.target.closest('.recon-toggle');
            if (!t) return;
            toggleReconMarket(t.dataset.ex, t.dataset.market);
        });
        container.dataset.hasClickListener = 'true';
    }
    reconPanelEl = container;
    renderReconPanel();
}

function removeReconPanel() {
    const container = document.getElementById('reconPanelContainer');
    if (container) {
        container.style.display = 'none';
        container.innerHTML = '';
    }
    reconPanelEl = null;
}

function renderReconPanel() {
    if (!reconPanelEl) return;
    reconPanelEl.innerHTML = RECON_EXCHANGES.map(ex => {
        const mkToggle = (market, letter) => {
            const on = reconMarkets[ex.id][market];
            const bg = on ? 'rgba(59, 130, 246, 0.2)' : 'transparent';
            const border = on ? `1px solid ${ex.color}` : '1px solid #475569';
            const checkmark = on ? `<span style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:${ex.color};font-size:11px;font-weight:bold;">✓</span>` : '';
            return `<span style="font-size:10px;color:#94a3b8;">${letter}</span>
                <div class="recon-toggle" data-ex="${ex.id}" data-market="${market}" title="Клик: вкл/выкл ${letter} ${ex.label}" style="position:relative;width:20px;height:20px;border-radius:3px;background:${bg};border:${border};cursor:pointer;user-select:none;${on ? '' : 'opacity:0.45;'}">
                    ${checkmark}
                </div>`;
        };
        
        // Alpha: спотовая торговля, показываем галочку "S"
        const isAlpha = ex.id === 'binance_alpha';
        const togglesHtml = isAlpha
            ? mkToggle('spot', 'S')
            : mkToggle('spot', 'S') + mkToggle('futures', 'F');
        
        return `<div style="display:flex;align-items:center;gap:5px;">
            <div style="display:flex;flex-direction:column;align-items:center;gap:2px;min-width:22px;">
                <span style="font-weight:600;font-size:11px;color:${ex.color};line-height:1;">${ex.label}</span>
                <img src="https://www.google.com/s2/favicons?domain=${ex.domain}&sz=32" onerror="this.style.display='none'" style="width:12px;height:12px;border-radius:2px;display:block;">
            </div>
            ${togglesHtml}
        </div>`;
    }).join('');
}

function toggleReconMarket(exId, market) {
    reconMarkets[exId][market] = !reconMarkets[exId][market];
    localStorage.setItem('reconMarkets', JSON.stringify(reconMarkets));
    renderReconPanel();
    const hasEnabled = RECON_EXCHANGES.some(ex =>
        reconMarkets[ex.id].spot || reconMarkets[ex.id].futures
    );
    if (currentSymbol && hasEnabled) {
        loadReconDensities(currentSymbol);
    } else if (!hasEnabled) {
        clearReconLines();
    }
}

function startReconUpdates(symbol) {
    if (reconUpdateTimer) clearInterval(reconUpdateTimer);
    if (!reconEnabled) return;
    ensureReconPanel();
    renderReconPanel();
    // Загружаем маппинг Alpha для Recon
    fetchAlphaSymbolMap();
    loadReconDensities(symbol);
    reconUpdateTimer = setInterval(() => {
        if (currentSymbol === symbol && reconEnabled) loadReconDensities(symbol);
    }, 3000);
}

function stopReconUpdates() {
    if (reconUpdateTimer) { clearInterval(reconUpdateTimer); reconUpdateTimer = null; }
    removeReconPanel();
    clearReconLines();
}

function renderReconSettings() {
    const container = document.getElementById('reconSettingsContainer');
    if (!container) return;
    container.innerHTML = RECON_EXCHANGES.map(ex => {
        const isAlpha = ex.id === 'binance_alpha';
        const minVal = isAlpha ? 1000 : 10000;
        return `<div style="display:flex;align-items:center;gap:6px;">
            <img src="https://www.google.com/s2/favicons?domain=${ex.domain}&sz=32" onerror="this.style.display='none'" style="width:16px;height:16px;border-radius:2px;flex-shrink:0;">
            <span style="font-weight:600;font-size:12px;color:${ex.color};min-width:24px;">${ex.label}</span>
            ${isAlpha ? '' : `<span style="font-size:11px;color:#94a3b8;min-width:10px;">S:</span>
            <input type="number" id="reconMinS_${ex.id}" value="${reconMinVolumes[ex.id].spot}" min="${minVal}" step="1000" style="width:70px;background:#1e293b;border:1px solid #475569;color:#fff;padding:4px 6px;border-radius:3px;font-size:12px;">`}
            <span style="font-size:11px;color:#94a3b8;min-width:10px;">${isAlpha ? 'A' : 'F'}:</span>
            <input type="number" id="reconMinF_${ex.id}" value="${reconMinVolumes[ex.id].futures}" min="${minVal}" step="1000" style="width:70px;background:#1e293b;border:1px solid #475569;color:#fff;padding:4px 6px;border-radius:3px;font-size:12px;">
        </div>`;
    }).join('');
}

function toggleReconSettings() {
    const toggle = document.getElementById('reconPanelToggle');
    const section = document.getElementById('reconSettingsSection');
    if (toggle && section) {
        section.style.display = toggle.checked ? 'block' : 'none';
    }
}