// ==========================================
// context_menu.js — КОНТЕКСТНОЕ МЕНЮ БИРЖ
// Shift + наведение на монету -> "Торговые площадки"
// ==========================================

let contextMenuSymbol = null;
let hoverTimeout = null;
let isShiftPressed = false;

function initContextMenu() {
    // Отслеживаем Shift
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Shift') isShiftPressed = true;
    });
    document.addEventListener('keyup', (e) => {
        if (e.key === 'Shift') {
            isShiftPressed = false;
            hideContextMenu();
        }
    });
    
    // Закрытие по Escape
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') hideContextMenu();
    });
    
    // Делегирование для строк таблицы
    document.addEventListener('mouseover', (e) => {
        const row = e.target.closest('.coin-row');
        if (!row) return;
        
        if (isShiftPressed) {
            clearTimeout(hoverTimeout);
            hoverTimeout = setTimeout(() => {
                contextMenuSymbol = row.dataset.symbol;
                const rect = row.getBoundingClientRect();
                showContextMenu(rect.right + 8, rect.top, contextMenuSymbol);
            }, 150); // задержка против дрожания
        }
    });
    
    document.addEventListener('mouseout', (e) => {
        const row = e.target.closest('.coin-row');
        if (!row) return;
        clearTimeout(hoverTimeout);
        // Скрываем с задержкой, чтобы успеть перевести мышь на меню
        hoverTimeout = setTimeout(() => {
            if (!isMenuHovered()) hideContextMenu();
        }, 200);
    });
    
    // Не скрывать если мышь на меню
    document.addEventListener('mouseover', (e) => {
        if (e.target.closest('#exchangesContextMenu')) {
            clearTimeout(hoverTimeout);
        }
    });
}

function isMenuHovered() {
    return document.querySelector('#exchangesContextMenu:hover') !== null;
}

function showContextMenu(x, y, symbol) {
    const menu = document.getElementById('exchangesContextMenu');
    menu.dataset.symbol = symbol;
    
    menu.innerHTML = `
        <div class="context-menu-header">📊 Торговые площадки: ${symbol}</div>
        <div class="context-menu-loading">Загрузка...</div>
    `;
    
    // Ограничиваем чтобы не выходило за экран
    const menuWidth = 220;
    const menuHeight = 300;
    if (x + menuWidth > window.innerWidth) x = window.innerWidth - menuWidth - 8;
    if (y + menuHeight > window.innerHeight) y = window.innerHeight - menuHeight - 8;
    
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;
    menu.classList.add('active');
    
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
    if (menu.dataset.symbol !== symbol) return;
    
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
    clearTimeout(hoverTimeout);
}

// Инициализация
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initContextMenu);
} else {
    initContextMenu();
}