// ==========================================
// context_menu.js — КОНТЕКСТНОЕ МЕНЮ БИРЖ
// Shift + наведение на монету -> "Торговые площадки"
// ==========================================

let contextMenuSymbol = null;
let hoverTimeout = null;
let isShiftPressed = false;

function initContextMenu() {
    // Правый клик как запасной способ открытия (не зависит от Shift/браузера)
    document.addEventListener('contextmenu', (e) => {
        const row = e.target.closest('.coin-row');
        if (!row) return;
        e.preventDefault();
        contextMenuSymbol = row.dataset.symbol;
        const rect = row.getBoundingClientRect();
        showContextMenu(rect.right + 8, rect.top, contextMenuSymbol);
    });
    
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
    const menu = document.querySelector('#exchangesContextMenu:hover');
    return menu !== null;
}

function showContextMenu(x, y, symbol) {
    const menu = document.getElementById('exchangesContextMenu');
    if (!menu) return;
    menu.dataset.symbol = symbol;
    
    menu.innerHTML = `
        <div class="context-menu-header" style="color:#888;font-weight:500;font-size:12px;padding:8px 12px;border-bottom:1px solid #2a2a2a;">Торговые площадки: ${symbol}</div>
        <div class="context-menu-loading" style="color:#666;font-size:12px;padding:8px 12px;">Загрузка...</div>
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
                <div class="context-menu-header" style="color:#888;font-weight:500;font-size:12px;padding:8px 12px;border-bottom:1px solid #2a2a2a;">Торговые площадки: ${symbol}</div>
                <div class="context-menu-error" style="color:#666;font-size:12px;padding:8px 12px;">Ошибка загрузки</div>
            `;
        });
}

function renderExchangesMenu(data, symbol) {
    const menu = document.getElementById('exchangesContextMenu');
    if (!menu) return;
    if (menu.dataset.symbol !== symbol) return;
    
    const exchanges = [
        { key: 'binance', name: 'Binance', color: '#F0B90B' },
        { key: 'bybit', name: 'Bybit', color: '#FFCD00' },
        { key: 'okx', name: 'OKX', color: '#0BE7A7' },
        { key: 'gate', name: 'Gate.io', color: '#00C6FF' },
        { key: 'mexc', name: 'MEXC', color: '#00D47E' },
        { key: 'bitget', name: 'Bitget', color: '#F58220' },
        { key: 'binance_alpha', name: 'Binance Alpha', color: '#8b5cf6' },
    ];
    
    const tigerOn = (typeof tigerIsEnabled === 'function') ? tigerIsEnabled() : true;

    const tigerExchanges = {
        'binance': 'BINANCE', 'bybit': 'BYBIT', 'okx': 'OKX', 'gate': 'Gate.io',
        'mexc': 'MEXC', 'bitget': 'Bitget', 'binance_alpha': 'BINANCE'
    };
    const lastGroup = (typeof tigerGetLastGroup === 'function') ? tigerGetLastGroup() : 'A';
    const groupOptions = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('').map(g =>
        `<option value="${g}" ${g === lastGroup ? 'selected' : ''}>${g}</option>`
    ).join('') + Array.from({length:12}, (_,i) => i).map(n =>
        `<option value="${n}" ${String(n) === lastGroup ? 'selected' : ''}>${n}</option>`
    ).join('');

    let headerHtml;
    if (tigerOn) {
        const isHttps = (location.protocol === 'https:');
        const connected = (typeof tigerIsConnected === 'function') ? tigerIsConnected() : false;
        const statusText = isHttps ? 'нужен HTTP' : (connected ? 'онлайн' : 'офлайн');
        const statusColor = isHttps ? '#f59e0b' : (connected ? '#22c55e' : '#ef4444');
        headerHtml = `
        <div class="context-menu-header" style="color:#888;font-weight:500;font-size:12px;padding:8px 12px;border-bottom:1px solid #2a2a2a;display:flex;align-items:center;justify-content:space-between;gap:8px;">
            <span>Торговые площадки: ${symbol}</span>
            <span style="display:flex;align-items:center;gap:4px;font-size:11px;color:${statusColor};">
                Tiger: ${statusText}
                <span style="display:flex;align-items:center;gap:4px;color:#aaa;margin-left:6px;">grp:
                <select id="tigerGroupSelect" style="background:#222;color:#fff;border:1px solid #444;border-radius:3px;padding:1px 4px;font-size:11px;">
                    ${groupOptions}
                </select>
                </span>
            </span>
        </div>
        `;
    } else {
        headerHtml = `<div class="context-menu-header" style="color:#888;font-weight:500;font-size:12px;padding:8px 12px;border-bottom:1px solid #2a2a2a;">Торговые площадки: ${symbol}</div>`;
    }

    let html = headerHtml;
    
    exchanges.forEach(ex => {
        const exData = data[ex.key] || { futures: false, spot: false };
    const isAlpha = ex.key === 'binance_alpha';
        const hasFutures = exData.futures;
        const hasSpot = exData.spot;
        const hasAny = hasFutures || hasSpot;
        
        let marketTags = '';
        if (isAlpha) {
            if (tigerOn) {
                marketTags = `<span class="market-tag ${hasFutures ? 'active' : ''}" title="Открыть в Tiger (Spot)" style="background:${hasFutures ? 'rgba(139,92,246,0.2)' : 'rgba(255,255,255,0.05)'};color:${hasFutures ? '#8b5cf6' : '#666'};border:1px solid ${hasFutures ? '#8b5cf6' : '#333'};cursor:${hasFutures ? 'pointer' : 'default'};" ${hasFutures ? `onclick="event.stopPropagation(); tigerSendFromMenu('${symbol}','${ex.key}','spot')"` : ''}>S</span>`;
            } else {
                marketTags = `<span class="market-tag ${hasFutures ? 'active' : ''}" title="Spot" style="background:${hasFutures ? 'rgba(139,92,246,0.2)' : 'rgba(255,255,255,0.05)'};color:${hasFutures ? '#8b5cf6' : '#666'};border:1px solid ${hasFutures ? '#8b5cf6' : '#333'};">S</span>`;
            }
        } else {
            if (tigerOn) {
                marketTags = `
                    <span class="market-tag ${hasFutures ? 'active' : ''}" title="Открыть в Tiger (Futures)" style="background:${hasFutures ? 'rgba(255,205,0,0.2)' : 'rgba(255,255,255,0.05)'};color:${hasFutures ? '#FFCD00' : '#666'};border:1px solid ${hasFutures ? '#FFCD00' : '#333'};cursor:${hasFutures ? 'pointer' : 'default'};" ${hasFutures ? `onclick="event.stopPropagation(); tigerSendFromMenu('${symbol}','${ex.key}','futures')"` : ''}>F</span>
                    <span class="market-tag ${hasSpot ? 'active' : ''}" title="Открыть в Tiger (Spot)" style="background:${hasSpot ? 'rgba(0,198,255,0.2)' : 'rgba(255,255,255,0.05)'};color:${hasSpot ? '#00C6FF' : '#666'};border:1px solid ${hasSpot ? '#00C6FF' : '#333'};cursor:${hasSpot ? 'pointer' : 'default'};" ${hasSpot ? `onclick="event.stopPropagation(); tigerSendFromMenu('${symbol}','${ex.key}','spot')"` : ''}>S</span>
                `;
            } else {
                marketTags = `
                    <span class="market-tag ${hasFutures ? 'active' : ''}" title="Futures" style="background:${hasFutures ? 'rgba(255,205,0,0.2)' : 'rgba(255,255,255,0.05)'};color:${hasFutures ? '#FFCD00' : '#666'};border:1px solid ${hasFutures ? '#FFCD00' : '#333'};">F</span>
                    <span class="market-tag ${hasSpot ? 'active' : ''}" title="Spot" style="background:${hasSpot ? 'rgba(0,198,255,0.2)' : 'rgba(255,255,255,0.05)'};color:${hasSpot ? '#00C6FF' : '#666'};border:1px solid ${hasSpot ? '#00C6FF' : '#333'};">S</span>
                `;
            }
        }
        
        html += `
            <div class="context-menu-exchange ${hasAny ? '' : 'unavailable'}" 
                 style="--exchange-color: ${ex.color};">
                <div class="exchange-name" style="color:#ccc;font-size:13px;font-weight:500;">${ex.name}</div>
                <div class="exchange-markets">
                    ${marketTags}
                </div>
            </div>
        `;
    });
    
    menu.innerHTML = html;
}

function hideContextMenu() {
    const menu = document.getElementById('exchangesContextMenu');
    if (!menu) return;
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