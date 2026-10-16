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
    const sortedDensities = [];
    for (const symbol of symbols) {
        bySymbol[symbol].sort((a, b) => Number(b.volume) - Number(a.volume));
        sortedDensities.push(...bySymbol[symbol]);
    }

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

    let html = '<div class="densities-grid">';

    for (const d of sortedDensities) {
        const sideColor = getSideColor(d.side);
        const volumePercent = getVolumePercent(d.volume);
        const exchangeShort = getExchangeShort(d.exchange);
        const ageFormatted = fmtAge(d.age_seconds);
        const volumeFormatted = fmtVolume(d.volume);
        const priceFormatted = d.price.toLocaleString();
        const marketType = d.symbol.includes('USDT') ? 'S' : 'F';

        html += `
            <div class="density-card">
                <div class="density-card-top">
                    <div class="density-symbol-block">
                        <span class="density-symbol">${d.symbol}</span>
                        <span class="density-market-type">${marketType}</span>
                    </div>
                    <div class="density-top-icons">
                        <span class="density-icon-text">B</span>
                        <svg class="density-svg-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                            <circle cx="12" cy="12" r="3"/>
                        </svg>
                        <svg class="density-svg-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z"/>
                        </svg>
                        <svg class="density-svg-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <circle cx="12" cy="12" r="3"/>
                            <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z"/>
                        </svg>
                    </div>
                </div>
                
                <div class="density-volume-bar">
                    <div class="density-volume-fill" style="width: ${volumePercent}%; background: linear-gradient(90deg, ${sideColor} 0%, ${sideColor}cc 50%, ${sideColor}66 100%);">
                        <span class="density-volume-text">$${volumeFormatted}</span>
                    </div>
                </div>
                
                <div class="density-card-bottom">
                    <div class="density-bottom-left">
                        <span class="density-side-icon" style="color: ${sideColor};">${d.side === 'buy' ? 'C' : '×'}</span>
                        <span class="density-age">${ageFormatted}</span>
                        <span class="density-price">${priceFormatted}</span>
                        <span class="density-change" style="color: ${sideColor};">${(Math.random() * 4 - 2).toFixed(1)}%</span>
                    </div>
                    <div class="density-bottom-right">
                        <span class="density-exchange-icon">◇</span>
                        <span class="density-exchange-name">${exchangeShort}</span>
                        <span class="density-secondary-value">${(d.price * 0.001).toFixed(4)}</span>
                        <span class="density-secondary-change" style="color: ${sideColor};">${(Math.random() * 2).toFixed(1)}%</span>
                    </div>
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