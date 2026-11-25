// ==========================================
// tiger_chart_link.js — ЛИНКОВКА В TIGER ИЗ ГРАФИКА
// ==========================================

let tigerLinkPopup = null;

function openTigerLinkPopup() {
    if (!currentSymbol) return;
    const symbol = currentSymbol;

    // Закрыть предыдущий popup
    if (tigerLinkPopup) { tigerLinkPopup.remove(); tigerLinkPopup = null; }

    // Создать popup
    const popup = document.createElement('div');
    popup.id = 'tigerLinkPopup';
    popup.style.cssText = 'position:fixed; z-index:9999; background:var(--bg-glass); border:1px solid var(--border-soft); border-radius:8px; padding:12px; min-width:280px; max-width:400px; box-shadow:0 4px 20px rgba(0,0,0,0.5); backdrop-filter:blur(8px);';
    popup.innerHTML = '<div style="color:#888;font-size:12px;padding:4px 0;">Загрузка...</div>';

    // Позиционировать рядом с кнопкой
    const btn = document.getElementById('tigerLinkBtn');
    if (btn) {
        const rect = btn.getBoundingClientRect();
        popup.style.top = (rect.bottom + 8) + 'px';
        popup.style.left = Math.max(8, rect.left - 100) + 'px';
    } else {
        popup.style.top = '80px';
        popup.style.left = '50%';
        popup.style.transform = 'translateX(-50%)';
    }

    document.body.appendChild(popup);
    tigerLinkPopup = popup;

    // Закрытие по клику вне
    setTimeout(() => {
        document.addEventListener('click', closeTigerLinkPopupOnClick, { once: true });
    }, 100);

    // Загрузить данные бирж
    fetch(`/api/exchanges/${symbol}/`)
        .then(res => res.json())
        .then(data => renderTigerLinkPopup(data, symbol, popup))
        .catch(() => {
            popup.innerHTML = '<div style="color:#ef4444;font-size:12px;padding:4px 0;">Ошибка загрузки</div>';
        });
}

function renderTigerLinkPopup(data, symbol, popup) {
    const exchanges = [
        { key: 'binance', name: 'Binance', color: '#F0B90B' },
        { key: 'bybit', name: 'Bybit', color: '#FFCD00' },
        { key: 'okx', name: 'OKX', color: '#0BE7A7' },
        { key: 'gate', name: 'Gate.io', color: '#00C6FF' },
        { key: 'mexc', name: 'MEXC', color: '#00D47E' },
        { key: 'bitget', name: 'Bitget', color: '#F58220' },
        { key: 'binance_alpha', name: 'Binance Alpha', color: '#8b5cf6' },
    ];

    const inBridge = (window.parent && window.parent !== window);
    const tigerOn = inBridge ? true : ((typeof tigerIsEnabled === 'function') ? tigerIsEnabled() : true);

    let html = `<div style="color:#888;font-weight:500;font-size:12px;padding:4px 0 8px;border-bottom:1px solid #2a2a2a;margin-bottom:8px;">Линковка в Tiger: ${symbol}</div>`;

    exchanges.forEach(ex => {
        const exData = data[ex.key] || { futures: false, spot: false };
        const isAlpha = ex.key === 'binance_alpha';
        const hasFutures = exData.futures;
        const hasSpot = exData.spot;
        const hasAny = hasFutures || hasSpot;

        if (!hasAny) return;

        let tags = '';
        if (isAlpha) {
            if (hasFutures && tigerOn) {
                tags = `<span class="market-tag active" style="background:rgba(139,92,246,0.2);color:#8b5cf6;border:1px solid #8b5cf6;cursor:pointer;padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700;" onclick="event.stopPropagation(); tigerSendFromMenu('${symbol}','${ex.key}','spot'); closeTigerLinkPopup();">S</span>`;
            }
        } else {
            if (tigerOn) {
                if (hasFutures) tags += `<span class="market-tag active" style="background:rgba(255,205,0,0.2);color:#FFCD00;border:1px solid #FFCD00;cursor:pointer;padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700;margin-right:4px;" onclick="event.stopPropagation(); tigerSendFromMenu('${symbol}','${ex.key}','futures'); closeTigerLinkPopup();">F</span>`;
                if (hasSpot) tags += `<span class="market-tag active" style="background:rgba(0,198,255,0.2);color:#00C6FF;border:1px solid #00C6FF;cursor:pointer;padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700;" onclick="event.stopPropagation(); tigerSendFromMenu('${symbol}','${ex.key}','spot'); closeTigerLinkPopup();">S</span>`;
            } else {
                if (hasFutures) tags += `<span class="market-tag" style="background:rgba(255,255,255,0.05);color:#666;border:1px solid #333;padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700;margin-right:4px;">F</span>`;
                if (hasSpot) tags += `<span class="market-tag" style="background:rgba(255,255,255,0.05);color:#666;border:1px solid #333;padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700;">S</span>`;
            }
        }

        if (!tags) return;

        html += `
            <div style="display:flex;align-items:center;justify-content:space-between;padding:6px 0;border-bottom:1px solid #2a2a2a;">
                <span style="color:#ccc;font-size:12px;font-weight:500;">${ex.name}</span>
                <div style="display:flex;gap:4px;">${tags}</div>
            </div>
        `;
    });

    if (html === `<div style="color:#888;font-weight:500;font-size:12px;padding:4px 0 8px;border-bottom:1px solid #2a2a2a;margin-bottom:8px;">Линковка в Tiger: ${symbol}</div>`) {
        html += '<div style="color:#666;font-size:12px;padding:8px 0;">Нет доступных бирж</div>';
    }

    popup.innerHTML = html;
}

function closeTigerLinkPopup() {
    if (tigerLinkPopup) { tigerLinkPopup.remove(); tigerLinkPopup = null; }
    document.removeEventListener('click', closeTigerLinkPopupOnClick);
}

function closeTigerLinkPopupOnClick(e) {
    if (tigerLinkPopup && !tigerLinkPopup.contains(e.target) && e.target.id !== 'tigerLinkBtn') {
        closeTigerLinkPopup();
    }
}

// Скрыть popup при закрытии графика
const _origCloseChart = (typeof closeChart === 'function') ? closeChart : null;
if (_origCloseChart) {
    const _wrappedCloseChart = closeChart;
    // Don't override, just ensure popup is closed
}

// Hook into closeChart to close popup
document.addEventListener('DOMContentLoaded', () => {
    const origCloseChart = window.closeChart;
    if (typeof origCloseChart === 'function') {
        window.closeChart = function() {
            closeTigerLinkPopup();
            origCloseChart();
        };
    }
});
PYEOF