// ==========================================
// search.js — ПОИСК МОНЕТ
// Выпадающий список поиска
// ==========================================

function showSearchDropdown(query) {
    if (!query || query.length === 0) { hideSearchDropdown(); return; }

    const filtered = allCoins.filter(coin =>
        coin.symbol.toUpperCase().includes(query.toUpperCase())
    ).slice(0, 10);

    const dropdown = document.getElementById('searchDropdown');
    if (filtered.length === 0) { hideSearchDropdown(); return; }

    dropdown.innerHTML = filtered.map(coin =>
        `<div class="search-dropdown-item" onclick="selectCoinFromSearch('${coin.symbol}')">
            <span class="symbol">${coin.symbol}</span>
            <span class="name">Vol: $${fmt(coin.volume)}</span>
        </div>`
    ).join('');

    dropdown.classList.add('active');
}

function hideSearchDropdown() {
    document.getElementById('searchDropdown').classList.remove('active');
}

function selectCoinFromSearch(symbol) {
    document.getElementById('searchInput').value = symbol;
    hideSearchDropdown();
    applyLocalFilters();
    openChart(symbol);
}