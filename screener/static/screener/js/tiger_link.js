// ==========================================
// tiger_link.js — ЛИНКОВКА С ТЕРМИНАЛОМ TIGER
// WebSocket локальный сервер терминала (порт 7819)
// ==========================================

const TIGER_WS_URL = 'ws://127.0.0.1:7819';
const TIGER_PING_INTERVAL = 20000;

let tigerSocket = null;
let tigerConnected = false;
let tigerReconnectTimer = null;
let tigerPingTimer = null;

function tigerIsEnabled() {
    try {
        const v = localStorage.getItem('tiger_link_enabled');
        if (v === null) return true;
        return v === '1';
    } catch (e) { return true; }
}

function tigerSetEnabled(v) {
    try { localStorage.setItem('tiger_link_enabled', v ? '1' : '0'); } catch (e) {}
}
function tigerGetLastGroup() {
    try { return localStorage.getItem('tiger_last_link_group') || 'A'; } catch (e) { return 'A'; }
}

function tigerSetLastGroup(group) {
    try { localStorage.setItem('tiger_last_link_group', group); } catch (e) {}
}

function tigerConnect() {
    if (!tigerIsEnabled()) return;
    if (tigerSocket && (tigerSocket.readyState === WebSocket.OPEN || tigerSocket.readyState === WebSocket.CONNECTING)) {
        return;
    }
    try {
        tigerSocket = new WebSocket(TIGER_WS_URL);
    } catch (e) {
        scheduleTigerReconnect();
        return;
    }

    tigerSocket.onopen = () => {
        tigerConnected = true;
        if (tigerPingTimer) clearInterval(tigerPingTimer);
        tigerPingTimer = setInterval(() => {
            if (tigerSocket && tigerSocket.readyState === WebSocket.OPEN) {
                try { tigerSocket.send('ping'); } catch (e) {}
            }
        }, TIGER_PING_INTERVAL);
    };

    tigerSocket.onmessage = (e) => {
        // Ответы терминала; можно логировать при необходимости
    };

    tigerSocket.onclose = () => {
        tigerConnected = false;
        if (tigerPingTimer) { clearInterval(tigerPingTimer); tigerPingTimer = null; }
        scheduleTigerReconnect();
    };

    tigerSocket.onerror = () => {
        try { tigerSocket.close(); } catch (e) {}
    };
}

function scheduleTigerReconnect() {
    if (tigerReconnectTimer) return;
    tigerReconnectTimer = setTimeout(() => {
        tigerReconnectTimer = null;
        tigerConnect();
    }, 3000);
}

function tigerSend(payload) {
    if (!tigerSocket || tigerSocket.readyState !== WebSocket.OPEN) {
        tigerConnect();
        // попытка отправить после подключения
        setTimeout(() => tigerSend(payload), 500);
        return false;
    }
    try {
        tigerSocket.send(JSON.stringify(payload));
        return true;
    } catch (e) {
        return false;
    }
}

function tigerLinkSymbol(symbol, exchange, market, linkGroup) {
    const group = linkGroup || tigerGetLastGroup();
    tigerSetLastGroup(group);
    const payload = {
        type: 'setLinkSymbol',
        e: exchange,
        m: market === 'futures' ? 'FUTURES' : 'SPOT',
        symbol: String(symbol).toUpperCase(),
        linkGroup: group
    };
    return tigerSend(payload);
}

const tigerExchangeMap = {
    'binance': 'BINANCE', 'bybit': 'BYBIT', 'okx': 'OKX', 'gate': 'Gate.io',
    'mexc': 'MEXC', 'bitget': 'Bitget', 'binance_alpha': 'BINANCE'
};

function tigerSendFromMenu(symbol, exchangeKey, market) {
    const groupSelect = document.getElementById('tigerGroupSelect');
    const group = groupSelect ? groupSelect.value : tigerGetLastGroup();
    tigerSetLastGroup(group);
    // Если скринер открыт внутри локального моста (iframe), отправляем команду родителю
    if (window.parent && window.parent !== window) {
        try {
            window.parent.postMessage({ type: 'tigerLink', symbol: symbol, exchangeKey: exchangeKey, market: market, group: group }, '*');
        } catch (e) {}
        return;
    }
    const exchange = tigerExchangeMap[exchangeKey] || 'BINANCE';
    tigerLinkSymbol(symbol, exchange, market, group);
}

window.tigerIsEnabled = tigerIsEnabled;
window.tigerSetEnabled = tigerSetEnabled;
window.tigerLinkSymbol = tigerLinkSymbol;
window.tigerIsConnected = () => tigerConnected;
window.tigerSendFromMenu = tigerSendFromMenu;

// Сохраняем выбранную группу при изменении select
document.addEventListener('change', (e) => {
    if (e.target && e.target.id === 'tigerGroupSelect') {
        tigerSetLastGroup(e.target.value);
    }
});

// Синхронизация чекбокса в настройках
document.addEventListener('change', (e) => {
    if (e.target && e.target.id === 'tigerLinkEnabled') {
        tigerSetEnabled(e.target.checked);
        if (e.target.checked) tigerConnect();
    }
});

// Установка состояния чекбокса при загрузке
document.addEventListener('DOMContentLoaded', () => {
    const cb = document.getElementById('tigerLinkEnabled');
    if (cb) cb.checked = tigerIsEnabled();
});
if (tigerIsEnabled()) {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', tigerConnect);
    } else {
        tigerConnect();
    }
}
