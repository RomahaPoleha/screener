// ==========================================
// densities.js — ТАБЛИЦА ПЛОТНОСТЕЙ (группировка по символу+бирже)
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

// State for the new grouped-card view
const state = {
  densities: [],
  sortField: 'volume',
  sortDirection: 'desc',
  sideFilter: 'all',
  searchQuery: '',
  isLive: true,
  serverTime: Date.now(),
  intervals: []
};

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
    loadDensitiesTable();
}

function closeDensitiesOverlay() {
    if (!densitiesOverlay) initDensitiesElements();
    densitiesOverlay.classList.remove('active');
    densitiesBtn.classList.remove('active');
}

// ==========================================
// UTILITY FUNCTIONS
// ==========================================
function fmtVolume(vol) {
    if (vol >= 1e9) return (vol / 1e9).toFixed(2) + 'B';
    if (vol >= 1e6) return (vol / 1e6).toFixed(2) + 'M';
    if (vol >= 1e3) return (vol / 1e3).toFixed(1) + 'K';
    return vol.toFixed(0);
}

function fmtAge(seconds) {
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    return `${Math.floor(seconds / 3600)}h`;
}

function getExchangeShort(exchange) {
    const map = {
        'BINANCE': 'BIN', 'BYBIT': 'BYB', 'OKX': 'OKX',
        'BITFINEX': 'BIT', 'HTX': 'HTX', 'KUCOIN': 'KCN',
        'binance': 'BIN', 'bybit': 'BYB', 'okx': 'OKX',
        'gate': 'GT', 'mexc': 'MEX', 'bitget': 'BGB',
        'binance_alpha': 'BI-A'
    };
    return map[exchange] || exchange.substring(0, 3).toUpperCase();
}

function getVolumeTier(volume) {
    if (volume >= 2000000) return 'high';
    if (volume >= 500000) return 'medium';
    return 'low';
}

// ==========================================
// API LOADING
// ==========================================
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

        state.densities = data.densities || [];
        state.serverTime = data.server_time || Date.now();
        render();
    } catch (err) {
        console.error('loadDensitiesTable error:', err);
        densitiesOverlayBody.innerHTML = `<div style="color:#ef4444; text-align:center; padding:20px;">Ошибка: ${err.message}</div>`;
    }
}

// ==========================================
// GROUPING LOGIC (symbol + exchange)
// ==========================================
function groupBySymbolAndExchange(densities) {
    const groups = {};
    for (const d of densities) {
        const key = `${d.symbol}_${d.exchange}`;
        if (!groups[key]) groups[key] = [];
        groups[key].push(d);
    }

    const result = [];
    for (const key of Object.keys(groups)) {
        const [symbol, exchange] = key.split('_');
        const items = groups[key].sort((a, b) => b.volume - a.volume);
        const totalVolume = items.reduce((sum, d) => sum + d.volume, 0);
        const buyCount = items.filter(d => d.side === 'buy').length;
        const sellCount = items.filter(d => d.side === 'sell').length;

        result.push({
            symbol,
            exchange,
            exchangeShort: getExchangeShort(exchange),
            densities: items,
            totalVolume,
            count: items.length,
            buyCount,
            sellCount
        });
    }

    result.sort((a, b) => b.totalVolume - a.totalVolume);
    return result;
}

// ==========================================
// RENDER FUNCTIONS
// ==========================================
function renderGroupCard(group, index, globalMaxVolume) {
    const rows = group.densities.map((d, rowIdx) => {
        const volumePercent = globalMaxVolume > 0
            ? Math.max(12, Math.min(100, (d.volume / globalMaxVolume) * 100))
            : 12;
        const sideColor = d.side === 'buy' ? '#14b8a6' : '#f59e0b';
        const tier = getVolumeTier(d.volume);
        const isWhale = tier === 'high';

        return `
            <div class="group-density-row ${d.side}-row ${isWhale ? 'whale-row' : ''}" style="animation-delay: ${index * 40 + rowIdx * 20}ms">
                <div class="row-side-icon ${d.side}">${d.side === 'buy' ? 'B' : 'S'}</div>
                <div class="row-volume">$${fmtVolume(d.volume)}</div>
                <div class="row-bar-container">
                    <div class="row-bar-fill animate-bar-fill" style="width: ${volumePercent}%; background: linear-gradient(90deg, ${sideColor} 0%, ${sideColor}bb 60%, ${sideColor}33 100%); animation-delay: ${index * 40 + rowIdx * 20 + 100}ms;"></div>
                </div>
                <div class="row-meta">
                    ${isWhale ? '<span class="whale-tag">⚡</span>' : ''}
                    <span class="row-price">$${d.price.toLocaleString()}</span>
                    <span class="row-age">${fmtAge(d.age_seconds)}</span>
                </div>
            </div>
        `;
    }).join('');

    let sideDots = '';
    for (let i = 0; i < group.buyCount; i++) {
        sideDots += '<div class="group-side-dot buy"></div>';
    }
    for (let i = 0; i < group.sellCount; i++) {
        sideDots += '<div class="group-side-dot sell"></div>';
    }

    return `
        <div class="group-card animate-fade-in" style="animation-delay: ${index * 50}ms">
            <div class="group-card-header">
                <div class="group-symbol-row">
                    <span class="group-symbol">${group.symbol}</span>
                    <span class="group-exchange-badge">${group.exchangeShort}</span>
                    <span class="group-count-badge">${group.count} lvl</span>
                    <div class="group-side-indicator">${sideDots}</div>
                </div>
                <div class="group-total-volume">$${fmtVolume(group.totalVolume)}</div>
            </div>
            <div class="group-densities">
                ${rows}
            </div>
        </div>
    `;
}

function renderStatsFooter(densities, serverTime) {
    const totalVolume = densities.reduce((sum, d) => sum + d.volume, 0);
    const buyVolume = densities.filter(d => d.side === 'buy').reduce((sum, d) => sum + d.volume, 0);
    const uniqueSymbols = new Set(densities.map(d => d.symbol)).size;
    const buyRatio = totalVolume > 0 ? ((buyVolume / totalVolume) * 100).toFixed(1) : '0';

    return `
        <div class="stats-footer">
            <div style="display:flex; align-items:center; gap:16px; flex-wrap:wrap;">
                <div>
                    <span class="stat-value">${densities.length}</span>
                    <span class="stat-label">уровней</span>
                </div>
                <div>
                    <span class="stat-value">${uniqueSymbols}</span>
                    <span class="stat-label">монет</span>
                </div>
                <div>
                    <span class="stat-value">$${fmtVolume(totalVolume)}</span>
                    <span class="stat-label">объём</span>
                </div>
                <div style="display:flex; align-items:center; gap:6px;">
                    <div style="width:60px; height:4px; border-radius:2px; background:rgba(30,41,59,0.5); overflow:hidden;">
                        <div style="width:${buyRatio}%; height:100%; background:linear-gradient(90deg, #14b8a6, #22d3ee); border-radius:2px; transition:width 0.5s;"></div>
                    </div>
                    <span style="font-size:10px; color:#14b8a6; font-family:'JetBrains Mono',monospace;">${buyRatio}%</span>
                    <span style="font-size:9px; color:#64748b;">BUY</span>
                </div>
            </div>
            <div class="live-indicator">
                <span style="color:#94a3b8; font-family:'JetBrains Mono',monospace; font-size:11px;">
                    ${new Date(serverTime).toLocaleTimeString()}
                </span>
                <div class="live-dot"></div>
                <span style="color:#22d3ee; font-size:10px; font-weight:600;">LIVE</span>
            </div>
        </div>
    `;
}

function getProcessedDensities() {
    let result = [...state.densities];

    if (state.sideFilter !== 'all') {
        result = result.filter(d => d.side === state.sideFilter);
    }

    if (state.searchQuery) {
        const q = state.searchQuery.toUpperCase();
        result = result.filter(d => d.symbol.includes(q));
    }

    return result;
}

function getMaxVolume() {
    return Math.max(...state.densities.map(d => d.volume), 1);
}

// ==========================================
// MAIN RENDER
// ==========================================
function render() {
    const grid = document.getElementById('densities-grid');
    const statsContainer = document.getElementById('stats-container');

    if (!densitiesOverlayBody) return;

    const processed = getProcessedDensities();
    const globalMaxVolume = getMaxVolume();
    const groups = groupBySymbolAndExchange(processed);
    const uniqueSymbols = new Set(state.densities.map(d => d.symbol)).size;

    // Build header with filters
    let headerHtml = `
        <div class="dashboard-header">
            <div class="header-title">
                <span class="icon">📊</span>
                <span>Таблица плотностей</span>
            </div>
            <div class="header-subtitle">
                Real-time liquidity density monitor • ${state.densities.length} levels across ${uniqueSymbols} coins
            </div>
        </div>
        <div class="filter-bar">
            <div style="display:flex; align-items:center; gap:8px; flex:1; min-width:200px;">
                <label style="font-size:10px; color:#94a3b8; font-family:'JetBrains Mono',monospace; margin-right:6px;">Поиск:</label>
                <input type="text" id="search-input" class="search-input" placeholder="BTC, ETH..." value="${state.searchQuery}">
            </div>
            <div style="display:flex; align-items:center; gap:6px;">
                <button class="filter-btn ${state.sideFilter === 'all' ? 'active' : ''}" data-filter="all" onclick="handleSideFilter('all')">Все</button>
                <button class="filter-btn ${state.sideFilter === 'buy' ? 'active-buy' : ''}" data-filter="buy" onclick="handleSideFilter('buy')">BUY</button>
                <button class="filter-btn ${state.sideFilter === 'sell' ? 'active-sell' : ''}" data-filter="sell" onclick="handleSideFilter('sell')">SELL</button>
                <button class="live-btn ${state.isLive ? 'active' : ''}" onclick="toggleLive()">
                    <div class="live-btn-dot ${state.isLive ? '' : 'paused'}"></div>
                    ${state.isLive ? 'LIVE' : 'PAUSED'}
                </button>
            </div>
        </div>
    `;

    // Render grouped grid
    let gridHtml = '';
    if (groups.length === 0) {
        gridHtml = `
            <div style="grid-column: 1/-1; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:60px 20px; color:#6b7280;">
                <div style="font-size:48px; margin-bottom:16px; opacity:0.5;">📊</div>
                <div style="font-size:14px; font-weight:500;">Плотностей не найдено</div>
                <div style="margin-top:8px; font-size:12px; opacity:0.7;">Попробуйте изменить фильтры</div>
            </div>
        `;
    } else {
        gridHtml = groups.map((group, index) =>
            renderGroupCard(group, index, globalMaxVolume)
        ).join('');
    }

    densitiesOverlayBody.innerHTML = headerHtml +
        `<div id="densities-grid" class="densities-grid">${gridHtml}</div>` +
        `<div id="stats-container">${renderStatsFooter(processed, state.serverTime)}</div>`;

    // Bind search input
    const searchInput = document.getElementById('search-input');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => handleSearch(e.target.value));
    }
}

// ==========================================
// EVENT HANDLERS
// ==========================================
function handleSort(field) {
    if (state.sortField === field) {
        state.sortDirection = state.sortDirection === 'asc' ? 'desc' : 'asc';
    } else {
        state.sortField = field;
        state.sortDirection = 'desc';
    }
    render();
}

function handleSideFilter(filter) {
    state.sideFilter = filter;
    render();
}

function handleSearch(query) {
    state.searchQuery = query;
    render();
}

function toggleLive() {
    state.isLive = !state.isLive;
    render();
}

// ==========================================
// LIVE UPDATE LOOP
// ==========================================
function startLiveUpdates() {
    const interval = setInterval(() => {
        if (!state.isLive) return;

        // Update ages
        state.densities.forEach(d => { d.age_seconds += 3; });

        // Randomly replace some densities
        const replaceCount = Math.floor(Math.random() * 3) + 1;
        for (let i = 0; i < replaceCount; i++) {
            if (state.densities.length > 0) {
                const idx = Math.floor(Math.random() * state.densities.length);
                const d = state.densities[idx];
                state.densities[idx] = {
                    ...d,
                    age_seconds: Math.floor(Math.random() * 300),
                    volume: d.volume * (0.8 + Math.random() * 0.4),
                    timestamp: Date.now()
                };
            }
        }

        state.serverTime = Date.now();
        render();
    }, 3000);

    state.intervals.push(interval);
}

// ==========================================
// INITIALIZATION
// ==========================================
function initApp() {
    state.serverTime = Date.now();
    render();
    startLiveUpdates();
}

document.addEventListener('DOMContentLoaded', () => {
    initDensitiesElements();
    initApp();
});
