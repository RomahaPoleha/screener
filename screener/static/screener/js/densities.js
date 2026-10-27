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
    currentPrices: {},
    // Exchange filters: { exchangeId: { futures: bool, spot: bool } }
    exchangeFilters: {},
    // Exchange min volumes: { exchangeId: { futures: number, spot: number } }
    exchangeMinVolumes: {},
    // Sort by NATR 1m
    sortByNatr1m: false,
    // Min age filter (seconds)
    minAgeFilter: 0,
    // Hidden symbols
    hiddenSymbols: new Set()
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
            <label class="exchange-toggle" title="${ex.name}">
                <img src="https://www.google.com/s2/favicons?domain=${ex.domain}&sz=16" onerror="this.style.display='none'" class="exchange-icon">
                <span class="exchange-label">${ex.label}</span>
                ${!isAlpha ? `
                <input type="checkbox" id="exFilter-${ex.id}-futures" ${futuresChecked} ${futuresDisabled} onchange="handleExchangeFilter('${ex.id}', 'futures', this.checked)">
                <span class="market-label futures">F</span>
                <input type="number" id="exMinVol-${ex.id}-futures" value="${futuresMinVol}" min="1000" step="1000" onchange="handleMinVolChange('${ex.id}', 'futures', this.value)" ${futuresDisabled}>
                ` : ''}
                <input type="checkbox" id="exFilter-${ex.id}-spot" ${spotChecked} onchange="handleExchangeFilter('${ex.id}', 'spot', this.checked)">
                <span class="market-label spot">S</span>
                <input type="number" id="exMinVol-${ex.id}-spot" value="${spotMinVol}" min="1000" step="1000" onchange="handleMinVolChange('${ex.id}', 'spot', this.value)">
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

// Handle exchange filter change
function handleExchangeFilter(exchangeId, market, enabled) {
    if (!state.exchangeFilters[exchangeId]) {
        state.exchangeFilters[exchangeId] = { futures: true, spot: true };
    }
    state.exchangeFilters[exchangeId][market] = enabled;
    saveExchangeFilters();
    renderContent(); // Re-render with new filters
}

// Get exchange domain for favicon
function getExchangeDomain(exchangeId) {
    const ex = EXCHANGES_CONFIG.find(e => e.id === exchangeId);
    return ex ? ex.domain : '';
}

function copySymbol(symbol) {
    // Добавляем суффикс USDT для полного торгового пары
    const fullSymbol = symbol + 'USDT';
    navigator.clipboard.writeText(fullSymbol).then(() => {
        // Visual feedback
        const toast = document.createElement('div');
        toast.textContent = `Скопировано: ${fullSymbol}`;
        toast.style.cssText = `
            position: fixed;
            bottom: 24px;
            left: 50%;
            transform: translateX(-50%);
            background: var(--accent-teal);
            color: var(--bg-primary);
            padding: 10px 20px;
            border-radius: 8px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            font-weight: 600;
            z-index: 2000;
            box-shadow: 0 4px 20px rgba(0,0,0,0.4);
            animation: fadeInUp 0.3s ease-out;
        `;
        document.body.appendChild(toast);
        setTimeout(() => {
            toast.style.animation = 'fadeInUp 0.3s ease-out reverse';
            setTimeout(() => toast.remove(), 300);
        }, 1500);
    }).catch(err => {
        console.error('Failed to copy:', err);
    });
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
        <div class="dashboard-header" style="display:flex; justify-content:space-between; align-items:center;">
            <div class="header-title">
                <span class="icon">🗺️</span>
                <span>Карта плотностей</span>
            </div>
            <button class="densities-hidden-btn" onclick="openHiddenCoinsModal()" style="padding:6px 14px; border-radius:6px; border:1px solid var(--border-primary); background:transparent; color:var(--text-secondary); cursor:pointer; font-family:'Inter',sans-serif; font-size:12px; font-weight:600; transition:all 0.2s;">Скрытые монеты (${state.hiddenSymbols.size})</button>
        </div>
        <div class="filter-bar">
            <div style="display:flex; align-items:center; gap:8px; flex:1; min-width:200px;">
                <label style="font-size:10px; color:#94a3b8; font-family:'JetBrains Mono',monospace; margin-left:8px; margin-right:6px;">Лимит:</label>
                <input type="number" id="densitiesLimit" value="20" min="1" max="50" step="1" style="width:60px; font-size:11px; font-family:'JetBrains Mono',monospace; background:rgba(30,41,59,0.3); border:1px solid var(--border-primary); color:var(--text-primary); padding:4px 8px; border-radius:4px;">
                <label style="font-size:10px; color:#94a3b8; font-family:'JetBrains Mono',monospace; margin-left:8px; margin-right:6px;">Поиск:</label>
                <input type="text" id="search-input" class="search-input" placeholder="BTC, ETH..." value="${state.searchQuery}">
                <label style="font-size:10px; color:#94a3b8; font-family:'JetBrains Mono',monospace; margin-left:8px; margin-right:6px;">Мин. возраст:</label>
                <input type="number" id="min-age-input" value="${state.minAgeFilter}" min="0" max="3600" step="1" onchange="handleMinAgeFilter(this.value)" style="width:70px; font-size:11px; font-family:'JetBrains Mono',monospace; background:rgba(30,41,59,0.3); border:1px solid var(--border-primary); color:var(--text-primary); padding:4px 8px; border-radius:4px;" placeholder="мин">
            </div>
            <div style="display:flex; align-items:center; gap:6px;">
                <label style="display:flex; align-items:center; gap:6px; cursor:pointer; font-size:10px; color:#94a3b8; font-family:'JetBrains Mono',monospace;">
                    <span>Фильтрация по Natr 1m</span>
                    <input type="checkbox" id="sortNatr1m" ${state.sortByNatr1m ? 'checked' : ''} onchange="handleNatrSort(this.checked)" style="accent-color:#8b5cf6;width:14px;height:14px;cursor:pointer;">
                </label>
                <button class="filter-btn ${state.sideFilter === 'all' ? 'active' : ''}" data-filter="all" onclick="handleSideFilter('all')">Все</button>
                <button class="filter-btn ${state.sideFilter === 'buy' ? 'active-buy' : ''}" data-filter="buy" onclick="handleSideFilter('buy')">BUY</button>
                <button class="filter-btn ${state.sideFilter === 'sell' ? 'active-sell' : ''}" data-filter="sell" onclick="handleSideFilter('sell')">SELL</button>
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

        // % отклонение цены плотности от текущей рыночной цены
        const currentPrice = state.currentPrices[d.symbol] || 0;
        let pctHtml = '';
        if (currentPrice > 0) {
            const pct = ((d.price - currentPrice) / currentPrice) * 100;
            const absPct = Math.abs(pct).toFixed(2);
            const sign = pct >= 0 ? '+' : '−';
            pctHtml = `<span class="row-pct" style="color:${sideColor};" title="От текущей цены $${currentPrice.toLocaleString()}">${sign}${absPct}%</span>`;
        }

        return `
            <div class="group-density-row ${d.side}-row ${isWhale ? 'whale-row' : ''}"
                 data-id="${d.id}"
                 data-age="${d.age_seconds}"
                 data-volume="${d.volume}"
                 data-local-max="${localMaxVolume}"
                 style="animation-delay: ${index * 40 + rowIdx * 20}ms">
                <div class="row-volume">$${fmtVolume(d.volume)}</div>
                <div class="row-bar-container">
                    <div class="row-bar-fill" style="width: ${volumePercent}%; background: linear-gradient(90deg, ${sideColor} 0%, ${sideColor}bb 60%, ${sideColor}33 100%);"></div>
                </div>
                <div class="row-meta">
                    ${isWhale ? '<span class="whale-tag">⚡</span>' : ''}
                    <span class="row-price">$${d.price.toLocaleString()}</span>
                    ${pctHtml}
                    <span class="row-age">${fmtAge(d.age_seconds)}</span>
                </div>
            </div>
        `;
    }).join('');

    return `
        <div class="group-card animate-fade-in" style="animation-delay: ${index * 50}ms">
            <div class="group-card-header">
                <div class="group-symbol-row">
                    <span class="group-symbol" onclick="copySymbol('${group.symbol}')" title="Клик для копирования">${group.symbol}</span>
                    <span class="group-exchange-badge">${group.exchangeShort}</span>
                    <span class="group-market-badge">${group.market === 'futures' ? 'F' : 'S'}</span>
                    <button class="group-hide-btn" onclick="hideSymbol('${group.symbol}')" title="Скрыть монету" style="background:none;border:none;cursor:pointer;font-size:16px;padding:2px 6px;line-height:1;color:var(--text-muted);transition:color 0.2s;" onmouseover="this.style.color='var(--accent-red)'" onmouseout="this.style.color='var(--text-muted)'">👁</button>
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
        </div>
    `;
}

function renderContent() {
    const grid = document.getElementById('densities-grid');
    const statsContainer = document.getElementById('stats-container');
    if (!grid || !statsContainer) return;

    const processed = getProcessedDensities();

    // Fetch current prices for % deviation
    // Primary: /api/data/ (cached 60s, cheap)
    // Fallback: /api/candles/{symbol}/1m (cached 300s) for symbols not in /api/data/
    fetch(`/api/data/`).then(r => r.json()).then(data => {
        const priceMap = {};
        if (Array.isArray(data)) {
            data.forEach(c => { priceMap[c.symbol] = c.price; });
        }

        // Find symbols without price
        const uniqueSymbols = [...new Set(processed.map(d => d.symbol))];
        const missing = uniqueSymbols.filter(s => !priceMap[s] || priceMap[s] <= 0);

        if (missing.length === 0) {
            state.currentPrices = priceMap;
            const filtered = getProcessedDensities();
            sortByPctDeviation(filtered);
            renderWithNatr(grid, filtered);
            return;
        }

        // Fetch prices for missing symbols from candles API in parallel
        const fetches = missing.map(sym =>
            fetch(`/api/candles/${sym}/?tf=1m`)
                .then(r => r.ok ? r.json() : null)
                .then(candles => {
                    if (Array.isArray(candles) && candles.length > 0) {
                        priceMap[sym] = candles[candles.length - 1].close;
                    }
                })
                .catch(() => {})
        );

        Promise.all(fetches).then(() => {
            state.currentPrices = priceMap;
            const filtered = getProcessedDensities();
            sortByPctDeviation(filtered);
            renderWithNatr(grid, filtered);
        });
    }).catch(err => {
        console.warn('Price fetch failed:', err);
        // Fallback: try candles for all unique symbols
        const uniqueSymbols = [...new Set(processed.map(d => d.symbol))];
        const priceMap = {};
        const fetches = uniqueSymbols.map(sym =>
            fetch(`/api/candles/${sym}/?tf=1m`)
                .then(r => r.ok ? r.json() : null)
                .then(candles => {
                    if (Array.isArray(candles) && candles.length > 0) {
                        priceMap[sym] = candles[candles.length - 1].close;
                    }
                })
                .catch(() => {})
        );
        Promise.all(fetches).then(() => {
            state.currentPrices = priceMap;
            const filtered = getProcessedDensities();
            sortByPctDeviation(filtered);
            renderWithNatr(grid, filtered);
        });
    });
}

function renderWithNatr(grid, processed) {
    let natrData = {};
    if (state.sortByNatr1m) {
        fetch(`/api/natr/`).then(r => r.json()).then(data2 => {
            if (data2.natr) {
                natrData = data2.natr;
            }
            const groups = groupBySymbolAndExchange(processed, natrData, false);
            renderGroups(grid, groups);
        }).catch(err => {
            console.warn('NATR fetch failed:', err);
            const groups = groupBySymbolAndExchange(processed, {}, false);
            renderGroups(grid, groups);
        });
        return;
    }
    const groups = groupBySymbolAndExchange(processed, natrData, true);
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
function groupBySymbolAndExchange(densities, natrData = {}, preserveOrder = false) {
    const groups = {};
    for (const d of densities) {
        const key = `${d.symbol}_${d.exchange}_${d.market}`;
        if (!groups[key]) groups[key] = [];
        groups[key].push(d);
    }

    const result = [];
    for (const key of Object.keys(groups)) {
        const [symbol, exchange, market] = key.split('_');
        const items = groups[key];

        // Sort items within group
        if (preserveOrder) {
            // Sort by % deviation from current price
            items.sort((a, b) => {
                const priceA = state.currentPrices[a.symbol] || 0;
                const priceB = state.currentPrices[b.symbol] || 0;
                if (!priceA || !priceB) return 0;
                const pctA = ((a.price - priceA) / priceA) * 100;
                const pctB = ((b.price - priceB) / priceB) * 100;
                return pctB - pctA; // Descending: highest positive % first
            });
        } else {
            // Default: sort by volume
            items.sort((a, b) => b.volume - a.volume);
        }

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

    // Sort groups
    if (!preserveOrder) {
        if (state.sortByNatr1m && Object.keys(natrData).length > 0) {
            // Сортируем исключительно по NATR бинанса (natr_1m30).
            // Один и тот же NATR применяется ко всем биржам одного символа,
            // поэтому все карточки символа идут подряд в порядке бинанс-NATR.
            const symbolOrder = [...new Set(densities.map(d => d.symbol))].sort((a, b) => {
                const natrA = natrData[a]?.natr_1m30;
                const natrB = natrData[b]?.natr_1m30;
                return (natrB ?? -1) - (natrA ?? -1);
            });
            const rank = new Map(symbolOrder.map((s, i) => [s, i]));
            result.sort((a, b) => {
                const ra = rank.has(a.symbol) ? rank.get(a.symbol) : Infinity;
                const rb = rank.has(b.symbol) ? rank.get(b.symbol) : Infinity;
                if (ra !== rb) return ra - rb;
                return b.totalVolume - a.totalVolume;
            });
        } else {
            result.sort((a, b) => b.totalVolume - a.totalVolume);
        }
    }
    // If preserveOrder = true, keep insertion order (from sorted densities)
    return result;
}

function getProcessedDensities() {
    let result = [...state.densities];

    // Filter out hidden symbols
    if (state.hiddenSymbols.size > 0) {
        result = result.filter(d => !state.hiddenSymbols.has(d.symbol));
    }

    if (state.sideFilter !== 'all') {
        result = result.filter(d => d.side === state.sideFilter);
    }

    if (state.searchQuery) {
        const q = state.searchQuery.toUpperCase();
        result = result.filter(d => d.symbol.includes(q));
    }

    // Filter by min age (value entered in minutes)
    if (state.minAgeFilter > 0) {
        result = result.filter(d => d.age_seconds >= state.minAgeFilter * 60);
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

    // Auto-filter: hide densities whose price deviates from current price by more than 30%
    result = result.filter(d => {
        const currentPrice = state.currentPrices[d.symbol];
        if (!currentPrice || currentPrice <= 0) return true;
        const pct = Math.abs(((d.price - currentPrice) / currentPrice) * 100);
        return pct <= 30;
    });

    return result;
}

function sortByPctDeviation(densities) {
    return densities.sort((a, b) => {
        const priceA = state.currentPrices[a.symbol] || 0;
        const priceB = state.currentPrices[b.symbol] || 0;
        if (!priceA || !priceB) return 0;
        const pctA = ((a.price - priceA) / priceA) * 100;
        const pctB = ((b.price - priceB) / priceB) * 100;
        return pctB - pctA; // Descending: highest positive % first
    });
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
        // Уменьшен множитель с 5x до 2x — сервер теперь отдаёт уже отфильтрованные и отсортированные данные
        const [futuresRes, spotRes] = await Promise.all([
            fetch(`/api/densities/?${new URLSearchParams({market: 'futures', min_volume: globalMinVolume, limit: limit * 2}).toString()}`),
            fetch(`/api/densities/?${new URLSearchParams({market: 'spot', min_volume: globalMinVolume, limit: limit * 2}).toString()}`)
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

function handleMinAgeFilter(value) {
    state.minAgeFilter = parseInt(value) || 0;
    renderContent();
}

function hideSymbol(symbol) {
    state.hiddenSymbols.add(symbol);
    renderShell(); // Update hidden coins count
    renderContent(); // Re-render without hidden symbol
}

function showSymbol(symbol) {
    state.hiddenSymbols.delete(symbol);
    renderShell(); // Update hidden coins count
    renderContent(); // Re-render with shown symbol
}

function openHiddenCoinsModal() {
    if (state.hiddenSymbols.size === 0) {
        alert('Нет скрытых монет');
        return;
    }
    
    const hiddenList = Array.from(state.hiddenSymbols).sort();
    
    const modal = document.createElement('div');
    modal.style.cssText = `
        position: fixed;
        top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.7);
        z-index: 2000;
        display: flex;
        align-items: center;
        justify-content: center;
    `;
    
    modal.innerHTML = `
        <div style="background: var(--bg-secondary); border: 1px solid var(--border-primary); border-radius: 12px; padding: 24px; min-width: 300px; max-width: 400px; max-height: 80vh; overflow-y: auto;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
                <h3 style="font-size:16px; font-weight:700; color:var(--text-primary);">Скрытые монеты (${hiddenList.length})</h3>
                <button onclick="this.closest('.modal-overlay').remove()" style="background:none;border:none;font-size:24px;cursor:pointer;color:var(--text-muted);line-height:1;">✕</button>
            </div>
            <div style="display:flex; flex-direction:column; gap:8px;">
                ${hiddenList.map(sym => `
                    <div style="display:flex; justify-content:space-between; align-items:center; padding:10px 12px; background:var(--bg-card); border:1px solid var(--border-primary); border-radius:8px;">
                        <span style="font-family:'JetBrains Mono',monospace; font-size:14px; font-weight:700; color:var(--text-primary);">${sym}</span>
                        <button onclick="showSymbol('${sym}'); this.parentElement.remove(); updateHiddenCount();" style="background:var(--accent-teal);border:none;color:var(--bg-primary);padding:6px 12px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;">Показать</button>
                    </div>
                `).join('')}
            </div>
            <button onclick="state.hiddenSymbols.clear(); renderShell(); renderContent(); this.closest('.modal-overlay').remove();" style="margin-top:16px;width:100%;background:var(--accent-red);border:none;color:white;padding:10px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;">Очистить всё</button>
        </div>
    `;
    
    modal.classList.add('modal-overlay');
    document.body.appendChild(modal);
    
    // Close on backdrop click
    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.remove();
    });
}

function updateHiddenCount() {
    const btn = document.querySelector('.densities-hidden-btn');
    if (btn) {
        btn.textContent = `Скрытые монеты (${state.hiddenSymbols.size})`;
    }
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
