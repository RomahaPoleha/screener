// ==========================================
// context_menu.js — КОНТЕКСТНОЕ МЕНЮ БИРЖ
// Shift + hover on coin -> "Exchanges"
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
        <div class="context-menu-header" style="color:#888;font-weight:500;font-size:12px;padding:8px 12px;border-bottom:1px solid #2a2a2a;">Exchanges: ${symbol}</div>
        <div class="context-menu-loading" style="color:#666;font-size:12px;padding:8px 12px;">Loading...</div>
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
                <div class="context-menu-header" style="color:#888;font-weight:500;font-size:12px;padding:8px 12px;border-bottom:1px solid #2a2a2a;">Exchanges: ${symbol}</div>
                <div class="context-menu-error" style="color:#666;font-size:12px;padding:8px 12px;">Load error</div>
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
        { key: 'binance_alpha', name: 'Block A', color: '#8b5cf6' },
    ];
    
    let html = `<div class="context-menu-header" style="color:#888;font-weight:500;font-size:12px;padding:8px 12px;border-bottom:1px solid #2a2a2a;">Exchanges: ${symbol}</div>`;
    
    exchanges.forEach(ex => {
        const exData = data[ex.key] || { futures: false, spot: false };
        const hasFutures = exData.futures;
        const hasSpot = exData.spot;
        const hasAny = hasFutures || hasSpot;
        
        html += `
            <div class="context-menu-exchange ${hasAny ? '' : 'unavailable'}" 
                 style="--exchange-color: ${ex.color};">
                <div class="exchange-name" style="color:#ccc;font-size:13px;font-weight:500;">${ex.name}</div>
                <div class="exchange-markets">
                    <span class="market-tag ${hasFutures ? 'active' : ''}" title="Futures" style="background:${hasFutures ? 'rgba(255,205,0,0.2)' : 'rgba(255,255,255,0.05)'};color:${hasFutures ? '#FFCD00' : '#666'};border:1px solid ${hasFutures ? '#FFCD00' : '#333'};">F</span>
                    <span class="market-tag ${hasSpot ? 'active' : ''}" title="Spot" style="background:${hasSpot ? 'rgba(0,198,255,0.2)' : 'rgba(255,255,255,0.05)'};color:${hasSpot ? '#00C6FF' : '#666'};border:1px solid ${hasSpot ? '#00C6FF' : '#333'};">S</span>
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