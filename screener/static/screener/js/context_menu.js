// ==========================================
// context_menu.js — КОНТЕКСТНОЕ МЕНЮ БИРЖ
// Правый клик на монету -> "Торговые площадки"
// ==========================================

let contextMenuSymbol = null;

function initContextMenu() {
    document.addEventListener('contextmenu', (e) => {
        const row = e.target.closest('.coin-row');
        if (!row) return;
        
        e.preventDefault();
        contextMenuSymbol = row.dataset.symbol;
        showContextMenu(e.clientX, e.clientY, contextMenuSymbol);
    });
    
    // Закрытие по клику вне меню
    document.addEventListener('click', (e) => {
        if (!e.target.closest('#exchangesContextMenu')) {
            hideContextMenu();
        }
    });
    
    // Закрытие по Escape
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') hideContextMenu();
    });
}

function showContextMenu(x, y, symbol) {
    const menu = document.getElementById('exchangesContextMenu');
    menu.dataset.symbol = symbol;
    
    // Показываем загрузку
    menu.innerHTML = `
        <div class="context-menu-header">📊 Торговые площадки: ${symbol}</div>
        <div class="context-menu-loading">Загрузка...</div>
    `;
    
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;
    menu.classList.add('active');
    
    // Загружаем данные
    fetch(`/api/exchanges/${symbol}/`)
        .then(res => res.json())
        .then(data => renderExchangesMenu(data, symbol))
        .catch(err => {
            menu.innerHTML = `
                <div class="context-menu-header">📊 Торговые площадки: ${symbol}</div>
                <div class="context-menu-error">Ошибка загрузки</div>
            `;
        });
}

function renderExchangesMenu(data, symbol) {
    const menu = document.getElementById('exchangesContextMenu');
    if (menu.dataset.symbol !== symbol) return; // Уже закрыли/сменили
    
    const exchanges = [
        { key: 'binance', name: 'Binance', color: '#F0B90B' },
        { key: 'bybit', name: 'Bybit', color: '#FFCD00' },
        { key: 'okx', name: 'OKX', color: '#0BE7A7' },
        { key: 'gate', name: 'Gate.io', color: '#00C6FF' },
        { key: 'mexc', name: 'MEXC', color: '#00D47E' },
        { key: 'bitget', name: 'Bitget', color: '#F58220' },
    ];
    
    let html = `<div class="context-menu-header">📊 Торговые площадки: ${symbol}</div>`;
    
    exchanges.forEach(ex => {
        const exData = data[ex.key] || { futures: false, spot: false };
        const hasFutures = exData.futures;
        const hasSpot = exData.spot;
        const hasAny = hasFutures || hasSpot;
        
        html += `
            <div class="context-menu-exchange ${hasAny ? '' : 'unavailable'}" 
                 style="--exchange-color: ${ex.color};">
                <div class="exchange-name">${ex.name}</div>
                <div class="exchange-markets">
                    <span class="market-tag ${hasFutures ? 'active' : ''}" title="Фьючерсы">⚡ Ф</span>
                    <span class="market-tag ${hasSpot ? 'active' : ''}" title="Спот">🔸 С</span>
                </div>
            </div>
        `;
    });
    
    menu.innerHTML = html;
}

function hideContextMenu() {
    const menu = document.getElementById('exchangesContextMenu');
    menu.classList.remove('active');
    contextMenuSymbol = null;
}

// Инициализация при загрузке
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initContextMenu);
} else {
    initContextMenu();
}