// ==========================================
// densities.js — ТАБЛИЦА ПЛОТНОСТЕЙ (полноэкранное окно, без мерцания)
// Загрузка и отображение плотностей из мастер-листа
// ==========================================

let densitiesOverlay = null;
let densitiesOverlayBody = null;
let densitiesBtn = null;
let densitiesMarketSelect = null;
let densitiesMinVolume = null;
let densitiesLimit = null;

const state = {
  densities: [],
  sortField: 'volume',
  sortDirection: 'desc',
  sideFilter: 'all',
  searchQuery: '',
  isLive: true,
  serverTime: Date.now(),
  intervals: [],
  globalMaxVolume: 1,
  rowMap: new Map()  // id -> {rowEl, volEl, ageEl, barEl}
};

function initDensitiesElements() {
    densitiesBtn = document.getElementById('densitiesBtn');
    densitiesMarketSelect = document.getElementById('densitiesMarketSelect');
    densitiesMinVolume = document.getElementById('densitiesMinVolume');
    densitiesLimit = document.getElementById('densitiesLimit');
}

function createFullscreenOverlay() {
    const existing = document.getElementById('densitiesFullscreenOverlay');
    if (existing) {
        densitiesOverlay = existing;
        densitiesOverlayBody = document.getElementById('densitiesFullscreenBody');
        return;
    }

    const overlay = document.createElement('div');
    overlay.id = 'densitiesFullscreenOverlay';
    overlay.className = 'densities-fullscreen-overlay';
    overlay.innerHTML = `
        <div class="densities-fullscreen-header">
            <button class="densities-close-btn" onclick="closeDensitiesOverlay()">✕ Закрыть</button>
            <div class="densities-fullscreen-title">Таблица плотностей</div>
        </div>
        <div class="densities-fullscreen-body" id="densitiesFullscreenBody"></div>
    `;
    document.body.appendChild(overlay);
    densitiesOverlay = overlay;
    densitiesOverlayBody = document.getElementById('densitiesFullscreenBody');

    // Delegated search input listener — added once, lives for overlay lifetime
    densitiesOverlayBody.addEventListener('input', (e) => {
        if (e.target && e.target.id === 'search-input') {
            handleSearch(e.target.value);
        }
    });
}

function toggleDensitiesOverlay() {
    if (!densitiesOverlay || !densitiesOverlay.classList.contains('active')) {
        openDensitiesOverlay();
    } else {
        closeDensitiesOverlay();
    }
    if (densitiesBtn) {
        densitiesBtn.classList.toggle('active', densitiesOverlay.classList.contains('active'));
    }
}

function openDensitiesOverlay() {
    createFullscreenOverlay();
    densitiesOverlay.classList.add('active');
    document.body.style.overflow = 'hidden';
    renderShell();
    loadDensitiesTable();
}

function closeDensitiesOverlay() {
    if (densitiesOverlay) {
        densitiesOverlay.classList.remove('active');
        document.body.style.overflow = '';
    }
    if (densitiesBtn) {
        densitiesBtn.classList.remove('active');
    }
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
// SHELL (статическая оболочка — заголовок + фильтры)
// ==========================================
function renderShell() {
    if (!densitiesOverlayBody) return;

    const uniqueSymbols = new Set(state.densities.map(d => d.symbol)).size;

    densitiesOverlayBody.innerHTML = `
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
        <div id="densities-grid" class="densities-grid"></div>
        <div id="stats-container"></div>
    `;
}

// ==========================================
// CONTENT (динамическое содержимое — сетка + статистика)
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
            <div class="group-density-row ${d.side}-row ${isWhale ? 'whale-row' : ''}"
                 data-id="${d.id}"
                 data-age="${d.age_seconds}"
                 data-volume="${d.volume}"
                 style="animation-delay: ${index * 40 + rowIdx * 20}ms">
                <div class="row-side-icon ${d.side}">${d.side === 'buy' ? 'B' : 'S'}</div>
                <div class="row-volume">$${fmtVolume(d.volume)}</div>
                <div class="row-bar-container">
                    <div class="row-bar-fill" style="width: ${volumePercent}%; background: linear-gradient(90deg, ${sideColor} 0%, ${sideColor}bb 60%, ${sideColor}33 100%);"></div>
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
                        <div id="buy-ratio-bar" style="width:${buyRatio}%; height:100%; background:linear-gradient(90deg, #14b8a6, #22d3ee); border-radius:2px; transition:width 0.5s;"></div>
                    </div>
                    <span id="buy-ratio-text" style="font-size:10px; color:#14b8a6; font-family:'JetBrains Mono',monospace;">${buyRatio}%</span>
                    <span style="font-size:9px; color:#64748b;">BUY</span>
                </div>
            </div>
            <div class="live-indicator">
                <span id="live-time" style="color:#94a3b8; font-family:'JetBrains Mono',monospace; font-size:11px;">
                    ${new Date(serverTime).toLocaleTimeString()}
                </span>
                <div class="live-dot"></div>
                <span style="color:#22d3ee; font-size:10px; font-weight:600;">LIVE</span>
            </div>
        </div>
    `;
}

function renderContent() {
    const grid = document.getElementById('densities-grid');
    const statsContainer = document.getElementById('stats-container');
    if (!grid || !statsContainer) return;

    const processed = getProcessedDensities();
    const groups = groupBySymbolAndExchange(processed);
    state.globalMaxVolume = getMaxVolume();

    if (groups.length === 0) {
        grid.innerHTML = `
            <div style="grid-column: 1/-1; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:60px 20px; color:#6b7280;">
                <div style="font-size:48px; margin-bottom:16px; opacity:0.5;">📊</div>
                <div style="font-size:14px; font-weight:500;">Плотностей не найдено</div>
                <div style="margin-top:8px; font-size:12px; opacity:0.7;">Попробуйте изменить фильтры</div>
            </div>
        `;
        state.rowMap.clear();
    } else {
        grid.innerHTML = groups.map((group, index) =>
            renderGroupCard(group, index, state.globalMaxVolume)
        ).join('');

        // Собираем ссылки на DOM-элементы строк для быстрого in-place обновления
        state.rowMap.clear();
        const allRows = grid.querySelectorAll('.group-density-row[data-id]');
        allRows.forEach(row => {
            const id = row.dataset.id;
            state.rowMap.set(id, {
                rowEl: row,
                volEl: row.querySelector('.row-volume'),
                ageEl: row.querySelector('.row-age'),
                barEl: row.querySelector('.row-bar-fill')
            });
        });
    }

    statsContainer.innerHTML = renderStatsFooter(processed, state.serverTime);
}

// ==========================================
// LIVE DOM UPDATE (in-place, без innerHTML — нет мерцания)
// ==========================================
function updateLiveDOM() {
    if (!densitiesOverlayBody) return;

    // Обновляем возраст и объём в данных
    state.densities.forEach(d => { d.age_seconds += 3; });
    state.serverTime = Date.now();

    // In-place обновление через кэш ссылок — без поиска по DOM
    state.densities.forEach(d => {
        const entry = state.rowMap.get(d.id);
        if (!entry) return;

        let age = d.age_seconds;
        if (entry.ageEl) entry.ageEl.textContent = fmtAge(age);

        let vol = d.volume;
        if (entry.volEl) entry.volEl.textContent = '$' + fmtVolume(vol);

        const barEl = entry.barEl;
        if (barEl && state.globalMaxVolume > 0) {
            const pct = Math.max(12, Math.min(100, (vol / state.globalMaxVolume) * 100));
            barEl.style.width = pct + '%';
        }
    });

    // Update live time in stats footer
    const timeEl = document.getElementById('live-time');
    if (timeEl) {
        timeEl.textContent = new Date(state.serverTime).toLocaleTimeString();
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
// API LOADING
// ==========================================
async function loadDensitiesTable() {
    if (!densitiesOverlayBody) initDensitiesElements();

    const market = densitiesMarketSelect?.value || 'futures';
    const minVolume = parseInt(densitiesMinVolume?.value) || 10000;
    const limit = parseInt(densitiesLimit?.value) || 20;

    const grid = document.getElementById('densities-grid');
    if (grid) grid.innerHTML = '<div style="grid-column: 1/-1; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:20px; color:#6b7280;">Загрузка...</div>';

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
        renderContent();
    } catch (err) {
        console.error('loadDensitiesTable error:', err);
        densitiesOverlayBody.innerHTML = `<div style="color:#ef4444; text-align:center; padding:20px;">Ошибка: ${err.message}</div>`;
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
    renderContent();
}

function handleSideFilter(filter) {
    state.sideFilter = filter;
    renderShell(); // filter bar state changed
    renderContent();
}

function handleSearch(query) {
    state.searchQuery = query;
    renderContent();
}

function toggleLive() {
    state.isLive = !state.isLive;
    renderShell(); // live button state changed
}

// ==========================================
// LIVE UPDATE LOOP (только in-place обновления, нет innerHTML)
// ==========================================
function startLiveUpdates() {
    const interval = setInterval(() => {
        if (!state.isLive) return;

        // Обновляем возраст в данных
        state.densities.forEach(d => { d.age_seconds += 3; });
        state.serverTime = Date.now();

        // In-place DOM обновление — без мерцания
        updateLiveDOM();
    }, 5000);

    state.intervals.push(interval);
}

// ==========================================
// INITIALIZATION
// ==========================================
function initApp() {
    state.serverTime = Date.now();
    startLiveUpdates();
}

document.addEventListener('DOMContentLoaded', () => {
    initDensitiesElements();
    initApp();
});
