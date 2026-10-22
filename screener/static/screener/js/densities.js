// ==========================================
// densities.js — КАРТА ПЛОТНОСТЕЙ (полноэкранное окно, без мерцания)
// Загрузка и отображение плотностей из мастер-листа
// ==========================================

// Конфигурация бирж (совпадает с scalp.js)
const EXCHANGES_CONFIG = [
    { id: 'binance', name: 'Binance', label: 'BI', domain: 'binance.com', color: '#f59e0b', minVolFutures: 40000, minVolSpot: 20000 },
    { id: 'bybit', name: 'Bybit', label: 'BY', domain: 'bybit.com', color: '#f59e0b', minVolFutures: 30000, minVolSpot: 20000 },
    { id: 'okx', name: 'OKX', label: 'OKX', domain: 'okx.com', color: '#ffffff', minVolFutures: 30000, minVolSpot: 20000 },
    { id: 'gate', name: 'Gate.io', label: 'GT', domain: 'gate.io', color: '#f59e0b', minVolFutures: 20000, minVolSpot: 15000 },
    { id: 'mexc', name: 'MEXC', label: 'MEX', domain: 'mexc.com', color: '#f59e0b', minVolFutures: 20000, minVolSpot: 15000 },
    { id: 'bitget', name: 'Bitget', label: 'BGB', domain: 'bitget.com', color: '#f59e0b', minVolFutures: 25000, minVolSpot: 15000 },
    { id: 'binance_alpha', name: 'Binance Alpha', label: 'BI-A', domain: 'binance.com', color: '#8b5cf6', minVolFutures: 10000, minVolSpot: 5000 },
];

let densitiesOverlay = null;
let densitiesOverlayBody = null;
let densitiesBtn = null;
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
    rowMap: new Map(),
    // Exchange filters: { exchangeId: { futures: bool, spot: bool } }
    exchangeFilters: {},
    // Exchange min volumes: { exchangeId: { futures: number, spot: number } }
    exchangeMinVolumes: {},
    // Sort by NATR 1m
    sortByNatr1m: false
};

function initDensitiesElements() {
    densitiesBtn = document.getElementById('densitiesBtn');
}

// Load exchange filters from localStorage
function loadExchangeFilters() {
    try {
        const saved = localStorage.getItem('densitiesExchangeFilters');
        if (saved) {
            const parsed = JSON.parse(saved);
            state.exchangeFilters = parsed.filters || {};
            state.exchangeMinVolumes = parsed.minVolumes || {};
            state.sortByNatr1m = parsed.sortByNatr1m || false;
        }
    } catch (e) {
        console.warn('Failed to load exchange filters:', e);
    }
    // Default: all enabled with configured min volumes
    EXCHANGES_CONFIG.forEach(ex => {
        if (!state.exchangeFilters[ex.id]) {
            state.exchangeFilters[ex.id] = { futures: true, spot: true };
        }
        if (!state.exchangeMinVolumes[ex.id]) {
            state.exchangeMinVolumes[ex.id] = { futures: ex.minVolFutures, spot: ex.minVolSpot };
        }
    });
}

// Save exchange filters to localStorage
function saveExchangeFilters() {
    try {
        localStorage.setItem('densitiesExchangeFilters', JSON.stringify({
            filters: state.exchangeFilters,
            minVolumes: state.exchangeMinVolumes,
            sortByNatr1m: state.sortByNatr1m
        }));
    } catch (e) {
        console.warn('Failed to save exchange filters:', e);
    }
}

// Render exchange toggles HTML
function renderExchangeToggles() {
    return EXCHANGES_CONFIG.map(ex => {
        const cfg = state.exchangeFilters[ex.id] || { futures: true, spot: true };
        const volCfg = state.exchangeMinVolumes[ex.id] || { futures: ex.minVolFutures, spot: ex.minVolSpot };
        const isAlpha = ex.id === 'binance_alpha';
        const futuresChecked = cfg.futures ? 'checked' : '';
        const spotChecked = cfg.spot ? 'checked' : '';
        const futuresDisabled = isAlpha ? 'disabled' : '';
        const futuresMinVol = volCfg.futures || ex.minVolFutures;
        const spotMinVol = volCfg.spot || ex.minVolSpot;
        
        return `
            <label class="exchange-toggle" title="${ex.name}" style="display:flex; align-items:center; gap:4px; padding:2px 6px; border-radius:4px; background:rgba(30,41,59,0.3); border:1px solid var(--border-primary); cursor:pointer; transition:all 0.2s;">
                <img src="https://www.google.com/s2/favicons?domain=${ex.domain}&sz=16" onerror="this.style.display='none'" style="width:14px;height:14px;border-radius:2px;flex-shrink:0;">
                <span style="font-size:9px; font-weight:600; color:var(--text-primary); font-family:'JetBrains Mono',monospace; min-width:18px; text-align:center;">${ex.label}</span>
                ${!isAlpha ? `
                <input type="checkbox" id="exFilter-${ex.id}-futures" ${futuresChecked} ${futuresDisabled} onchange="handleExchangeFilter('${ex.id}', 'futures', this.checked)" style="accent-color:#f59e0b;width:14px;height:14px;cursor:pointer;">
                <span style="font-size:8px; color:#f59e0b; font-weight:600;">F</span>
                <input type="number" id="exMinVol-${ex.id}-futures" value="${futuresMinVol}" min="1000" step="1000" onchange="handleMinVolChange('${ex.id}', 'futures', this.value)" style="width:60px;font-size:10px;font-family:'JetBrains Mono',monospace;background:rgba(30,41,59,0.5);border:1px solid var(--border-primary);color:var(--text-primary);padding:2px 4px;border-radius:3px;" ${futuresDisabled}>
                ` : ''}
                <input type="checkbox" id="exFilter-${ex.id}-spot" ${spotChecked} onchange="handleExchangeFilter('${ex.id}', 'spot', this.checked)" style="accent-color:#00C6FF;width:14px;height:14px;cursor:pointer;">
                <span style="font-size:8px; color:#00C6FF; font-weight:600;">S</span>
                <input type="number" id="exMinVol-${ex.id}-spot" value="${spotMinVol}" min="1000" step="1000" onchange="handleMinVolChange('${ex.id}', 'spot', this.value)" style="width:60px;font-size:10px;font-family:'JetBrains Mono',monospace;background:rgba(30,41,59,0.5);border:1px solid var(--border-primary);color:var(--text-primary);padding:2px 4px;border-radius:3px;">
            </label>
        `;
    }).join('');
}

// Handle min volume change
function handleMinVolChange(exchangeId, market, value) {
    const numValue = parseInt(value) || 0;
    if (!state.exchangeMinVolumes[exchangeId]) {
        state.exchangeMinVolumes[exchangeId] = { futures: 0, spot: 0 };
    }
    state.exchangeMinVolumes[exchangeId][market] = numValue;
    saveExchangeFilters();
    // Don't re-render content, just reload data
    loadDensitiesTable();
}

// Get exchange domain for favicon
function getExchangeDomain(exchangeId) {
    const ex = EXCHANGES_CONFIG.find(e => e.id === exchangeId);
    return ex ? ex.domain : '';
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
            <div class="densities-fullscreen-title">Карта плотностей</div>
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
    loadExchangeFilters(); // Load saved filters
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
                <span class="icon">🗺️</span>
                <span>Карта плотностей</span>
            </div>
            <div class="header-subtitle">
                Real-time liquidity density map • ${state.densities.length} levels across ${uniqueSymbols} coins
            </div>
        </div>
        <div class="filter-bar">
            <div style="display:flex; align-items:center; gap:8px; flex:1; min-width:200px;">
                <label style="font-size:10px; color:#94a3b8; font-family:'JetBrains Mono',monospace; margin-left:8px; margin-right:6px;">Лимит:</label>
                <input type="number" id="densitiesLimit" value="20" min="1" max="50" step="1" style="width:60px; font-size:11px; font-family:'JetBrains Mono',monospace; background:rgba(30,41,59,0.3); border:1px solid var(--border-primary); color:var(--text-primary); padding:4px 8px; border-radius:4px;">
                <label style="font-size:10px; color:#94a3b8; font-family:'JetBrains Mono',monospace; margin-left:8px; margin-right:6px;">Поиск:</label>
                <input type="text" id="search-input" class="search-input" placeholder="BTC, ETH..." value="${state.searchQuery}">
            </div>
            <div style="display:flex; align-items:center; gap:6px;">
                <button class="filter-btn ${state.sideFilter === 'all' ? 'active' : ''}" data-filter="all" onclick="handleSideFilter('all')">Все</button>
                <button class="filter-btn ${state.sideFilter === 'buy' ? 'active-buy' : ''}" data-filter="buy" onclick="handleSideFilter('buy')">BUY</button>
                <button class="filter-btn ${state.sideFilter === 'sell' ? 'active-sell' : ''}" data-filter="sell" onclick="handleSideFilter('sell')">SELL</button>
                <label style="display:flex; align-items:center; gap:4px; cursor:pointer; font-size:11px; color:var(--text-secondary);">
                    <input type="checkbox" id="sortNatr1m" ${state.sortByNatr1m ? 'checked' : ''} onchange="handleNatrSort(this.checked)" style="accent-color:#8b5cf6;width:14px;height:14px;cursor:pointer;">
                    <span>NATR 1m</span>
                </label>
                <button class="live-btn ${state.isLive ? 'active' : ''}" onclick="toggleLive()">
                    <div class="live-btn-dot ${state.isLive ? '' : 'paused'}"></div>
                    ${state.isLive ? 'LIVE' : 'PAUSED'}
                </button>
                <button class="densities-refresh-btn" onclick="loadDensitiesTable()">Обновить</button>
            </div>
        </div>
        <div class="exchange-filters-bar" style="display:flex; align-items:center; gap:6px; padding:8px 16px; background:rgba(13,17,23,0.8); backdrop-filter:blur(12px); border-bottom:1px solid var(--border-primary); flex-wrap:wrap;">
            <span style="font-size:10px; color:#94a3b8; font-family:'JetBrains Mono',monospace; margin-right:8px;">Биржи:</span>
            ${renderExchangeToggles()}
        </div>
        <div id="densities-grid" class="densities-grid"></div>
        <div id="stats-container"></div>
    `;

    // Bind controls from overlay body (scoped, not document-wide)
    densitiesLimit = densitiesOverlayBody.querySelector('#densitiesLimit');
}

// ==========================================
// CONTENT (динамическое содержимое — сетка + статистика)
// ==========================================
function renderGroupCard(group, index) {
    // Local max volume within this group for proportional bars
    const localMaxVolume = Math.max(...group.densities.map(d => d.volume), 1);
    const domain = getExchangeDomain(group.exchange);

    const rows = group.densities.map((d, rowIdx) => {
        const volumePercent = localMaxVolume > 0
            ? Math.max(12, Math.min(100, (d.volume / localMaxVolume) * 100))
            : 12;
        const sideColor = d.side === 'buy' ? '#14b8a6' : '#f59e0b';
        const tier = getVolumeTier(d.volume);
        const isWhale = tier === 'high';

        return `
            <div class="group-density-row ${d.side}-row ${isWhale ? 'whale-row' : ''}"
                 data-id="${d.id}"
                 data-age="${d.age_seconds}"
                 data-volume="${d.volume}"
                 data-local-max="${localMaxVolume}"
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

    return `
        <div class="group-card animate-fade-in" style="animation-delay: ${index * 50}ms">
            <div class="group-card-header">
                <div class="group-symbol-row">
                    ${domain ? `<img src="https://www.google.com/s2/favicons?domain=${domain}&sz=16" onerror="this.style.display='none'" class="group-exchange-icon" style="width:16px;height:16px;border-radius:3px;margin-right:6px;flex-shrink:0;">` : ''}
                    <span class="group-symbol">${group.symbol}</span>
                    <span class="group-exchange-badge">${group.exchangeShort}</span>
                    <span class="group-market-badge">${group.market === 'futures' ? 'F' : 'S'}</span>
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

    // Fetch NATR 1m data if sort is enabled
    let natrData = {};
    if (state.sortByNatr1m) {
        // Get unique symbols from processed densities
        const symbols = [...new Set(processed.map(d => d.symbol))];
        // Fetch NATR data from API
        fetch(`/api/natr/`).then(r => r.json()).then(data => {
            if (data.natr) {
                natrData = data.natr;
            }
            // Re-render with NATR data
            const groups = groupBySymbolAndExchange(processed, natrData);
            renderGroups(grid, groups);
        }).catch(err => {
            console.warn('NATR fetch failed:', err);
            const groups = groupBySymbolAndExchange(processed, {});
            renderGroups(grid, groups);
        });
        return; // Will re-render after NATR fetch
    }

    const groups = groupBySymbolAndExchange(processed, natrData);
    renderGroups(grid, groups);
}

function renderGroups(grid, groups) {
    const statsContainer = document.getElementById('stats-container');
    
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
            renderGroupCard(group, index)
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

    if (statsContainer) {
        statsContainer.innerHTML = renderStatsFooter(getProcessedDensities(), state.serverTime);
    }
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
        if (barEl) {
            const localMax = parseFloat(entry.rowEl.dataset.localMax) || 1;
            const pct = Math.max(12, Math.min(100, (vol / localMax) * 100));
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
// GROUPING LOGIC (symbol + exchange + market)
// ==========================================
function groupBySymbolAndExchange(densities, natrData = {}) {
    const groups = {};
    for (const d of densities) {
        const key = `${d.symbol}_${d.exchange}_${d.market}`;
        if (!groups[key]) groups[key] = [];
        groups[key].push(d);
    }

    const result = [];
    for (const key of Object.keys(groups)) {
        const [symbol, exchange, market] = key.split('_');
        const items = groups[key].sort((a, b) => b.volume - a.volume);
        const totalVolume = items.reduce((sum, d) => sum + d.volume, 0);
        const buyCount = items.filter(d => d.side === 'buy').length;
        const sellCount = items.filter(d => d.side === 'sell').length;

        result.push({
            symbol,
            exchange,
            market,
            exchangeShort: getExchangeShort(exchange),
            densities: items,
            totalVolume,
            count: items.length,
            buyCount,
            sellCount
        });
    }

    // Sort by NATR 1m if enabled, otherwise by total volume
    if (state.sortByNatr1m && Object.keys(natrData).length > 0) {
        result.sort((a, b) => {
            const natrA = (natrData[a.symbol]?.natr_1m30) || 0;
            const natrB = (natrData[b.symbol]?.natr_1m30) || 0;
            return natrB - natrA; // Descending NATR
        });
    } else {
        result.sort((a, b) => b.totalVolume - a.totalVolume);
    }
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

    // Filter by exchange filters
    result = result.filter(d => {
        const cfg = state.exchangeFilters[d.exchange];
        if (!cfg) return true;
        return cfg[d.market] === true;
    });

    // Filter by per-exchange min volumes
    result = result.filter(d => {
        const volCfg = state.exchangeMinVolumes[d.exchange];
        if (!volCfg) return true;
        const minVol = volCfg[d.market];
        if (minVol === undefined || minVol === null) return true;
        return d.volume >= minVol;
    });

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

    const limit = parseInt(densitiesLimit?.value) || 20;
    // Use a low global min_volume to get all data, then filter per-exchange on client
    const globalMinVolume = 1000;

    const grid = document.getElementById('densities-grid');
    if (grid) grid.innerHTML = '<div style="grid-column: 1/-1; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:20px; color:#6b7280;">Загрузка...</div>';

    try {
        // Fetch both futures and spot in parallel
        const [futuresRes, spotRes] = await Promise.all([
            fetch(`/api/densities/?${new URLSearchParams({market: 'futures', min_volume: globalMinVolume, limit: limit * 5}).toString()}`),
            fetch(`/api/densities/?${new URLSearchParams({market: 'spot', min_volume: globalMinVolume, limit: limit * 5}).toString()}`)
        ]);

        if (!futuresRes.ok) throw new Error(`Futures HTTP ${futuresRes.status}`);
        if (!spotRes.ok) throw new Error(`Spot HTTP ${spotRes.status}`);

        const [futuresData, spotData] = await Promise.all([futuresRes.json(), spotRes.json()]);

        // Merge densities from both markets, adding market field to each density
        const futuresDensities = (futuresData.densities || []).map(d => ({...d, market: 'futures'}));
        const spotDensities = (spotData.densities || []).map(d => ({...d, market: 'spot'}));
        
        state.densities = [...futuresDensities, ...spotDensities];
        state.serverTime = futuresData.server_time || spotData.server_time || Date.now();
        renderContent();
    } catch (err) {
        console.error('loadDensitiesTable error:', err);
        const grid = document.getElementById('densities-grid');
        if (grid) grid.innerHTML = `<div style="grid-column: 1/-1; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:20px; color:#ef4444;">Ошибка: ${err.message}</div>`;
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

function handleNatrSort(enabled) {
    state.sortByNatr1m = enabled;
    saveExchangeFilters();
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
