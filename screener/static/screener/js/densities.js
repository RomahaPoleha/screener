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

    // Сортировка
    const { field, direction } = densitiesSortState;
    densities.sort((a, b) => {
        let valA = a[field];
        let valB = b[field];
        if (field === 'price' || field === 'volume' || field === 'age_seconds') {
            valA = Number(valA);
            valB = Number(valB);
        } else {
            valA = String(valA).toLowerCase();
            valB = String(valB).toLowerCase();
        }
        if (valA < valB) return direction === 'asc' ? -1 : 1;
        if (valA > valB) return direction === 'asc' ? 1 : -1;
        return 0;
    });

    // Группируем по символам
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

    // Находим максимальный объем
    let maxVolume = 0;
    for (const d of densities) {
        if (Number(d.volume) > maxVolume) maxVolume = Number(d.volume);
    }

    const getSideColor = (side) => side === 'buy' ? '#14b8a6' : '#f59e0b';
    
    const getVolumePercent = (volume) => {
        if (!maxVolume || maxVolume === 0) return 15;
        const percent = (Number(volume) / maxVolume) * 100;
        return Math.max(15, Math.min(100, percent));
    };

    let html = '<div class="new-densities-grid">';

    for (const d of sortedDensities) {
        const sideColor = getSideColor(d.side);
        const volumePercent = getVolumePercent(d.volume);
        const exchangeShort = getExchangeShort(d.exchange);
        const ageFormatted = fmtAge(d.age_seconds);
        const volumeFormatted = fmtVolume(d.volume);
        const priceFormatted = d.price.toLocaleString();
        
        html += `
            <div class="new-density-card" style="border-color: ${sideColor}30;">
                <div class="new-density-header">
                    <div class="new-density-symbol">${d.symbol}</div>
                    <div class="new-density-icons">
                        <span style="color:#64748b;font-size:10px;">☆</span>
                        <span style="color:#64748b;font-size:10px;">⚙</span>
                    </div>
                </div>
                
                <div class="new-density-exchange" style="background:${sideColor}20;color:${sideColor};">
                    ${exchangeShort}
                </div>
                
                <div class="new-density-main">
                    <div class="new-density-volume" style="color:#22d3ee;">$${volumeFormatted}</div>
                    
                    <div class="new-density-bar-wrapper">
                        <div class="new-density-bar" style="width:${volumePercent}%;background:${sideColor};"></div>
                    </div>
                </div>
                
                <div class="new-density-footer">
                    <div class="new-density-info">
                        <span style="color:${sideColor};font-weight:700;">${d.side === 'buy' ? '●' : '×'}</span>
                        <span style="color:#64748b;">${ageFormatted}</span>
                        <span style="color:#94a3b8;">${priceFormatted}</span>
                    </div>
                    <div class="new-density-change" style="color:${sideColor};">
                        ${(Math.random() * 4 - 2).toFixed(2)}%
                    </div>
                </div>
            </div>
        `;
    }

    html += '</div>';
    
    html += `
        <div style="padding:12px 16px;border-top:1px solid #1e293b;display:flex;justify-content:space-between;font-size:11px;color:#64748b;">
            <div>
                <span style="color:#22d3ee;font-weight:600;">${count}</span> уровней
                <span style="margin:0 6px;">|</span>
                <span style="color:#94a3b8;">${symbols_count}</span> символов
            </div>
            <div style="display:flex;align-items:center;gap:8px;">
                <span>Обновлено:</span>
                <span style="font-family:monospace;">${new Date(server_time * 1000).toLocaleTimeString()}</span>
            </div>
        </div>
    `;

    // Добавляем стили с !important чтобы переопределить старые
    html += `
        <style>
            .new-densities-grid {
                display: grid !important;
                grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)) !important;
                gap: 10px !important;
                padding: 12px !important;
            }
            .new-density-card {
                background: #0f172a !important;
                border: 1px solid !important;
                border-radius: 8px !important;
                padding: 12px !important;
                transition: all 0.2s !important;
            }
            .new-density-card:hover {
                transform: translateY(-2px) !important;
                box-shadow: 0 4px 12px rgba(0,0,0,0.3) !important;
            }
            .new-density-header {
                display: flex !important;
                justify-content: space-between !important;
                align-items: center !important;
                margin-bottom: 8px !important;
            }
            .new-density-symbol {
                font-size: 13px !important;
                font-weight: 700 !important;
                color: #e2e8f0 !important;
            }
            .new-density-exchange {
                display: inline-block !important;
                padding: 2px 8px !important;
                border-radius: 4px !important;
                font-size: 10px !important;
                font-weight: 700 !important;
                margin-bottom: 10px !important;
            }
            .new-density-main {
                display: flex !important;
                align-items: center !important;
                gap: 10px !important;
                margin-bottom: 10px !important;
            }
            .new-density-volume {
                font-size: 16px !important;
                font-weight: 700 !important;
                min-width: 75px !important;
            }
            .new-density-bar-wrapper {
                flex: 1 !important;
                height: 24px !important;
                background: rgba(30, 41, 59, 0.5) !important;
                border-radius: 4px !important;
                overflow: hidden !important;
                position: relative !important;
            }
            .new-density-bar {
                height: 100% !important;
                border-radius: 4px !important;
                transition: width 0.3s ease !important;
                box-shadow: 0 0 10px currentColor !important;
            }
            .new-density-footer {
                display: flex !important;
                justify-content: space-between !important;
                align-items: center !important;
                font-size: 11px !important;
            }
            .new-density-info {
                display: flex !important;
                align-items: center !important;
                gap: 8px !important;
            }
        </style>
    `;

    densitiesOverlayBody.innerHTML = html;
}

// Инициализация при DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
    initDensitiesElements();
});