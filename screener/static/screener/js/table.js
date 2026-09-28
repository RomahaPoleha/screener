// ==========================================
// table.js — ТАБЛИЦА МОНЕТ
// Рендер, сортировка, фильтры, подсветка
// ==========================================

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
    els.table.innerHTML = data.map(coin => {
        const isUp = coin.change >= 0;
        const natr = natrData[coin.symbol] || {};
        const n1 = natr.natr_1m30;
        const n5 = natr.natr_5m14;
        const n1Txt = (n1 !== undefined && n1 !== null) ? n1.toFixed(1) : '-';
        const n5Txt = (n5 !== undefined && n5 !== null) ? n5.toFixed(1) : '-';
        const colorId = coinColors[coin.symbol];
        const colorHex = colorId ? (COIN_COLOR_OPTIONS.find(c => c.id === colorId) || {}).hex : null;

        // Подсветка активной монеты
        const isActive = coin.symbol === currentSymbol;
        const activeClass = isActive ? ' active' : '';

        return `<div class="coin-row${activeClass}" data-symbol="${coin.symbol}" onclick="openChart('${coin.symbol}')">
            <div class="coin-color-dot" style="background:${colorHex || 'transparent'};"
                 onclick="event.stopPropagation(); openColorPicker(event, '${coin.symbol}')"
                 title="Цвет группы"></div>
            <div class="coin-symbol">${coin.symbol}</div>
            <div class="coin-change ${isUp ? 'text-up' : 'text-down'}">${isUp ? '+' : ''}${coin.change}%</div>
            <div class="coin-volume">$${fmt(coin.volume)}</div>
            <div class="coin-natr ${n1 ? getNatrClass(n1) : 'empty'}">${n1Txt}</div>
            <div class="coin-natr ${n5 ? getNatrClass(n5) : 'empty'}">${n5Txt}</div>
        </div>`;
    }).join('');
}

// Функция быстрого обновления подсветки (без перерисовки таблицы)
function updateActiveCoinHighlight() {
    document.querySelectorAll('.coin-row').forEach(row => {
        if (row.dataset.symbol === currentSymbol) {
            row.classList.add('active');
        } else {
            row.classList.remove('active');
        }
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
    const searchVal = els.search.value.toUpperCase();
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
            if (['change', 'volume'].includes(sortState.field)) {
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