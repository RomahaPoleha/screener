// ==========================================
// scalp.js — SCALP (ПЛОТНОСТИ С БИРЖ)
// Загрузка через /api/scalp/, отрисовка линий, настройки бирж
// ==========================================

// --- Переменные состояния ---
// (isScalpLoading уже объявлен в state_core.js, здесь не объявляем!)
let scalpLines = [];
let scalpEnabled = false;
let scalpUpdateTimer = null;
let previousScalpData = {};

// Конфигурация бирж
const EXCHANGES_CONFIG = [
    { id: 'binance',       name: 'Binance',       label: 'BI',  domain: 'binance.com', color: '#f59e0b' },
    { id: 'binance_alpha', name: 'Binance Alpha', label: 'BA',  domain: 'binance.com', color: '#8b5cf6' },
    { id: 'bybit',         name: 'Bybit',         label: 'BY',  domain: 'bybit.com',   color: '#f59e0b' },
    { id: 'okx',           name: 'OKX',           label: 'OKX', domain: 'okx.com',     color: '#ffffff' },
    { id: 'gate',          name: 'Gate.io',       label: 'GT',  domain: 'gate.io',     color: '#17e8a5' },
    { id: 'mexc',          name: 'MEXC',          label: 'MEX', domain: 'mexc.com',    color: '#17e8a5' },
    { id: 'bitget',        name: 'Bitget',        label: 'BGB', domain: 'bitget.com',  color: '#00f0e6' },
];

// Текущие настройки каждой биржи
let scalpExchanges = {
    binance:       { enabled: true, markets: { futures: true, spot: false, alpha: false }, minVolumeFutures: 200000, minVolumeSpot: 100000, minVolumeAlpha: 50000 },
    binance_alpha: { enabled: true, markets: { alpha: true }, minVolumeAlpha: 50000 },
    bybit:         { enabled: true, markets: { futures: true, spot: false, alpha: false }, minVolumeFutures: 200000, minVolumeSpot: 100000, minVolumeAlpha: 50000 },
    okx:           { enabled: true, markets: { futures: true, spot: false, alpha: false }, minVolumeFutures: 200000, minVolumeSpot: 100000, minVolumeAlpha: 50000 },
    gate:          { enabled: true, markets: { futures: true, spot: true, alpha: false },  minVolumeFutures: 200000, minVolumeSpot: 100000, minVolumeAlpha: 50000 },
    mexc:          { enabled: true, markets: { futures: true, spot: true, alpha: false },  minVolumeFutures: 200000, minVolumeSpot: 100000, minVolumeAlpha: 50000 },
    bitget:        { enabled: true, markets: { futures: true, spot: true, alpha: false },  minVolumeFutures: 200000, minVolumeSpot: 100000, minVolumeAlpha: 50000 },
};

// Миграция любого старого формата + подхват сохранённых значений
try {
    const saved = JSON.parse(localStorage.getItem('scalpExchanges') || 'null');
    if (saved && typeof saved === 'object') {
        for (const id of Object.keys(scalpExchanges)) {
            const s = saved[id];
            if (!s) continue;

            if (!scalpExchanges[id]) {
                scalpExchanges[id] = { enabled: false, markets: { futures: false, spot: false, alpha: false }, minVolumeFutures: 200000, minVolumeSpot: 100000, minVolumeAlpha: 50000 };
            }

            if (typeof s === 'boolean') {
                scalpExchanges[id].enabled = s;
            } else if (typeof s === 'object') {
                scalpExchanges[id].enabled = s.enabled !== false;
                if (s.markets) {
                    scalpExchanges[id].markets.futures = !!s.markets.futures;
                    scalpExchanges[id].markets.spot    = !!s.markets.spot;
                    scalpExchanges[id].markets.alpha   = !!s.markets.alpha;
                }
                if (Number(s.minVolumeFutures) > 0) scalpExchanges[id].minVolumeFutures = Number(s.minVolumeFutures);
                if (Number(s.minVolumeSpot)    > 0) scalpExchanges[id].minVolumeSpot    = Number(s.minVolumeSpot);
                if (Number(s.minVolumeAlpha)   > 0) scalpExchanges[id].minVolumeAlpha   = Number(s.minVolumeAlpha);
            }
        }
    }
} catch(e) { console.warn('⚠️ scalpExchanges повреждён или не читается', e); }

// Вычисляем scalpEnabled при загрузке
scalpEnabled = Object.values(scalpExchanges).some(cfg =>
    cfg.enabled && (cfg.markets.futures || cfg.markets.spot || cfg.markets.alpha)
);

// ==========================================
// ЗАГРУЗКА ПЛОТНОСТЕЙ
// ==========================================
async function loadScalpDensities(symbol) {
    // Используем window.isScalpLoading, который объявлен в state_core.js
    if (!window.candleSeries || window.isScalpLoading) return;

    if (!scalpEnabled) {
        if (scalpLines.length > 0) clearScalpLines();
        previousScalpData = {};
        return;
    }

    window.isScalpLoading = true;
    try {
        const loadList = [];
        const activeKeys = new Set();

        for (const exId in scalpExchanges) {
            const ex = scalpExchanges[exId];
            if (!ex.enabled) continue;

            if (ex.markets.futures) {
                loadList.push({ exchange: exId, market: 'futures', minVol: ex.minVolumeFutures });
                activeKeys.add(`${exId}|futures`);
            }
            if (ex.markets.spot) {
                loadList.push({ exchange: exId, market: 'spot', minVol: ex.minVolumeSpot });
                activeKeys.add(`${exId}|spot`);
            }
            if (ex.markets.alpha) {
                loadList.push({ exchange: exId, market: 'alpha', minVol: ex.minVolumeAlpha });
                activeKeys.add(`${exId}|alpha`);
            }
        }

        if (loadList.length === 0) {
            if (scalpLines.length > 0) clearScalpLines();
            previousScalpData = {};
            return;
        }

        for (const key in previousScalpData) {
            if (!activeKeys.has(key)) delete previousScalpData[key];
        }

        const allNewData = {};
        let hasChanges = false;

        for (const item of loadList) {
            const key = `${item.exchange}|${item.market}`;
            try {
                const res = await fetch(`/api/scalp/${symbol}/?min_volume=${item.minVol}&market=${item.market}&limit=50`);
                if (!res.ok) continue;
                const data = await res.json();

                const filtered = (data.densities || []).filter(d => {
                    if ((d.exchange || 'binance') !== item.exchange) return false;
                    if ((d.age_seconds || 0) < 180) return false;
                    return true;
                });
                allNewData[key] = filtered;
            } catch (e) {
                console.error(`Scalp load error (${key}):`, e);
                allNewData[key] = previousScalpData[key] || [];
            }
        }

        for (const key in allNewData) {
            const newData = allNewData[key];
            const prevData = previousScalpData[key] || [];
            const curSig = JSON.stringify(newData.map(d => ({ p: d.price, v: d.volume, s: d.side, e: d.exchange })));
            const prevSig = JSON.stringify(prevData.map(d => ({ p: d.price, v: d.volume, s: d.side, e: d.exchange })));
            if (curSig !== prevSig) {
                hasChanges = true;
                previousScalpData[key] = newData;
            }
        }

        if (Object.keys(previousScalpData).length !== activeKeys.size) hasChanges = true;
        if (!hasChanges) return;

        clearScalpLines();

        for (const key in allNewData) {
            const [exchange, market] = key.split('|');
            const densities = allNewData[key];

            const PREFIX = {
                binance: 'BI',
                binance_alpha: 'BA',
                bybit: 'BY',
                okx: 'OK',
                gate: 'G',
                mexc: 'MX',
                bitget: 'BG'
            };
            const exchangePrefix = PREFIX[exchange] || exchange.slice(0, 2).toUpperCase();
            const marketSuffix = market === 'futures' ? 'F' : (market === 'alpha' ? 'A' : 'S');
            const prefix = `${exchangePrefix}-${marketSuffix}`;

            densities.forEach(d => {
                const ageSeconds = d.age_seconds || 0;
                const ageText = typeof formatAge === 'function' ? formatAge(ageSeconds) : `${ageSeconds}s`;
                const volumeText = typeof formatVolumeText === 'function' ? formatVolumeText(d.volume) : (d.volume >= 1000 ? (d.volume/1000).toFixed(1)+'K' : d.volume);
                const volumeNum = parseFloat(d.volume) || 0;

                const lineColor = volumeNum < 500000 ? 'rgba(251, 191, 36, 0.9)' : 'rgba(186, 85, 211, 0.9)';

                const line = window.candleSeries.createPriceLine({
                    price: d.price,
                    color: lineColor,
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true,
                    axisLabelColor: '#000000',
                    axisLabelBackgroundColor: lineColor,
                    title: `${prefix} ${volumeText} ${ageText}`
                });
                scalpLines.push(line);
            });
        }
    } catch (err) {
        console.error('Scalp load error:', err);
    } finally {
        window.isScalpLoading = false;
    }
}

function clearScalpLines() {
    if (!window.candleSeries) return;
    scalpLines.forEach(l => { try { window.candleSeries.removePriceLine(l); } catch(e){} });
    scalpLines = [];
}

function startScalpUpdates(symbol) {
    if (scalpUpdateTimer) clearInterval(scalpUpdateTimer);
    loadScalpDensities(symbol);
    scalpUpdateTimer = setInterval(() => {
        if (window.currentSymbol === symbol && scalpEnabled) loadScalpDensities(symbol);
    }, 3000);
}

// ==========================================
// НАСТРОЙКИ SCALP
// ==========================================
function renderScalpCards() {
    const container = document.getElementById('scalpExchangesContainer');
    if (!container) return;

    container.innerHTML = EXCHANGES_CONFIG.map(ex => {
        const cfg = scalpExchanges[ex.id] || { enabled: false, markets: { futures: false, spot: false, alpha: false }, minVolumeFutures: 200000, minVolumeSpot: 100000, minVolumeAlpha: 50000 };
        const isEnabled = cfg.enabled !== false;

        const marketsToRender = [];
        if (ex.id === 'binance_alpha') {
            marketsToRender.push({ key: 'alpha', label: 'A', minVolKey: 'minVolumeAlpha', defaultVol: 50000, minAttr: 10000 });
        } else {
            marketsToRender.push({ key: 'futures', label: 'F', minVolKey: 'minVolumeFutures', defaultVol: 200000, minAttr: 10000 });
            marketsToRender.push({ key: 'spot', label: 'S', minVolKey: 'minVolumeSpot', defaultVol: 100000, minAttr: 10000 });
        }

        const marketsHtml = marketsToRender.map(m => {
            const mEnabled = cfg.markets && cfg.markets[m.key];
            const vol = cfg[m.minVolKey] || m.defaultVol;
            return `
                <span style="font-size:11px;color:#94a3b8;min-width:10px;">${m.label}:</span>
                <input type="number" id="scalp-${ex.id}-${m.key}v" value="${vol}" min="${m.minAttr}" step="10000" style="width:70px;background:#1e293b;border:1px solid #475569;color:#fff;padding:4px 6px;border-radius:3px;font-size:12px;" ${!mEnabled || !isEnabled ? 'disabled' : ''}>
                <label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:12px;color:#e2e8f0;">
                    <input type="checkbox" id="scalp-${ex.id}-${m.key}" ${mEnabled ? 'checked' : ''} ${!isEnabled ? 'disabled' : ''} style="accent-color:${ex.color};width:14px;height:14px;" onchange="document.getElementById('scalp-${ex.id}-${m.key}v').disabled = !this.checked">
                    <span>${m.label}</span>
                </label>
            `;
        }).join('');

        return `<div style="display:flex;align-items:center;gap:8px; flex-wrap: wrap;">
            <img src="https://www.google.com/s2/favicons?domain=${ex.domain}&sz=32" onerror="this.style.display='none'" style="width:16px;height:16px;border-radius:2px;flex-shrink:0;">
            <span style="font-weight:600;font-size:12px;color:${ex.color};min-width:24px;">${ex.label || ex.name.substring(0, 2).toUpperCase()}</span>
            ${marketsHtml}
            <label style="position:relative;display:inline-block;width:36px;height:20px;cursor:pointer; margin-left: auto;" title="Включить/выключить биржу">
                <input type="checkbox" id="scalp-${ex.id}-toggle" ${isEnabled ? 'checked' : ''} style="opacity:0;width:0;height:0;" onchange="toggleScalpExchange('${ex.id}', this.checked)">
                <span style="position:absolute;top:0;left:0;right:0;bottom:0;background:${isEnabled ? ex.color : '#475569'};border-radius:20px;transition:.3s;">
                    <span style="position:absolute;height:14px;width:14px;left:3px;bottom:3px;background:#ffffff;border-radius:50%;transition:.3s;transform:${isEnabled ? 'translateX(16px)' : 'translateX(0)'};"></span>
                </span>
            </label>
        </div>`;
    }).join('');
}

function toggleScalpExchange(exchangeId, enabled) {
    if (!scalpExchanges[exchangeId]) {
        scalpExchanges[exchangeId] = { enabled: false, markets: { futures: false, spot: false, alpha: false }, minVolumeFutures: 200000, minVolumeSpot: 100000, minVolumeAlpha: 50000 };
    }
    scalpExchanges[exchangeId].enabled = enabled;
    localStorage.setItem('scalpExchanges', JSON.stringify(scalpExchanges));

    ['futures', 'spot', 'alpha'].forEach(market => {
        const checkbox = document.getElementById(`scalp-${exchangeId}-${market}`);
        const input = document.getElementById(`scalp-${exchangeId}-${market}v`);

        if (checkbox) checkbox.disabled = !enabled;
        if (input) {
            input.disabled = !enabled ? true : !checkbox.checked;
        }
    });

    renderScalpCards();
    applyScalpSettingsSilent();
}

function applyScalpSettingsSilent() {
    window.scalpEnabled = Object.values(scalpExchanges).some(cfg =>
        cfg.enabled && (cfg.markets.futures || cfg.markets.spot || cfg.markets.alpha)
    );

    if (window.currentSymbol && window.candleSeries) {
        clearScalpLines();
        window.previousScalpData = {};
    }

    if (window.currentSymbol) {
        if (window.scalpEnabled) {
            startScalpUpdates(window.currentSymbol);
        } else {
            if (window.scalpUpdateTimer) { clearInterval(window.scalpUpdateTimer); window.scalpUpdateTimer = null; }
        }
    }

    const btn = document.getElementById('settingsBtn');
    if (btn) {
        if (window.densityEnabled || window.scalpEnabled || window.reconEnabled) {
            btn.style.background = '#f59e0b';
            btn.style.color = '#000000';
        } else {
            btn.style.background = '#2a2a2a';
            btn.style.color = '#ffffff';
        }
    }
}