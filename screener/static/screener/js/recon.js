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
};
let reconMinVolumes = {
    binance: { spot: 10000, futures: 10000 },
    bybit:   { spot: 10000, futures: 10000 },
    okx:     { spot: 10000, futures: 10000 },
    gate:    { spot: 10000, futures: 10000 },
    mexc:    { spot: 10000, futures: 10000 },
    bitget:  { spot: 10000, futures: 10000 }
};

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
} catch (e) {}

const RECON_EXCHANGES = [
    { id: 'binance', label: 'BI', color: '#f59e0b', domain: 'binance.com' },
    { id: 'bybit',   label: 'BY', color: '#f59e0b', domain: 'bybit.com' },
    { id: 'okx',     label: 'OKX', color: '#f59e0b', domain: 'okx.com' },
    { id: 'gate',    label: 'GT',  color: '#f59e0b', domain: 'gate.io' },
    { id: 'mexc',    label: 'MEX', color: '#f59e0b', domain: 'mexc.com' },
    { id: 'bitget',  label: 'BGB', color: '#f59e0b', domain: 'bitget.com' },
];

/// ==========================================
// 🔥 ИСПРАВЛЕННАЯ ФУНКЦИЯ: Получение снапшота Gate.io через WebSocket
// ==========================================
function fetchGateReconViaWS(symbol, market) {
    return new Promise((resolve) => {
        const isFutures = market === 'futures';
        const wsUrl = isFutures ? "wss://fx-ws.gateio.ws/v4/ws/usdt" : "wss://api.gateio.ws/ws/v4/";
        const channel = isFutures ? "futures.order_book_update" : "spot.order_book_update";

        // Гарантируем формат BTC_USDT (даже если пришло BTC или BTCUSDT)
        const cleanSymbol = symbol.toUpperCase().replace('USDT', '') + '_USDT';

        try {
            const ws = new WebSocket(wsUrl);
            let resolved = false;

            // Увеличили таймаут до 5 секунд для надежности
            const timeout = setTimeout(() => {
                if (!resolved) {
                    resolved = true;
                    try { ws.close(); } catch(e) {}
                    console.warn(`⚠️ Gate WS таймаут для ${cleanSymbol}`);
                    resolve([]);
                }
            }, 5000);

            ws.onopen = () => {
                const subscribeMsg = {
                    time: Math.floor(Date.now() / 1000),
                    channel: channel,
                    event: "subscribe",
                    payload: isFutures ? [cleanSymbol, "100ms", "100"] : [cleanSymbol, "100ms"],
                    id: 1 // 🔥 КРИТИЧНО: Gate.io требует id для корректной обработки подписки
                };
                ws.send(JSON.stringify(subscribeMsg));
            };

            ws.onmessage = (e) => {
                if (resolved) return;
                try {
                    const msg = JSON.parse(e.data);

                    // Игнорируем системные сообщения и подтверждения подписки
                    if (msg.event === 'pong' || msg.channel === 'futures.ping' || msg.channel === 'spot.ping' || msg.event === 'subscribe') {
                        return;
                    }

                    // 🔥 Ловим первый же пакет с данными стакана для нашего символа
                    // (Первый пакет после подписки у Gate.io ВСЕГДА является полным снапшотом)
                    if (msg.channel === channel && msg.result && msg.result.s === cleanSymbol) {
                        resolved = true;
                        clearTimeout(timeout);
                        try { ws.close(); } catch(e) {}

                        console.log(`✅ Gate WS получил снапшот: ${cleanSymbol} | Bids: ${msg.result.b.length}, Asks: ${msg.result.a.length}`);

                        resolve({
                            bids: msg.result.b || [],
                            asks: msg.result.a || []
                        });
                    }
                } catch (err) {
                    // Игнорируем ошибки парсинга
                }
            };

            ws.onerror = () => {
                if (!resolved) {
                    resolved = true;
                    clearTimeout(timeout);
                    try { ws.close(); } catch(e) {}
                    console.error(`❌ Gate WS ошибка соединения для ${cleanSymbol}`);
                    resolve([]);
                }
            };

            ws.onclose = () => {
                if (!resolved) {
                    resolved = true;
                    clearTimeout(timeout);
                    resolve([]);
                }
            };
        } catch (err) {
            console.error(`❌ Gate WS ошибка создания:`, err);
            resolve([]);
        }
    });
}

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
            allNewData[market] = densities.slice(0, 20);
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

    // 🔥 Gate теперь через WS, REST URL не нужен
    if (exId === 'gate') return null;
    if (exId === 'mexc') return `/api/mexc-depth/?market=${market}&symbol=${symbol}`;
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
    }
    const toLevel = (row) => {
        if (Array.isArray(row)) return [parseFloat(row[0]), Math.abs(parseFloat(row[1]))];
        if (row && typeof row === 'object') return [parseFloat(row.p || row.price), Math.abs(parseFloat(row.v || row.vol))];
        return [NaN, NaN];
    };
    return { rawBids, rawAsks, toLevel };
}

async function fetchReconMarket(exId, symbol, market) {
    let data;
    try {
        // 🔥 Gate.io работает через WebSocket (без серверного прокси)
        if (exId === 'gate') {
            data = await fetchGateReconViaWS(symbol, market);
            if (!data || !data.bids) return [];
        } else if (exId === 'mexc') {
            const res = await fetch(`/api/mexc-depth/?market=${market}&symbol=${symbol}`);
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
        if (exId === 'okx') {
            if (data.code !== undefined && data.code !== '0' && data.code !== 0) return [];
        } else if (exId === 'bitget') {
            if (data.code !== undefined && data.code !== '00000' && data.code !== 0) return [];
        } else if (exId === 'bybit') {
            if (data.retCode !== undefined && data.retCode !== 0) return [];
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
            const vol = p * q;
            if (vol >= minVolume) out.push({ price: p, volume: vol });
        }
    };
    push(rawBids); push(rawAsks);
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
        return `<div style="display:flex;align-items:center;gap:5px;">
            <div style="display:flex;flex-direction:column;align-items:center;gap:2px;min-width:22px;">
                <span style="font-weight:600;font-size:11px;color:${ex.color};line-height:1;">${ex.label}</span>
                <img src="https://www.google.com/s2/favicons?domain=${ex.domain}&sz=32" onerror="this.style.display='none'" style="width:12px;height:12px;border-radius:2px;display:block;">
            </div>
            ${mkToggle('spot', 'S')}
            ${mkToggle('futures', 'F')}
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
    container.innerHTML = RECON_EXCHANGES.map(ex => `<div style="display:flex;align-items:center;gap:6px;">
        <img src="https://www.google.com/s2/favicons?domain=${ex.domain}&sz=32" onerror="this.style.display='none'" style="width:16px;height:16px;border-radius:2px;flex-shrink:0;">
        <span style="font-weight:600;font-size:12px;color:${ex.color};min-width:24px;">${ex.label}</span>
        <span style="font-size:11px;color:#94a3b8;min-width:10px;">F:</span>
        <input type="number" id="reconMinF_${ex.id}" value="${reconMinVolumes[ex.id].futures}" min="10000" step="1000" style="width:70px;background:#1e293b;border:1px solid #475569;color:#fff;padding:4px 6px;border-radius:3px;font-size:12px;">
        <span style="font-size:11px;color:#94a3b8;min-width:10px;">S:</span>
        <input type="number" id="reconMinS_${ex.id}" value="${reconMinVolumes[ex.id].spot}" min="10000" step="1000" style="width:70px;background:#1e293b;border:1px solid #475569;color:#fff;padding:4px 6px;border-radius:3px;font-size:12px;">
    </div>`).join('');
}

function toggleReconSettings() {
    const toggle = document.getElementById('reconPanelToggle');
    const section = document.getElementById('reconSettingsSection');
    if (toggle && section) {
        section.style.display = toggle.checked ? 'block' : 'none';
    }
}