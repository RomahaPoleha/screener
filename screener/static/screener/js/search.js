// ==========================================
// search.js — ПОИСК МОНЕТ (МОДАЛЬНОЕ ОКНО)
// ==========================================

let searchModalOpen = false;
let selectedDropdownIndex = -1;

function openSearchModal() {
    const modal = document.getElementById('searchModal');
    const input = document.getElementById('searchInput');
    const dropdown = document.getElementById('searchDropdown');

    if (!modal || searchModalOpen) return;

    searchModalOpen = true;
    selectedDropdownIndex = -1;
    modal.classList.add('active');
    document.body.style.overflow = 'hidden';

    input.value = '';
    dropdown.innerHTML = '';
    dropdown.classList.remove('active');

    setTimeout(() => input.focus(), 50);
}

function closeSearchModal() {
    const modal = document.getElementById('searchModal');
    if (!modal || !searchModalOpen) return;

    searchModalOpen = false;
    selectedDropdownIndex = -1;
    modal.classList.remove('active');
    document.body.style.overflow = '';

    const input = document.getElementById('searchInput');
    const dropdown = document.getElementById('searchDropdown');
    input.value = '';
    dropdown.innerHTML = '';
    dropdown.classList.remove('active');
}

function showSearchDropdown(query) {
    const dropdown = document.getElementById('searchDropdown');
    const input = document.getElementById('searchInput');

    if (!query || query.length === 0) {
        hideSearchDropdown();
        return;
    }

    const filtered = allCoins.filter(coin =>
        coin.symbol.toUpperCase().includes(query.toUpperCase())
    ).slice(0, 10);

    if (filtered.length === 0) {
        hideSearchDropdown();
        return;
    }

    dropdown.innerHTML = filtered.map((coin, index) =>
        `<div class="search-dropdown-item" data-index="${index}" data-symbol="${coin.symbol}">
            <span class="symbol">${coin.symbol}</span>
            <span class="name">Vol: $${fmt(coin.volume)}</span>
        </div>`
    ).join('');

    dropdown.classList.add('active');
    selectedDropdownIndex = -1;
    updateDropdownSelection();
}

function hideSearchDropdown() {
    const dropdown = document.getElementById('searchDropdown');
    dropdown.classList.remove('active');
    selectedDropdownIndex = -1;
}

function updateDropdownSelection() {
    const items = document.querySelectorAll('#searchDropdown .search-dropdown-item');
    items.forEach((item, index) => {
        if (index === selectedDropdownIndex) {
            item.classList.add('selected');
            item.scrollIntoView({ block: 'nearest' });
        } else {
            item.classList.remove('selected');
        }
    });
}

function selectCoinFromSearch(symbol) {
    const input = document.getElementById('searchInput');
    input.value = symbol;
    hideSearchDropdown();
    applyLocalFilters();
    openChart(symbol);
    closeSearchModal();
}

function handleSearchKeydown(e) {
    const dropdown = document.getElementById('searchDropdown');
    const items = dropdown.querySelectorAll('.search-dropdown-item');
    const isDropdownActive = dropdown.classList.contains('active') && items.length > 0;

    switch (e.key) {
        case 'Escape':
            e.preventDefault();
            closeSearchModal();
            break;

        case 'ArrowDown':
            if (isDropdownActive) {
                e.preventDefault();
                selectedDropdownIndex = Math.min(selectedDropdownIndex + 1, items.length - 1);
                updateDropdownSelection();
            }
            break;

        case 'ArrowUp':
            if (isDropdownActive) {
                e.preventDefault();
                selectedDropdownIndex = Math.max(selectedDropdownIndex - 1, 0);
                updateDropdownSelection();
            }
            break;

        case 'Enter':
            if (isDropdownActive && selectedDropdownIndex >= 0) {
                e.preventDefault();
                const selectedItem = items[selectedDropdownIndex];
                if (selectedItem) {
                    selectCoinFromSearch(selectedItem.dataset.symbol);
                }
            } else if (e.target.value.trim()) {
                const firstMatch = allCoins.find(coin =>
                    coin.symbol.toUpperCase() === e.target.value.trim().toUpperCase()
                );
                if (firstMatch) {
                    e.preventDefault();
                    selectCoinFromSearch(firstMatch.symbol);
                }
            }
            break;

        case 'Tab':
            if (isDropdownActive) {
                e.preventDefault();
                selectedDropdownIndex = Math.min(selectedDropdownIndex + 1, items.length - 1);
                updateDropdownSelection();
            }
            break;
    }
}

function handleSearchInput(e) {
    showSearchDropdown(e.target.value);
}

function handleDropdownClick(e) {
    const item = e.target.closest('.search-dropdown-item');
    if (item && item.dataset.symbol) {
        selectCoinFromSearch(item.dataset.symbol);
    }
}

function handleDropdownMouseover(e) {
    const item = e.target.closest('.search-dropdown-item');
    if (item) {
        const items = document.querySelectorAll('#searchDropdown .search-dropdown-item');
        selectedDropdownIndex = Array.from(items).indexOf(item);
        updateDropdownSelection();
    }
}

function initSearchModal() {
    const modal = document.getElementById('searchModal');
    const input = document.getElementById('searchInput');
    const dropdown = document.getElementById('searchDropdown');
    const backdrop = modal?.querySelector('.search-modal-backdrop');

    if (!modal || !input || !dropdown) return;

    input.addEventListener('input', handleSearchInput);
    input.addEventListener('keydown', handleSearchKeydown);
    dropdown.addEventListener('click', handleDropdownClick);
    dropdown.addEventListener('mouseover', handleDropdownMouseover);

    backdrop?.addEventListener('click', closeSearchModal);

    document.addEventListener('keydown', (e) => {
        if (searchModalOpen) {
            handleSearchKeydown(e);
        }
    });

    document.addEventListener('keydown', (e) => {
        if (!searchModalOpen && !e.ctrlKey && !e.altKey && !e.metaKey) {
            const tag = e.target.tagName.toLowerCase();
            const isInput = tag === 'input' || tag === 'textarea' || e.target.isContentEditable;
            if (!isInput && e.key.length === 1 && /^[a-zA-Z0-9]$/.test(e.key)) {
                e.preventDefault();
                openSearchModal();
                setTimeout(() => {
                    input.value = e.key.toUpperCase();
                    input.dispatchEvent(new Event('input'));
                }, 50);
            }
        }
    });
}

document.addEventListener('DOMContentLoaded', initSearchModal);