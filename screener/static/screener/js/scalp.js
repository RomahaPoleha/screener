// ==========================================
// scalp.js — SCALP (ПЛОТНОСТИ С БИРЖ)
// Загрузка через /api/scalp/, отрисовка линий, настройки бирж
// Включена поддержка Binance Alpha
// ==========================================

// --- Переменные состояния ---
let scalpLines = [];
let scalpEnabled = false;
let scalpUpdateTimer = null;
let previousScalpData = {};
let isScalpLoading = false; // Добавлено для предотвращения параллельных запросов

// Конфигурация бирж (добавлен binance_alpha)
const EXCHANGES_CONFIG = [
    { id: 'binance',       name: 'Binance',       label: 'BI',  domain: 'binance.com', color: '#f59e0b' },
    { id: 'binance_alpha', name: 'Binance Alpha', label: 'BA',  domain: 'binance.com', color: '#8b5cf6' }, // Фиолетовый для отличия
    { id: 'bybit',         name: 'Bybit',         label: 'BY',  domain: 'bybit.com',   color: '#f59e0b' },
    { id: 'okx',           name: 'OKX',           label: 'OKX', domain: 'okx.com',     color: '#ffffff' },
    { id: 'gate',          name: 'Gate.io',       label: 'GT',  domain: 'gate.io',     color: '#17e8a5' },
    { id: 'mexc',          name: 'MEXC',          label: 'MEX', domain: 'mexc.com',    color: '#17e8a5' },
    { id: 'bitget',        name: 'Bitget',        label: 'BGB', domain: 'bitget.com',  color: '#00f0e6' },
];

// Текущие настройки каждой биржи (добавлен market: alpha и minVolumeAlpha)
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

            // Инициализация, если ключа нет
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
                    scalpExchanges[id].markets.alpha   = !!s.markets.alpha; // Подхват alpha
                }
                if (Number(s.minVolumeFutures) > 0) scalpExchanges[id].minVolumeFutures = Number(s.minVolumeFutures);
                if (Number(s.minVolumeSpot)    > 0) scalpExchanges[id].minVolumeSpot    = Number(s.minVolumeSpot);
                if (Number(s.minVolumeAlpha)   > 0) scalpExchanges[id].minVolumeAlpha   = Number(s.minVolumeAlpha); // Подхват minVolumeAlpha
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
    if (!window.candleSeries || isScalpLoading) return;

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
            if (ex.markets.alpha) {
                loadList.push({ exchange: exId, market: 'alpha', minVol: ex.minVolumeAlpha });
                activeKeys.add(`${exId}|alpha`);
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
                // Запрос к Django API. Убедитесь, что бэкенд обрабатывает market='alpha' и читает ключ scalp:alpha:binance:{symbol}
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
                const ageText = formatAge(ageSeconds); // Убедитесь, что эта функция определена в вашем основном коде
                const volumeText = formatVolumeText(d.volume); // Убедитесь, что эта функция определена в вашем основном коде
                const volumeNum = parseFloat(d.volume) || 0;

                // Цветовая кодировка: жёлтый до 500k, фиолетовый (как Alpha) для крупных плотностей
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
        isScalpLoading = false;
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
// НАСТРОЙКИ SCALP (отдельная модалка)
// ==========================================
function openScalpSettingsModal() {
    const container = document.getElementById('scalpExchangesContainer');
    if (!container) return;

    container.innerHTML = EXCHANGES_CONFIG.map(ex => {
        const cfg = scalpExchanges[ex.id] || { enabled: false, markets: { futures: false, spot: false, alpha: false }, minVolumeFutures: 200000, minVolumeSpot: 100000, minVolumeAlpha: 50000 };

        // Специальная разметка для Binance Alpha (один блок вместо двух)
        let marketsHtml = '';
        if (ex.id === 'binance_alpha') {
            marketsHtml = `
            <div style="background:#1e293b; border:1px solid #475569; border-radius:4px; padding:10px; grid-column: span 2;">
                <label style="display:flex; align-items:center; gap:6px; cursor:pointer; font-size:12px; color:#e2e8f0; margin-bottom:8px;">
                    <input type="checkbox" id="scalpAlpha_${ex.id}" ${cfg.markets.alpha ? 'checked' : ''} style="accent-color:${ex.color}; width:14px; height:14px;">
                    <span>Alpha Market</span>
                </label>
                <label style="font-size:10px; color:#94a3b8; display:block; margin-bottom:4px;">Мин. объём (USDT):</label>
                <input type="number" id="scalpMinAlpha_${ex.id}" value="${cfg.minVolumeAlpha}" min="10000" step="10000" style="width:100%; background:#1e293b; border:1px solid #475569; color:#fff; padding:5px 8px; border-radius:3px; font-size:12px;">
            </div>`;
        } else {
            // Стандартная разметка для Futures и Spot
            marketsHtml = `
            <div style="background:#1e293b; border:1px solid #475569; border-radius:4px; padding:10px;">
                <label style="display:flex; align-items:center; gap:6px; cursor:pointer; font-size:12px; color:#e2e8f0; margin-bottom:8px;">
                    <input type="checkbox" id="scalpFutures_${ex.id}" ${cfg.markets.futures ? 'checked' : ''} style="accent-color:${ex.color}; width:14px; height:14px;">
                    <span>Futures</span>
                </label>
                <label style="font-size:10px; color:#94a3b8; display:block; margin-bottom:4px;">Мин. объём (USDT):</label>
                <input type="number" id="scalpMinFutures_${ex.id}" value="${cfg.minVolumeFutures}" min="10000" step="10000" style="width:100%; background:#1e293b; border:1px solid #475569; color:#fff; padding:5px 8px; border-radius:3px; font-size:12px;">
            </div>
            <div style="background:#1e293b; border:1px solid #475569; border-radius:4px; padding:10px;">
                <label style="display:flex; align-items:center; gap:6px; cursor:pointer; font-size:12px; color:#e2e8f0; margin-bottom:8px;">
                    <input type="checkbox" id="scalpSpot_${ex.id}" ${cfg.markets.spot ? 'checked' : ''} style="accent-color:${ex.color}; width:14px; height:14px;">
                    <span>Spot</span>
                </label>
                <label style="font-size:10px; color:#94a3b8; display:block; margin-bottom:4px;">Мин. объём (USDT):</label>
                <input type="number" id="scalpMinSpot_${ex.id}" value="${cfg.minVolumeSpot}" min="10000" step="10000" style="width:100%; background:#1e293b; border:1px solid #475569; color:#fff; padding:5px 8px; border-radius:3px; font-size:12px;">
            </div>`;
        }

        return `
        <div class="exchange-card" style="background:#3b4252; border:1px solid #475569; border-radius:6px; padding:14px;">
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px;">
                <div style="display:flex; align-items:center; gap:8px;">
                    <span style="position:relative; width:26px; height:26px; display:inline-block;">
                        <img src="https://www.google.com/s2/favicons?domain=${ex.domain}&sz=64" onerror="this.style.display='none'" style="position:relative; width:26px; height:26px; border-radius:6px; background:#fff;">
                    </span>
                    <span style="font-weight:600; color:${ex.color}; font-size:14px; min-width:110px;">${ex.name}</span>
                </div>
                <label style="display:flex; align-items:center; gap:6px; cursor:pointer; font-size:12px; color:#e2e8f0;">
                    <input type="checkbox" id="scalpEnabled_${ex.id}" ${cfg.enabled ? 'checked' : ''} style="accent-color:${ex.color}; width:16px; height:16px;">
                    <span>Включить</span>
                </label>
            </div>
            <div style="display:grid; grid-template-columns: 1fr 1fr; gap:10px;">
                ${marketsHtml}
            </div>
        </div>`;
    }).join('');

    const modalEl = document.getElementById('scalpSettingsModal');
    if (modalEl) {
        const modal = new bootstrap.Modal(modalEl);
        modal.show();
    }
}

function applyScalpSettings() {
    EXCHANGES_CONFIG.forEach(ex => {
        const enabledToggle = document.getElementById(`scalpEnabled_${ex.id}`);

        if (!scalpExchanges[ex.id]) {
            scalpExchanges[ex.id] = { enabled: false, markets: { futures: false, spot: false, alpha: false }, minVolumeFutures: 200000, minVolumeSpot: 100000, minVolumeAlpha: 50000 };
        }

        scalpExchanges[ex.id].enabled = enabledToggle ? enabledToggle.checked : false;

        // Обработка стандартных рынков
        const fCheckbox = document.getElementById(`scalpFutures_${ex.id}`);
        const sCheckbox = document.getElementById(`scalpSpot_${ex.id}`);
        const fInput = document.getElementById(`scalpMinFutures_${ex.id}`);
        const sInput = document.getElementById(`scalpMinSpot_${ex.id}`);

        if (fCheckbox) scalpExchanges[ex.id].markets.futures = fCheckbox.checked;
        if (sCheckbox) scalpExchanges[ex.id].markets.spot = sCheckbox.checked;
        if (fInput) scalpExchanges[ex.id].minVolumeFutures = parseInt(fInput.value) || 200000;
        if (sInput) scalpExchanges[ex.id].minVolumeSpot = parseInt(sInput.value) || 100000;

        // Обработка рынка Alpha
        const alphaCheckbox = document.getElementById(`scalpAlpha_${ex.id}`);
        const alphaInput = document.getElementById(`scalpMinAlpha_${ex.id}`);

        if (alphaCheckbox) scalpExchanges[ex.id].markets.alpha = alphaCheckbox.checked;
        if (alphaInput) scalpExchanges[ex.id].minVolumeAlpha = parseInt(alphaInput.value) || 50000;
    });

    localStorage.setItem('scalpExchanges', JSON.stringify(scalpExchanges));

    scalpEnabled = Object.values(scalpExchanges).some(cfg =>
        cfg.enabled && (cfg.markets.futures || cfg.markets.spot || cfg.markets.alpha)
    );

    // Всегда очищаем линии перед перерисовкой
    if (window.currentSymbol && window.candleSeries) {
        clearScalpLines();
        previousScalpData = {};
    }

    // Перезапускаем обновление
    if (window.currentSymbol) {
        if (scalpEnabled) {
            startScalpUpdates(window.currentSymbol);
        } else {
            if (scalpUpdateTimer) { clearInterval(scalpUpdateTimer); scalpUpdateTimer = null; }
        }
    }

    const modalEl = document.getElementById('scalpSettingsModal'); // Исправлено на правильный ID модалки
    if (modalEl) {
        const modal = bootstrap.Modal.getInstance(modalEl);
        if (modal) modal.hide();
    }
}