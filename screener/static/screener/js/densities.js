// ==========================================
// densities.js — ТАБЛИЦА ПЛОТНОСТЕЙ
// Загрузка и отображение плотностей из мастер-листа
// ==========================================

let densitiesOverlay = null;
let densitiesOverlayBody = null;
let densitiesBtn = null;
let densitiesMarketSelect = null;
let densitiesMinVolume = null;
let densitiesLimit = null;

let densitiesData = [];
let densitiesSortState = { field: 'volume', direction: 'desc' };

function initDensitiesElements() {
    densitiesOverlay = document.getElementById('densitiesOverlay');
    densitiesOverlayBody = document.getElementById('densitiesOverlayBody');
    densitiesBtn = document.getElementById('densitiesBtn');
    densitiesMarketSelect = document.getElementById('densitiesMarketSelect');
    densitiesMinVolume = document.getElementById('densitiesMinVolume');
    densitiesLimit = document.getElementById('densitiesLimit');
}

function toggleDensitiesOverlay() {
    if (!densitiesOverlay) initDensitiesElements();
    if (densitiesOverlay.classList.contains('active')) {
        closeDensitiesOverlay();
    } else {
        openDensitiesOverlay();
    }
}

function openDensitiesOverlay() {
    if (!densitiesOverlay) initDensitiesElements();
    densitiesOverlay.classList.add('active');
    densitiesBtn.classList.add('active');
    
    const chartContainer = document.getElementById('chart-container');
    if (chartContainer) {
        chartContainer.style.zIndex = '99';
        chartContainer.style.position = 'relative';
    }
    
    loadDensitiesTable();
}

function closeDensitiesOverlay() {
    if (!densitiesOverlay) initDensitiesElements();
    densitiesOverlay.classList.remove('active');
    densitiesBtn.classList.remove('active');
    
    const chartContainer = document.getElementById('chart-container');
    if (chartContainer) {
        chartContainer.style.zIndex = 'auto';
        chartContainer.style.position = 'static';
    }
}

function fmtVolume(v) {
    if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B';
    if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
    if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
    return v.toFixed(0);
}

function fmtAge(sec) {
    if (sec < 60) return sec.toFixed(0) + 's';
    if (sec < 3600) return (sec / 60).toFixed(1) + 'm';
    return (sec / 3600).toFixed(1) + 'h';
}

function getExchangeShort(exchange) {
    const map = {
        'binance': 'BI',
        'bybit': 'BY',
        'okx': 'OKX',
        'gate': 'GT',
        'mexc': 'MEX',
        'bitget': 'BGB',
        'binance_alpha': 'BI-A',
    };
    return map[exchange] || exchange.slice(0, 2).toUpperCase();
}

function getSideColor(side) {
    return side === 'buy' ? '#14b8a6' : '#f59e0b';
}

function getMarketType(symbol) {
    // Можно доработать логику, например: return symbol.includes('USDT') ? 'S' : 'F';
    return 'S'; 
}

function getVolumePercent(volume, maxVolume) {
    if (!maxVolume || maxVolume === 0) return 15;
    const percent = (Number(volume) / maxVolume) * 100;
    return Math.max(15, Math.min(100, percent)); // Минимум 15% для визуальной видимости
}

async function loadDensitiesTable() {
    if (!densitiesOverlayBody) initDensitiesElements();

    const market = densitiesMarketSelect?.value || 'futures';
    const minVolume = parseInt(densitiesMinVolume?.value) || 10000;
    const limit = parseInt(densitiesLimit?.value) || 20;

    densitiesOverlayBody.innerHTML = '<div style="color:#6b7280; text-align:center; padding:20px;">Загрузка...</div>';

    try {
        const params = new URLSearchParams({
            market: market,
            min_volume: minVolume,
            limit: limit,
        });
        const res = await fetch(`/api/densities/?${params.toString()}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        densitiesData = data.densities || [];
        renderDensitiesTable(data);
    } catch (err) {
        console.error('loadDensitiesTable error:', err);
        densitiesOverlayBody.innerHTML = `<div style="color:#ef4444; text-align:center; padding:20px;">Ошибка: ${err.message}</div>`;
    }
}

function sortDensities(field) {
    if (densitiesSortState.field === field) {
        densitiesSortState.direction = densitiesSortState.direction === 'asc' ? 'desc' : 'asc';
    } else {
        densitiesSortState.field = field;
        densitiesSortState.direction = field === 'symbol' ? 'asc' : 'desc';
    }
    renderDensitiesTable({ densities: densitiesData });
}

function renderDensitiesTable(data) {
    if (!densitiesOverlayBody) return;

    const { count, symbols_count, market, server_time } = data;
    let densities = data.densities || [];

    if (!densities || densities.length === 0) {
        densitiesOverlayBody.innerHTML = `
            <div style="color:#6b7280; text-align:center; padding:40px; font-size:14px;">
                <div style="font-size:48px; margin-bottom:16px;"></div>
                <div>Плотностей не найдено</div>
                <div style="margin-top:8px; font-size:12px; opacity:0.7;">
                    Маркет: ${market} | Символов: ${symbols_count}
                </div>
            </div>
        `;
        return;
    }

    const { field, direction } = densitiesSortState;
    densities.sort((a, b) => {
        let valA = a[field];
        let valB = b[field];
        if (field === 'price' || field === 'volume' || field === 'age_seconds') {
            valA = Number(valA); valB = Number(valB);
        } else {
            valA = String(valA).toLowerCase(); valB = String(valB).toLowerCase();
        }
        if (valA < valB) return direction === 'asc' ? -1 : 1;
        if (valA > valB) return direction === 'asc' ? 1 : -1;
        return 0;
    });

    const bySymbol = {};
    for (const d of densities) {
        if (!bySymbol[d.symbol]) bySymbol[d.symbol] = [];
        bySymbol[d.symbol].push(d);
    }

    const symbols = Object.keys(bySymbol).sort();

    let maxVolume = 0;
    for (const d of densities) {
        if (Number(d.volume) > maxVolume) maxVolume = Number(d.volume);
    }

    const getSideColor = (side) => side === 'buy' ? '#14b8a6' : '#f59e0b';
    const getVolumePercent = (volume) => {
        if (!maxVolume || maxVolume === 0) return 15;
        const percent = (Number(volume) / maxVolume) * 100;
        return Math.max(20, Math.min(100, percent));
    };

    const marketLabel = market === 'spot' ? 'Спот' : 'Фьючерс';

    let html = '<div class="densities-grid">';

    for (const symbol of symbols) {
        const densList = bySymbol[symbol];
        // densList already sorted by volume descending due to earlier sort? We'll sort again to be sure.
        densList.sort((a, b) => Number(b.volume) - Number(a.volume));

        html += `
            <div class="density-card">
                <div class="density-card-header">
                    <div class="density-symbol">${symbol}</div>
                    <div class="density-market-label">${marketLabel}</div>
                </div>
                <div class="density-bars">
        `;

        for (const d of densList) {
            const sideColor = getSideColor(d.side);
            const volumePercent = getVolumePercent(d.volume);
            const exchangeShort = getExchangeShort(d.exchange);
            const volumeFormatted = fmtVolume(d.volume);
            // price not needed per bar, but we could show if wanted
            html += `
                <div class="density-bar-item" style="margin-bottom:8px;">
                    <div class="density-bar-left" style="display:flex;align-items:center;gap:6px;">
                        <div class="density-side-dot" style="width:10px;height:10px;border-radius:50%;background:${sideColor};"></div>
                        <span class="density-exchange">${exchangeShort}</span>
                    </div>
                    <div class="density-bar-bg" style="height:8px;background:#1e293b;border-radius:4px;overflow:hidden;margin-top:4px;">
                        <div class="density-bar-fill" style="width:${volumePercent}%;height:100%;background:linear-gradient(90deg, ${sideColor} 0%, ${sideColor}cc 100%);"></div>
                    </div>
                    <span class="density-volume">$${volumeFormatted}</span>
                </div>
            `;
        }

        html += `
                </div>
            </div>
        `;
    }

    html += '</div>';

    html += `
        <div class="densities-footer">
            <div>
                <span class="footer-highlight">${count}</span> уровней
                <span class="footer-separator">|</span>
                <span>${symbols_count}</span> символов
                <span class="footer-separator">|</span>
                <span style="text-transform:capitalize;">${market}</span>
            </div>
            <div class="footer-update">
                <span>Обновлено:</span>
                <span class="footer-time">${new Date(server_time * 1000).toLocaleTimeString()}</span>
                <div class="footer-pulse"></div>
            </div>
        </div>
    `;

    densitiesOverlayBody.innerHTML = html;
}

// Инициализация при DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
    initDensitiesElements();
});