// ==========================================
// recon.js — RECON (ПЛОТНОСТИ ОРДЕРОВ)
// Загрузка стаканов с бирж, отрисовка линий, панель
// Включена поддержка Binance Alpha
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

// Добавлен binance_alpha с маркетом 'alpha'
let reconMarkets = {
    binance:       { spot: false, futures: true },
    binance_alpha: { alpha: false }, // Новый маркет для Alpha
    bybit:         { spot: false, futures: false },
    okx:           { spot: false, futures: false },
    gate:          { spot: false, futures: false },
    mexc:          { spot: false, futures: false },
    bitget:        { spot: false, futures: false },
};

let reconMinVolumes = {
    binance:       { spot: 10000, futures: 10000 },
    binance_alpha: { alpha: 50000 }, // Порог 50k для Alpha (как в Python-воркере)
    bybit:         { spot: 10000, futures: 10000 },
    okx:           { spot: 10000, futures: 10000 },
    gate:          { spot: 10000, futures: 10000 },
    mexc:          { spot: 10000, futures: 10000 },
    bitget:        { spot: 10000, futures: 10000 }
};

// --- НОВОЕ: Кэш для мультипликаторов контрактов Gate.io Futures ---
let gateFuturesMultipliers = {};
let isGateMultipliersLoading = false;

if (localStorage.getItem('densityMinVolumeFuture')) densityMinVolumeFuture = parseInt(localStorage.getItem('densityMinVolumeFuture'));
if (localStorage.getItem('densityMinVolumeSpot')) densityMinVolumeSpot = parseInt(localStorage.getItem('densityMinVolumeSpot'));

try {
    const savedRecon = JSON.parse(localStorage.getItem('reconMarkets') || 'null');
    if (savedRecon) {
        for (const id of Object.keys(reconMarkets)) {
            if (savedRecon[id]) {
                // Безопасное слияние, чтобы не сломать новые ключи (например, 'alpha')
                for (const m of Object.keys(reconMarkets[id])) {
                    if (savedRecon[id][m] !== undefined) {
                        reconMarkets[id][m] = !!savedRecon[id][m];
                    }
                }
            }
        }
    }

    const savedVol = JSON.parse(localStorage.getItem('reconMinVolumes') || 'null');
    if (savedVol) {
        for (const id of Object.keys(reconMinVolumes)) {
            if (savedVol[id]) {
                for (const m of Object.keys(reconMinVolumes[id])) {
                    if (Number(savedVol[id][m]) > 0) {
                        reconMinVolumes[id][m] = Number(savedVol[id][m]);
                    }
                }
            }
        }
    }
} catch (e) {
    console.warn("Ошибка парсинга localStorage recon:", e);
}

// Добавлен binance_alpha (фиолетовый цвет #8b5cf6 для визуального отличия)
const RECON_EXCHANGES = [
    { id: 'binance',       label: 'BI',  color: '#f59e0b', domain: 'binance.com' },
    { id: 'binance_alpha', label: 'BA',  color: '#8b5cf6', domain: 'binance.com' },
    { id: 'bybit',         label: 'BY',  color: '#f59e0b', domain: 'bybit.com' },
    { id: 'okx',           label: 'OKX', color: '#ffffff', domain: 'okx.com' },
    { id: 'gate',          label: 'GT',  color: '#17e8a5', domain: 'gate.io' },
    { id: 'mexc',          label: 'MEX', color: '#17e8a5', domain: 'mexc.com' },
    { id: 'bitget',        label: 'BGB', color: '#00f0e6', domain: 'bitget.com' },
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

// ==========================================
// RECON — МУЛЬТИ-БИРЖЕВАЯ ЛОГИКА (С BINANCE ALPHA)
// ==========================================
function getReconUrl(exId, symbol, market) {
    // --- BINANCE ALPHA ---
    if (exId === 'binance_alpha') {
        // Символ может прийти уже с USDT, проверяем чтобы не было дублей (ALPHA_175USDTUSDT)
        const sym = symbol.toUpperCase().endsWith('USDT') ? symbol.toUpperCase() : `${symbol.toUpperCase()}USDT`;
        return `https://www.binance.com/bapi/defi/v1/public/alpha-trade/fullDepth?symbol=${sym}&limit=500`;
    }
    // --- СТАНДАРТНЫЕ БИРЖИ ---
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
    return null;
}

function parseReconLevels(exId, data) {
    let rawBids = [], rawAsks = [];

    if (exId === 'binance_alpha') {
        rawBids = (data.data && data.data.bids) || [];
        rawAsks = (data.data && data.data.asks) || [];
    } else if (exId === 'binance') {
        rawBids = data.bids || []; rawAsks = data.asks || [];
    } else if (exId === 'bybit') {
        rawBids = (data.result && data.result.b) || [];
        rawAsks = (data.result && data.result.a) || [];
    } else if (exId === 'okx') {
        const d = (data.data || [])[0] || {};
        rawBids = d.bids || []; rawAsks = d.asks || [];
    } else if (exId === 'gate' || exId === 'mexc') {
        rawBids = data.bids || []; rawAsks = data.asks || [];
    } else if (exId === 'bitget') {
        const inner = data.data || {};
        rawBids = inner.bids || []; rawAsks = inner.asks || [];
    }

    const toLevel = (row) => {
        if (Array.isArray(row)) return [parseFloat(row[0]), Math.abs(parseFloat(row[1]))];
        if (row && typeof row === 'object') return [parseFloat(row.p || row.price), Math.abs(parseFloat(row.v || row.vol))];
        return [NaN, NaN];
    };
    return { rawBids, rawAsks, toLevel };
}

async function loadGateFuturesMultipliers() {
    if (Object.keys(gateFuturesMultipliers).length > 0 || isGateMultipliersLoading) return;
    isGateMultipliersLoading = true;
    try {
        const res = await fetch('https://api.gateio.ws/api/v4/futures/usdt/contracts');
        if (res.ok) {
            const contracts = await res.json();
            contracts.forEach(c => {
                gateFuturesMultipliers[c.name] = parseFloat(c.quanto_multiplier);
            });
        }
    } catch (e) {
        console.warn('Не удалось загрузить мультипликаторы Gate.io futures.', e);
    } finally {
        isGateMultipliersLoading = false;
    }
}

async function fetchReconMarket(exId, symbol, market) {
    if (exId === 'gate' && market === 'futures') {
        await loadGateFuturesMultipliers();
    }

    let data;
    try {
        if (exId === 'mexc' || exId === 'gate') {
            const res = await fetch(`/api/${exId}-depth/?market=${market}&symbol=${symbol}`);
            if (!res.ok) return [];
            data = await res.json();
        } else {
            const url = getReconUrl(exId, symbol, market);
            if (!url) return [];
            const res = await fetch(url);
            if (!res.ok) return [];
            data = await res.json();
        }
    } catch (e) { return []; }

    if (data) {
        // Валидация ответов
        if (exId === 'binance_alpha') {
            if (data.code !== '000000') return [];
        } else if (exId === 'okx') {
            if (data.code !== undefined && data.code !== '0' && data.code !== 0) return [];
        } else if (exId === 'bitget') {
            if (data.code !== undefined && data.code !== '00000' && data.code !== 0) return [];
        } else if (exId === 'bybit') {
            if (data.retCode !== undefined && data.retCode !== 0) return [];
        } else {
            if (data.code !== undefined && data.code !== 0 && data.code !== '0') return [];
            if (data.retCode !== undefined && data.retCode !== 0) return [];
        }
        if (data.msg && typeof data.msg === 'string' && data.msg.toLowerCase().includes('not found')) return [];
    }

    const { rawBids, rawAsks, toLevel } = parseReconLevels(exId, data);
    const minVolume = reconMinVolumes[exId] ? reconMinVolumes[exId][market] : 10000;
    const out = [];

    const push = (arr) => {
        for (const row of arr) {
            const [p, q] = toLevel(row);
            if (!isFinite(p) || !isFinite(q) || p <= 0) continue;

            let vol = p * q;
            if (exId === 'gate' && market === 'futures') {
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
    return out;
}

async function loadReconDensities(symbol) {
    if (!reconEnabled || !candleSeries || window.isReconLoading) return;
    window.isReconLoading = true;
    try {
        const tasks = [];
        const allMarkets = ['spot', 'futures', 'alpha']; // Добавлен alpha

        for (const ex of RECON_EXCHANGES) {
            for (const market of allMarkets) {
                // Пропускаем, если биржа не поддерживает этот маркет или он выключен
                if (!reconMarkets[ex.id] || !reconMarkets[ex.id][market]) continue;

                tasks.push(fetchReconMarket(ex.id, symbol, market)
                    .then(d => ({ ex: ex.id, market, data: d }))
                    .catch(() => ({ ex: ex.id, market, data: null })));
            }
        }

        if (tasks.length === 0) { clearReconLines(); return; }

        const results = await Promise.all(tasks);
        const newLines = [];

        for (const r of results) {
            if (!r.data || r.data.length === 0) continue;
            const ex = RECON_EXCHANGES.find(e => e.id === r.ex);

            // Формируем суффикс: A для Alpha, F для Futures, S для Spot
            const suffix = r.market === 'alpha' ? 'A' : (r.market === 'futures' ? 'F' : 'S');
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
    } finally {
        window.isReconLoading = false;
    }
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
        // Определяем, какие маркеты доступны для конкретной биржи
        const marketsForEx = ex.id === 'binance_alpha' ? ['alpha'] : ['spot', 'futures'];

        const mkToggle = (market, letter) => {
            if (!reconMarkets[ex.id] || reconMarkets[ex.id][market] === undefined) return '';

            const on = reconMarkets[ex.id][market];
            const bg = on ? `${ex.color}33` : 'transparent'; // 33 = 20% opacity hex
            const border = on ? `1px solid ${ex.color}` : '1px solid #475569';
            const checkmark = on ? `<span style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:${ex.color};font-size:11px;font-weight:bold;">✓</span>` : '';

            return `<span style="font-size:10px;color:#94a3b8;">${letter}</span>
                <div class="recon-toggle" data-ex="${ex.id}" data-market="${market}" title="Клик: вкл/выкл ${letter} ${ex.label}" style="position:relative;width:20px;height:20px;border-radius:3px;background:${bg};border:${border};cursor:pointer;user-select:none;${on ? '' : 'opacity:0.45;'}">
                    ${checkmark}
                </div>`;
        };

        return `<div style="display:flex;align-items:center;gap:5px;">
            <div style="display:flex;flex-direction:column;align-items:center;gap:2px;min-width:22px;">
                <span style="font-weight:600;font-size:11px;color:${ex.color};line-height:1;">${ex.label}</span>
                <img src="https://www.google.com/s2/favicons?domain=${ex.domain}&sz=32" onerror="this.style.display='none'" style="width:12px;height:12px;border-radius:2px;display:block;">
            </div>
            ${marketsForEx.map(m => mkToggle(m, m === 'alpha' ? 'A' : (m === 'futures' ? 'F' : 'S'))).join('')}
        </div>`;
    }).join('');
}

function toggleReconMarket(exId, market) {
    if (!reconMarkets[exId] || reconMarkets[exId][market] === undefined) return;

    reconMarkets[exId][market] = !reconMarkets[exId][market];
    localStorage.setItem('reconMarkets', JSON.stringify(reconMarkets));
    renderReconPanel();

    const hasEnabled = RECON_EXCHANGES.some(ex => {
        return Object.values(reconMarkets[ex.id] || {}).some(v => v === true);
    });

    if (typeof currentSymbol !== 'undefined' && currentSymbol && hasEnabled) {
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
    loadReconDensities(symbol);
    reconUpdateTimer = setInterval(() => {
        if (typeof currentSymbol !== 'undefined' && currentSymbol === symbol && reconEnabled) {
            loadReconDensities(symbol);
        }
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
        const marketsForEx = ex.id === 'binance_alpha' ? ['alpha'] : ['spot', 'futures'];

        const inputs = marketsForEx.map(m => {
            const label = m === 'alpha' ? 'A:' : (m === 'futures' ? 'F:' : 'S:');
            const val = reconMinVolumes[ex.id] ? reconMinVolumes[ex.id][m] : 10000;
            return `<span style="font-size:11px;color:#94a3b8;min-width:10px;">${label}</span>
                    <input type="number" id="reconMin_${m}_${ex.id}" value="${val}" min="1000" step="1000"
                           style="width:70px;background:#1e293b;border:1px solid #475569;color:#fff;padding:4px 6px;border-radius:3px;font-size:12px;"
                           onchange="updateReconMinVolume('${ex.id}', '${m}', this.value)">`;
        }).join('');

        return `<div style="display:flex;align-items:center;gap:6px;">
            <img src="https://www.google.com/s2/favicons?domain=${ex.domain}&sz=32" onerror="this.style.display='none'" style="width:16px;height:16px;border-radius:2px;flex-shrink:0;">
            <span style="font-weight:600;font-size:12px;color:${ex.color};min-width:24px;">${ex.label}</span>
            ${inputs}
        </div>`;
    }).join('');
}

// Глобальная функция для обновления порогов из UI
window.updateReconMinVolume = function(exId, market, value) {
    if (!reconMinVolumes[exId]) reconMinVolumes[exId] = {};
    reconMinVolumes[exId][market] = Number(value) || 10000;
    localStorage.setItem('reconMinVolumes', JSON.stringify(reconMinVolumes));
    if (typeof currentSymbol !== 'undefined' && currentSymbol) {
        loadReconDensities(currentSymbol);
    }
};

function toggleReconSettings() {
    const toggle = document.getElementById('reconPanelToggle');
    const section = document.getElementById('reconSettingsSection');
    if (toggle && section) {
        section.style.display = toggle.checked ? 'block' : 'none';
    }
}