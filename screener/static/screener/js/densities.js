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
    loadDensitiesTable();
}

function closeDensitiesOverlay() {
    if (!densitiesOverlay) initDensitiesElements();
    densitiesOverlay.classList.remove('active');
    densitiesBtn.classList.remove('active');
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

function renderDensitiesTable(data) {
    if (!densitiesOverlayBody) return;

    const { count, symbols_count, market, server_time } = data;
    let densities = data.densities || [];

    if (!densities || densities.length === 0) {
        densitiesOverlayBody.innerHTML = `
            <div style="color:#6b7280; text-align:center; padding:20px;">
                Плотностей не найдено (маркет: ${market}, символов в мастер-листе: ${symbols_count})
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

    // Иконка сортировки
    const getSortIcon = (colField) => {
        if (densitiesSortState.field !== colField) return '';
        return densitiesSortState.direction === 'asc' ? ' ▲' : ' ▼';
    };

    let html = `
        <div class="densities-table-header">
            <div class="densities-col densities-col-symbol" onclick="sortDensities('symbol')" style="cursor:pointer;">Символ${getSortIcon('symbol')}</div>
            <div class="densities-col densities-col-exchange" onclick="sortDensities('exchange')" style="cursor:pointer;">Биржа${getSortIcon('exchange')}</div>
            <div class="densities-col densities-col-price" onclick="sortDensities('price')" style="cursor:pointer;">Цена${getSortIcon('price')}</div>
            <div class="densities-col densities-col-volume" onclick="sortDensities('volume')" style="cursor:pointer;">Объём${getSortIcon('volume')}</div>
            <div class="densities-col densities-col-age">Возраст</div>
        </div>
        <div class="densities-table-body">
    `;

    for (const symbol of symbols) {
        const items = bySymbol[symbol];
        for (const d of items) {
            const sideClass = getSideClass(d.side);
            // Показываем сторону через цвет цены
            html += `
                <div class="densities-row">
                    <div class="densities-col densities-col-symbol">${symbol}</div>
                    <div class="densities-col densities-col-exchange">${d.exchange}</div>
                    <div class="densities-col densities-col-price ${sideClass}">${d.price.toLocaleString()}</div>
                    <div class="densities-col densities-col-volume">$${fmtVolume(d.volume)}</div>
                    <div class="densities-col densities-col-age">${fmtAge(d.age_seconds)}</div>
                </div>
            `;
        }
    }

    html += `</div>`;

    html += `
        <div class="densities-table-footer">
            Всего: ${count} уровней | Символов: ${symbols_count} | Маркет: ${market} | Обновлено: ${new Date(server_time * 1000).toLocaleTimeString()}
        </div>
    `;

    densitiesOverlayBody.innerHTML = html;
}

document.addEventListener('DOMContentLoaded', () => {
    initDensitiesElements();
});