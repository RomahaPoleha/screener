// Добавим стили для densities-overlay чтобы оно перекрывало весь chart-area
// Добавим это в файл styles.css

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
        // Закрыть: убрать класс active
        densitiesOverlay.classList.remove('active');
        densitiesBtn.classList.remove('active');
        // Вернуть chart-container в исходное состояние (если нужно)
        const chartContainer = document.getElementById('chart-container');
        if (chartContainer) {
            chartContainer.style.zIndex = 'auto';
        }
    } else {
        // Открыть: добавить класс active и установить z-index для chart-container
        densitiesOverlay.classList.add('active');
        densitiesBtn.classList.add('active');
        const chartContainer = document.getElementById('chart-container');
        if (chartContainer) {
            // Сделать chart-container не видимым, чтобы densities overlay был поверх
            chartContainer.style.zIndex = '99';
        }
        loadDensitiesTable();
    }
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
    
    // Заменить контент chart-container на content="chart" чтобы densities overlay перекрывал его полностью
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
    
    // Вернуть chart-container в нормальное состояние
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

function getSideClass(side) {
    return side === 'buy' ? 'densities-buy' : 'densities-sell';
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

function getSideColor(side) {
    return side === 'buy' ? '#14b8a6' : '#f59e0b';
}

function getSideBgGradient(side, volume) {
    const intensity = Math.min(volume / 100000, 1);
    if (side === 'buy') {
        return `linear-gradient(135deg, rgba(20, 184, 166, ${0.1 + intensity * 0.3}) 0%, rgba(20, 184, 166, ${0.05 + intensity * 0.15}) 100%)`;
    } else {
        return `linear-gradient(135deg, rgba(245, 158, 11, ${0.1 + intensity * 0.3}) 0%, rgba(245, 158, 11, ${0.05 + intensity * 0.15}) 100%)`;
    }
}

function renderDensitiesTable(data) {
    if (!densitiesOverlayBody) return;

    const { count, symbols_count, market, server_time } = data;
    let densities = data.densities || [];

    if (!densities || densities.length === 0) {
        densitiesOverlayBody.innerHTML = `
            <div style="color:#6b7280; text-align:center; padding:40px; font-size:14px;">
                <div style="font-size:48px; margin-bottom:16px;">📊</div>
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

    // Цвет для buy/sell
    const getSideColor = (side) => {
        return side === 'buy' ? '#14b8a6' : '#f59e0b';
    };

    const getSideBgGradient = (side, volume) => {
        const intensity = Math.min(volume / 100000, 1);
        if (side === 'buy') {
            return `linear-gradient(135deg, rgba(20, 184, 166, ${0.1 + intensity * 0.3}) 0%, rgba(20, 184, 166, ${0.05 + intensity * 0.15}) 100%)`;
        } else {
            return `linear-gradient(135deg, rgba(245, 158, 11, ${0.1 + intensity * 0.3}) 0%, rgba(245, 158, 11, ${0.05 + intensity * 0.15}) 100%)`;
        }
    };

    let html = `
        <style>
            .densities-grid {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
                gap: 12px;
                padding: 16px;
            }
            .density-card {
                background: #0f172a;
                border: 1px solid #1e293b;
                border-radius: 12px;
                padding: 14px;
                position: relative;
                overflow: hidden;
                transition: all 0.2s;
                cursor: pointer;
            }
            .density-card:hover {
                border-color: #334155;
                transform: translateY(-2px);
                box-shadow: 0 8px 24px rgba(0,0,0,0.4);
            }
            .density-card::before {
                content: '';
                position: absolute;
                top: 0;
                left: 0;
                right: 0;
                height: 3px;
                background: ${side} => getSideColor(side);
            }
            .density-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 12px;
            }
            .density-symbol {
                font-size: 14px;
                font-weight: 700;
                color: #e2e8f0;
                letter-spacing: 0.5px;
            }
            .density-icons {
                display: flex;
                gap: 8px;
                opacity: 0.6;
            }
            .density-icon {
                width: 16px;
                height: 16px;
                cursor: pointer;
                transition: opacity 0.2s;
            }
            .density-icon:hover {
                opacity: 1;
            }
            .density-main {
                display: flex;
                gap: 12px;
                align-items: flex-end;
                margin-bottom: 12px;
            }
            .density-volume {
                font-size: 20px;
                font-weight: 700;
                color: #22d3ee;
            }
            .density-price {
                font-size: 13px;
                color: #94a3b8;
                margin-bottom: 2px;
            }
            .density-exchange {
                display: inline-block;
                padding: 2px 8px;
                background: rgba(51, 65, 85, 0.5);
                border-radius: 4px;
                font-size: 11px;
                font-weight: 600;
                color: #cbd5e1;
                margin-bottom: 8px;
            }
            .density-footer {
                display: flex;
                justify-content: space-between;
                align-items: center;
                font-size: 12px;
                color: #64748b;
            }
            .density-age {
                display: flex;
                align-items: center;
                gap: 4px;
            }
            .density-side-indicator {
                width: 8px;
                height: 8px;
                border-radius: 50%;
                display: inline-block;
            }
            .density-gradient-bar {
                position: absolute;
                bottom: 0;
                left: 0;
                right: 0;
                height: 4px;
                opacity: 0.6;
            }
        </style>
        <div class="densities-grid">
    `;

    for (const symbol of symbols) {
        const items = bySymbol[symbol];
        for (const d of items) {
            const sideColor = getSideColor(d.side);
            const bgGradient = getSideBgGradient(d.side, d.volume);
            const exchangeShort = getExchangeShort(d.exchange);
            
            html += `
                <div class="density-card" style="background: ${bgGradient};">
                    <div style="position: absolute; top: 0; left: 0; right: 0; height: 3px; background: ${sideColor};"></div>
                    
                    <div class="density-header">
                        <div class="density-symbol">${symbol}</div>
                        <div class="density-icons">
                            <svg class="density-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z"/>
                            </svg>
                            <svg class="density-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <circle cx="12" cy="12" r="3"/>
                                <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010 2.83l.06.06a1.65 1.65 0 00.33 1.82 1.65 1.65 0 00-1 1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 2 0 012 2v.09a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 2 01-2 2h-.09a1.65 1.65 0 00-1.51 1z"/>
                            </svg>
                        </div>
                    </div>
                    
                    <div class="density-exchange">${exchangeShort}</div>
                    
                    <div class="density-main">
                        <div>
                            <div class="density-volume">$${fmtVolume(d.volume)}</div>
                            <div class="density-price" style="color: ${sideColor}; font-weight: 600;">
                                ${d.price.toLocaleString()}
                            </div>
                        </div>
                    </div>
                    
                    <div class="density-footer">
                        <div class="density-age">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <circle cx="12" cy="12" r="10"/>
                                <polyline points="12 6 12 12 16 14"/>
                            </svg>
                            ${fmtAge(d.age_seconds)}
                        </div>
                        <div style="display: flex; align-items: center; gap: 6px;">
                            <span class="density-side-indicator" style="background: ${sideColor};"></span>
                            <span style="text-transform: capitalize; color: ${sideColor};">${d.side}</span>
                        </div>
                    </div>
                    
                    <div class="density-gradient-bar" style="background: linear-gradient(90deg, ${sideColor}40 0%, ${sideColor}10 100%);"></div>
                </div>
            `;
        }
    }

    html += `</div>`;

    html += `
        <div style="padding: 16px; border-top: 1px solid #1e293b; display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: #64748b;">
            <div>
                <span style="color: #22d3ee; font-weight: 600;">${count}</span> уровней 
                <span style="margin: 0 8px;">|</span>
                <span style="color: #94a3b8;">${symbols_count}</span> символов
                <span style="margin: 0 8px;">|</span>
                <span style="text-transform: capitalize;">${market}</span>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
                <span>Обновлено:</span>
                <span style="color: #94a3b8; font-family: monospace;">${new Date(server_time * 1000).toLocaleTimeString()}</span>
                <div style="width: 8px; height: 8px; background: #22d3ee; border-radius: 50%; animation: pulse 2s infinite;"></div>
            </div>
        </div>
        <style>
            @keyframes pulse {
                0%, 100% { opacity: 1; }
                50% { opacity: 0.5; }
            }
        </style>
    `;

    densitiesOverlayBody.innerHTML = html;
}

document.addEventListener('DOMContentLoaded', () => {
    initDensitiesElements();
});