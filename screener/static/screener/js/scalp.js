// ==========================================
// scalp.js — SCALP (ПЛОТНОСТИ С БИРЖ)
// Загрузка через /api/scalp/, отрисовка линий, настройки бирж
// ==========================================

// --- Переменные состояния ---
let scalpLines = [];
let scalpEnabled = false;
let scalpUpdateTimer = null;
let previousScalpData = {};

// Конфигурация бирж (легко расширяется — добавь строку)
const EXCHANGES_CONFIG = [
    { id: 'binance', name: 'Binance', label: 'BI',   domain: 'binance.com', color: '#f59e0b' },
    { id: 'bybit',   name: 'Bybit',   label: 'BY',   domain: 'bybit.com',   color: '#f59e0b' },
    { id: 'okx',     name: 'OKX',     label: 'OKX',  domain: 'okx.com',     color: '#ffffff' },
    { id: 'gate',    name: 'Gate.io', label: 'GT',   domain: 'gate.io',     color: '#f59e0b' },
    { id: 'mexc',    name: 'MEXC',    label: 'MEX',  domain: 'mexc.com',    color: '#f59e0b' },
    { id: 'bitget',  name: 'Bitget',  label: 'BGB',  domain: 'bitget.com',  color: '#f59e0b' },
    { id: 'binance_alpha', name: 'Binance Alpha', label: 'BA', domain: 'binance.com', color: '#8b5cf6' },
];

// Текущие настройки каждой биржи
let scalpExchanges = {
    binance: { enabled: true, markets: { futures: true, spot: false }, minVolumeFutures: 200000, minVolumeSpot: 100000 },
    bybit:   { enabled: true, markets: { futures: true, spot: false }, minVolumeFutures: 200000, minVolumeSpot: 100000 },
    okx:     { enabled: true, markets: { futures: true, spot: false }, minVolumeFutures: 200000, minVolumeSpot: 100000 },
    gate:    { enabled: true, markets: { futures: true, spot: true },  minVolumeFutures: 200000, minVolumeSpot: 100000 },
    mexc:    { enabled: true, markets: { futures: true, spot: true },  minVolumeFutures: 200000, minVolumeSpot: 100000 },
    bitget:  { enabled: true, markets: { futures: true, spot: true },  minVolumeFutures: 200000, minVolumeSpot: 100000 },
    binance_alpha: { enabled: true, markets: { futures: true, spot: false }, minVolumeFutures: 50000, minVolumeSpot: 50000 },
};

// Миграция любого старого формата + подхват сохранённых значений
try {
    const saved = JSON.parse(localStorage.getItem('scalpExchanges') || 'null');
    if (saved && typeof saved === 'object') {
        for (const id of Object.keys(scalpExchanges)) {
            const s = saved[id];
            if (!s) continue;
            if (typeof s === 'boolean') {
                scalpExchanges[id].enabled = s;
            } else if (typeof s === 'object') {
                scalpExchanges[id].enabled = s.enabled !== false;
                if (s.markets) {
                    scalpExchanges[id].markets.futures = !!s.markets.futures;
                    scalpExchanges[id].markets.spot    = !!s.markets.spot;
                }
                if (Number(s.minVolumeFutures) > 0) scalpExchanges[id].minVolumeFutures = Number(s.minVolumeFutures);
                if (Number(s.minVolumeSpot)    > 0) scalpExchanges[id].minVolumeSpot    = Number(s.minVolumeSpot);
            }
        }
    }
} catch(e) { console.warn('⚠️ scalpExchanges повреждён'); }

// Вычисляем scalpEnabled при загрузке
scalpEnabled = Object.values(scalpExchanges).some(cfg => cfg.enabled && (cfg.markets.futures || cfg.markets.spot));

// ==========================================
// ЗАГРУЗКА ПЛОТНОСТЕЙ
// ==========================================
async function loadScalpDensities(symbol) {
    if (!candleSeries || isScalpLoading) return;
    // Если скальп выключен — очищаем линии и выходим
    if (!scalpEnabled) {
        if (scalpLines.length > 0) clearScalpLines();
        previousScalpData = {};
        return;
    }
    isScalpLoading = true;
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
        }
        // Если нет включённых бирж/рынков — очищаем линии и выходим
        if (loadList.length === 0) {
            if (scalpLines.length > 0) clearScalpLines();
            previousScalpData = {};
            return;
        }
        // Удаляем кэш для выключенных бирж/рынков
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
                // Фильтруем по бирже И по возрасту >= 180 сек
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
        // Проверяем изменения
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
        // Проверяем удалённые ключи (были изменения)
        if (Object.keys(previousScalpData).length !== activeKeys.size) hasChanges = true;
        if (!hasChanges) return;
        // Очищаем ВСЕ линии перед перерисовкой
        clearScalpLines();
        for (const key in allNewData) {
            const [exchange, market] = key.split('|');
            const densities = allNewData[key];
            const PREFIX = { binance: 'BI', bybit: 'BY', okx: 'OK', gate: 'G', mexc: 'MX', bitget: 'BG' };
            const exchangePrefix = PREFIX[exchange] || exchange.slice(0, 2).toUpperCase();
            const marketSuffix = market === 'futures' ? 'F' : 'S';
            const prefix = `${exchangePrefix}-${marketSuffix}`;
            densities.forEach(d => {
                const ageSeconds = d.age_seconds || 0;
                const ageText = formatAge(ageSeconds);
                const volumeText = formatVolumeText(d.volume);
                const volumeNum = parseFloat(d.volume) || 0;
                const lineColor = volumeNum < 500000 ? 'rgba(251, 191, 36, 0.9)' : 'rgba(186, 85, 211, 0.9)';
                const line = candleSeries.createPriceLine({
                    price: d.price, color: lineColor, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true, axisLabelColor: '#000000', axisLabelBackgroundColor: lineColor,
                    title: `${prefix} ${volumeText} ${ageText}`
                });
                scalpLines.push(line);
            });
        }
    } catch (err) {
        console.error('Scalp load error:', err);
    } finally {
        isScalpLoading = false;
    }
}

function clearScalpLines() {
    scalpLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch(e){} });
    scalpLines = [];
}

function startScalpUpdates(symbol) {
    if (scalpUpdateTimer) clearInterval(scalpUpdateTimer);
    loadScalpDensities(symbol);
    scalpUpdateTimer = setInterval(() => {
        if (currentSymbol === symbol && scalpEnabled) loadScalpDensities(symbol);
    }, 3000);
}

// ==========================================
// НАСТРОЙКИ SCALP (отдельная модалка)
// ==========================================
function openScalpSettingsModal() {
    const container = document.getElementById('scalpExchangesContainer');
    container.innerHTML = EXCHANGES_CONFIG.map(ex => {
        const cfg = scalpExchanges[ex.id] || { enabled: false, markets: { futures: false, spot: false }, minVolumeFutures: 200000, minVolumeSpot: 100000 };
        return `<div class="exchange-card" style="background:#3b4252; border:1px solid #475569; border-radius:6px; padding:14px;">
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px;">
                <div style="display:flex; align-items:center; gap:8px;">
                    <span style="position:relative; width:26px; height:26px; display:inline-block;">
                        <span style="position:absolute; inset:0; display:flex; align-items:center; justify-content:center; font-weight:700; color:${ex.color}; font-size:14px;">${ex.name[0]}</span>
                        <img src="https://www.google.com/s2/favicons?domain=${ex.domain}&sz=64" onerror="this.style.display='none'" style="position:relative; width:26px; height:26px; border-radius:6px;">
                    </span>
                    <span style="font-weight:600; color:${ex.color}; font-size:14px; min-width:90px;">${ex.label || ex.name}</span>
                </div>
                <label style="display:flex; align-items:center; gap:6px; cursor:pointer; font-size:12px; color:#e2e8f0;">
                    <input type="checkbox" id="scalpEnabled_${ex.id}" ${cfg.enabled ? 'checked' : ''} style="accent-color:${ex.color}; width:16px; height:16px;">
                    <span>Включить</span>
                </label>
            </div>
            <div style="display:grid; grid-template-columns: 1fr 1fr; gap:10px;">
                <div style="background:#1e293b; border:1px solid #475569; border-radius:4px; padding:10px;">
                    <label style="display:flex; align-items:center; gap:6px; cursor:pointer; font-size:12px; color:#e2e8f0; margin-bottom:8px;">
                        <input type="checkbox" id="scalpFutures_${ex.id}" ${cfg.markets.futures ? 'checked' : ''} style="accent-color:${ex.color}; width:14px; height:14px;">
                        <span>Futures</span>
                    </label>
                    <label style="font-size:10px; color:#94a3b8; display:block; margin-bottom:4px;">Мин. объём (USDT):</label>
                    <input type="number" id="scalpMinFutures_${ex.id}" value="${cfg.minVolumeFutures}" min="200000" step="10000" style="width:100%; background:#1e293b; border:1px solid #475569; color:#fff; padding:5px 8px; border-radius:3px; font-size:12px;">
                </div>
                <div style="background:#1e293b; border:1px solid #475569; border-radius:4px; padding:10px;">
                    <label style="display:flex; align-items:center; gap:6px; cursor:pointer; font-size:12px; color:#e2e8f0; margin-bottom:8px;">
                        <input type="checkbox" id="scalpSpot_${ex.id}" ${cfg.markets.spot ? 'checked' : ''} style="accent-color:${ex.color}; width:14px; height:14px;">
                        <span>Spot</span>
                    </label>
                    <label style="font-size:10px; color:#94a3b8; display:block; margin-bottom:4px;">Мин. объём (USDT):</label>
                    <input type="number" id="scalpMinSpot_${ex.id}" value="${cfg.minVolumeSpot}" min="100000" step="10000" style="width:100%; background:#1e293b; border:1px solid #475569; color:#fff; padding:5px 8px; border-radius:3px; font-size:12px;">
                </div>
            </div>
        </div>`;
    }).join('');
    const modal = new bootstrap.Modal(document.getElementById('scalpSettingsModal'));
    modal.show();
}

function applyScalpSettings() {
    EXCHANGES_CONFIG.forEach(ex => {
        const toggle = document.getElementById(`scalp-${ex.id}-toggle`);
        const fCheckbox = document.getElementById(`scalp-${ex.id}-f`);
        const sCheckbox = document.getElementById(`scalp-${ex.id}-s`);
        const fInput = document.getElementById(`scalp-${ex.id}-fv`);
        const sInput = document.getElementById(`scalp-${ex.id}-sv`);
        if (!scalpExchanges[ex.id]) {
            scalpExchanges[ex.id] = { enabled: false, markets: { futures: false, spot: false }, minVolumeFutures: 200000, minVolumeSpot: 100000 };
        }
        scalpExchanges[ex.id].enabled = toggle ? toggle.checked : false;
        scalpExchanges[ex.id].markets.futures = fCheckbox ? fCheckbox.checked : false;
        scalpExchanges[ex.id].markets.spot = sCheckbox ? sCheckbox.checked : false;
        scalpExchanges[ex.id].minVolumeFutures = fInput ? parseInt(fInput.value) || 200000 : 200000;
        scalpExchanges[ex.id].minVolumeSpot = sInput ? parseInt(sInput.value) || 100000 : 100000;
    });
    localStorage.setItem('scalpExchanges', JSON.stringify(scalpExchanges));
    scalpEnabled = Object.values(scalpExchanges).some(cfg =>
        cfg.enabled && (cfg.markets.futures || cfg.markets.spot)
    );
    // Всегда очищаем линии перед перерисовкой
    if (currentSymbol && candleSeries) {
        clearScalpLines();
        previousScalpData = {};
    }
    // Перезапускаем обновление
    if (currentSymbol) {
        if (scalpEnabled) startScalpUpdates(currentSymbol);
        else {
            if (scalpUpdateTimer) { clearInterval(scalpUpdateTimer); scalpUpdateTimer = null; }
        }
    }
    const modal = bootstrap.Modal.getInstance(document.getElementById('settingsModal'));
    if (modal) modal.hide();
}