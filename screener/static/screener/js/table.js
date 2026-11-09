// ==========================================
// table.js — ТАБЛИЦА МОНЕТ
// Рендер, сортировка, фильтры, подсветка, настройки столбцов
// ==========================================

const COIN_COLUMN_CONFIG = [
    { id: 'color',   defaultVisible: true },
    { id: 'symbol',  defaultVisible: true, fixed: true },
    { id: 'change',  defaultVisible: true },
    { id: 'gap',     defaultVisible: true, fixed: true },
    { id: 'deals',   defaultVisible: true },
    { id: 'volume',  defaultVisible: true },
    { id: 'natr_1m', defaultVisible: true },
    { id: 'natr_5m', defaultVisible: true },
];

const COIN_COLUMN_WIDTHS = {
    color: 16, symbol: 48, change: 45, gap: 8,
    deals: 45, volume: 45, natr_1m: 45, natr_5m: 45,
};

const COIN_COLUMN_LABELS = {
    color: 'Цвет', symbol: 'Пара', change: 'Изм. 24ч',
    gap: '', deals: 'Сделки', volume: 'Объём',
    natr_1m: 'NATR 1m', natr_5m: 'NATR 5m',
};

const GEAR_WIDTH = 20;
const PANEL_PADDING = 24;
const COL_GAP = 4;
const PANEL_MIN_WIDTH = 180;

function getColumnPrefs() {
    try {
        const stored = localStorage.getItem('coin_column_visibility');
        if (stored) return JSON.parse(stored);
    } catch (e) {}
    const prefs = {};
    COIN_COLUMN_CONFIG.forEach(c => { prefs[c.id] = c.defaultVisible; });
    return prefs;
}

function saveColumnPrefs(prefs) {
    try { localStorage.setItem('coin_column_visibility', JSON.stringify(prefs)); } catch (e) {}
}

function getVisibleColumns() {
    const prefs = getColumnPrefs();
    return COIN_COLUMN_CONFIG.filter(c => c.fixed || prefs[c.id] !== false);
}

function getGridColumns() {
    const cols = getVisibleColumns().map(c => COIN_COLUMN_WIDTHS[c.id] + 'px');
    cols.push(GEAR_WIDTH + 'px');
    return cols.join(' ');
}

function getPanelWidth() {
    const visible = getVisibleColumns();
    const numCols = visible.length + 1;
    const colWidth = visible.reduce((sum, c) => sum + COIN_COLUMN_WIDTHS[c.id], 0) + GEAR_WIDTH;
    const gaps = COL_GAP * (numCols - 1);
    const total = colWidth + gaps + PANEL_PADDING;
    return Math.max(PANEL_MIN_WIDTH, total);
}

function updatePanelWidth() {
    const panel = document.querySelector('.right-panel');
    if (panel) panel.style.width = getPanelWidth() + 'px';
}

function applyGridColumnLayout() {
    const gridCols = getGridColumns();
    const header = document.getElementById('coinsHeader');
    if (header) {
        header.style.gridTemplateColumns = gridCols;
        const prefs = getColumnPrefs();
        COIN_COLUMN_CONFIG.forEach(col => {
            const cell = header.querySelector('.col-' + col.id);
            if (cell) cell.style.display = (col.fixed || prefs[col.id] !== false) ? '' : 'none';
        });
    }
    document.querySelectorAll('.coin-row').forEach(row => {
        row.style.gridTemplateColumns = gridCols;
    });
    updatePanelWidth();
}

function toggleColumnSettings() {
    const dropdown = document.getElementById('columnSettingsDropdown');
    if (!dropdown) return;
    if (dropdown.classList.contains('active')) {
        dropdown.classList.remove('active');
        return;
    }
    renderColumnSettings();
    dropdown.classList.add('active');
}

function renderColumnSettings() {
    const dropdown = document.getElementById('columnSettingsDropdown');
    if (!dropdown) return;
    const prefs = getColumnPrefs();
    let html = '';
    COIN_COLUMN_CONFIG.forEach(col => {
        if (col.fixed) return;
        const checked = prefs[col.id] !== false ? 'checked' : '';
        html += `<label><input type="checkbox" data-col="${col.id}" ${checked} onchange="onColumnToggle('${col.id}', this.checked)"> ${COIN_COLUMN_LABELS[col.id]}</label>`;
    });
    dropdown.innerHTML = html;
}

function onColumnToggle(colId, visible) {
    const prefs = getColumnPrefs();
    prefs[colId] = visible;
    saveColumnPrefs(prefs);
    applyGridColumnLayout();
    if (typeof applyLocalFilters === 'function') applyLocalFilters();
}

document.addEventListener('click', (e) => {
    const dropdown = document.getElementById('columnSettingsDropdown');
    const btn = document.getElementById('columnGearBtn');
    if (!dropdown || !dropdown.classList.contains('active')) return;
    if (e.target === btn || btn?.contains(e.target)) return;
    if (dropdown.contains(e.target)) return;
    dropdown.classList.remove('active');
});

document.addEventListener('DOMContentLoaded', () => {
    applyGridColumnLayout();
});

function resetFilters() {
    document.getElementById('searchInput').value = '';
    document.getElementById('volRange').value = 0;
    document.getElementById('changeRange').value = -100;
    applyLocalFilters();
}

function renderTable(data) {
    if (els.coinsCount) els.coinsCount.textContent = data.length;
    if (!data.length) {
        els.table.innerHTML = '<div style="color:#6b7280; text-align:center; padding:20px;">Нет данных</div>';
        return;
    }
    const prefs = getColumnPrefs();
    const vis = {};
    COIN_COLUMN_CONFIG.forEach(c => { vis[c.id] = c.fixed || prefs[c.id] !== false; });

    els.table.innerHTML = data.map(coin => {
        const isUp = coin.change >= 0;
        const natr = natrData[coin.symbol] || {};
        const n1 = natr.natr_1m30;
        const n5 = natr.natr_5m14;
        const n1Txt = (n1 !== undefined && n1 !== null) ? n1.toFixed(1) : '-';
        const n5Txt = (n5 !== undefined && n5 !== null) ? n5.toFixed(1) : '-';
        const colorId = coinColors[coin.symbol];
        const colorHex = colorId ? (COIN_COLOR_OPTIONS.find(c => c.id === colorId) || {}).hex : null;

        const isActive = coin.symbol === currentSymbol;
        const activeClass = isActive ? ' active' : '';
        const dealsTxt = (coin.deals !== undefined && coin.deals !== null) ? formatVolumeText(coin.deals) : '-';

        let cells = '';
        if (vis.color) cells += `<div class="coin-color-dot" style="background:${colorHex || 'transparent'};" onclick="event.stopPropagation(); openColorPicker(event, '${coin.symbol}')" title="Цвет группы"></div>`;
        if (vis.symbol) cells += `<div class="coin-symbol">${coin.symbol}</div>`;
        if (vis.change) cells += `<div class="coin-change ${isUp ? 'text-up' : 'text-down'}">${isUp ? '+' : ''}${coin.change}%</div>`;
        if (vis.gap) cells += `<div class="coin-gap"></div>`;
        if (vis.deals) cells += `<div class="coin-deals" title="Сделок за 24ч">${dealsTxt}</div>`;
        if (vis.volume) cells += `<div class="coin-volume">$${fmt(coin.volume)}</div>`;
        if (vis.natr_1m) cells += `<div class="coin-natr ${n1 ? getNatrClass(n1) : 'empty'}">${n1Txt}</div>`;
        if (vis.natr_5m) cells += `<div class="coin-natr ${n5 ? getNatrClass(n5) : 'empty'}">${n5Txt}</div>`;
        cells += `<div></div>`;

        return `<div class="coin-row${activeClass}" data-symbol="${coin.symbol}" onclick="openChart('${coin.symbol}')" style="grid-template-columns: ${getGridColumns()}">
            ${cells}
        </div>`;
    }).join('');

    applyGridColumnLayout();
}

function updateActiveCoinHighlight() {
    document.querySelectorAll('.coin-row').forEach(row => {
        if (row.dataset.symbol === currentSymbol) row.classList.add('active');
        else row.classList.remove('active');
    });
}

function sortBy(field) {
    if (sortState.field === field) sortState.direction = sortState.direction === 'asc' ? 'desc' : 'asc';
    else { sortState.field = field; sortState.direction = (field === 'natr_1m' || field === 'natr_5m') ? 'desc' : 'asc'; }

    document.querySelectorAll('.coins-header span').forEach(el => el.textContent = '');
    const arrow = document.getElementById(`sort-${field}`);
    if (arrow) arrow.textContent = sortState.direction === 'asc' ? '↑' : '↓';

    applyLocalFilters();
}

function applyLocalFilters() {
    const searchVal = document.getElementById('searchInput').value.toUpperCase();
    const minVol = parseFloat(els.vol.value);
    const minChange = parseFloat(els.change.value);

    let filtered = allCoins.filter(coin => {
        if (coin.volume < minVol || coin.change < minChange) return false;
        if (searchVal && !coin.symbol.includes(searchVal)) return false;
        return true;
    });

    if (sortState.field) {
        filtered.sort((a, b) => {
            let valA, valB;
            if (['change', 'volume', 'deals'].includes(sortState.field)) {
                valA = a[sortState.field]; valB = b[sortState.field];
            }
            else if (sortState.field === 'natr_1m') {
                valA = natrData[a.symbol]?.natr_1m30 ?? -1;
                valB = natrData[b.symbol]?.natr_1m30 ?? -1;
            }
            else if (sortState.field === 'natr_5m') {
                valA = natrData[a.symbol]?.natr_5m14 ?? -1;
                valB = natrData[b.symbol]?.natr_5m14 ?? -1;
            }
            return sortState.direction === 'asc' ? valA - valB : valB - valA;
        });
    } else {
        filtered.sort((a, b) => Math.abs(b.change) - Math.abs(a.change));
    }

    renderTable(filtered);
}