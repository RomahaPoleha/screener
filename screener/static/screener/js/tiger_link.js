// ==========================================
// tiger_link.js — ЛИНКОВКА С ТЕРМИНАЛОМ TIGER
// WebSocket локальный сервер терминала (порт 7819)
// При наличии расширения "Tiger Screener Bridge" — через расширение (работает и на HTTPS)
// ==========================================

const TIGER_WS_URL = 'ws://127.0.0.1:7819/';
const TIGER_PING_INTERVAL = 20000;

let tigerSocket = null;
let tigerConnected = false;
let tigerReconnectTimer = null;
let tigerPingTimer = null;
let useExtension = !!window.__TIGER_EXT__;
let extReadyResolve = null;
const extReadyPromise = new Promise((res) => { extReadyResolve = res; });
if (useExtension) extReadyResolve();
window.addEventListener('tiger-ext-ready', () => { useExtension = true; if (extReadyResolve) extReadyResolve(); });

// --- Extension backend ---
const extPending = new Map();

function extConnect() {
  // статус придёт через postMessage
  window.postMessage({ source: 'screener', kind: 'status' }, '*');
}

function extSend(payload) {
  const requestId = 'r-' + Date.now() + '-' + Math.random().toString(36).slice(2);
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      extPending.delete(requestId);
      resolve(null);
    }, 10000);
    extPending.set(requestId, { resolve: (data) => { clearTimeout(timer); extPending.delete(requestId); resolve(data); } });
    window.postMessage({ source: 'screener', kind: 'cmd', payload, requestId }, '*');
  });
}

window.addEventListener('message', (e) => {
  if (e.source !== window) return;
  const d = e.data;
  if (!d || d.source !== 'tiger-ext') return;
  if (d.kind === 'status') {
    tigerConnected = !!d.connected;
    // notify UI if needed
  } else if (d.kind === 'resp') {
    const entry = extPending.get(d.requestId);
    if (entry) entry.resolve(d.data);
  } else if (d.kind === 'push') {
    // игнорируем пуши для линковки
  }
});

// --- Direct WebSocket backend ---
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

function directConnect() {
  if (!tigerIsEnabled()) return;
  if (tigerSocket && (tigerSocket.readyState === WebSocket.OPEN || tigerSocket.readyState === WebSocket.CONNECTING)) return;
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
    if (e.data === 'pong') return;
    try {
      const msg = JSON.parse(e.data);
      if (msg.response && msg.response.type === 'error') {
        console.warn('[Tiger] error:', msg.response.error && msg.response.error.message);
      }
    } catch (err) {}
  };
  tigerSocket.onclose = () => {
    tigerConnected = false;
    if (tigerPingTimer) { clearInterval(tigerPingTimer); tigerPingTimer = null; }
    scheduleTigerReconnect();
  };
  tigerSocket.onerror = (e) => {
    try { tigerSocket.close(); } catch (e) {}
  };
}

function scheduleTigerReconnect() {
  if (tigerReconnectTimer) return;
  tigerReconnectTimer = setTimeout(() => {
    tigerReconnectTimer = null;
    connect();
  }, 3000);
}

function directSend(payload) {
  if (!tigerSocket || tigerSocket.readyState !== WebSocket.OPEN) {
    directConnect();
    setTimeout(() => directSend(payload), 500);
    return false;
  }
  try {
    tigerSocket.send(JSON.stringify(payload));
    return true;
  } catch (e) {
    return false;
  }
}

// --- Unified API ---
function connect() {
  if (useExtension) {
    extConnect();
  } else {
    directConnect();
  }
}

function send(payload) {
  if (useExtension) {
    return extSend(payload).then((data) => {
      if (data && data.response && data.response.type === 'error') {
        console.warn('[Tiger] error:', data.response.error && data.response.error.message);
      } else if (data) {
        console.log('[Tiger] ok:', (data.response && data.response.title) || 'ok');
      }
      return true;
    });
  }
  return directSend(payload);
}

function tigerLinkSymbol(symbol, exchange, market, linkGroup) {
  const group = linkGroup || tigerGetLastGroup();
  tigerSetLastGroup(group);
  let sym = String(symbol).trim().toUpperCase();
  if (!/USDT$/i.test(sym)) sym = sym + 'USDT';
  const payload = {
    type: 'setLinkSymbol',
    e: exchange,
    m: market === 'futures' ? 'FUTURES' : 'SPOT',
    symbol: sym,
    linkGroup: group
  };
  console.log('[Tiger] send', payload);
  return send(payload);
}

const tigerExchangeMap = {
  'binance': 'BINANCE', 'bybit': 'BYBIT', 'okx': 'OKX', 'gate': 'Gate.io',
  'mexc': 'MEXC', 'bitget': 'Bitget', 'binance_alpha': 'BINANCE-ALPHA'
};

function tigerSendFromMenu(symbol, exchangeKey, market) {
  const groupSelect = document.getElementById('tigerGroupSelect');
  const group = groupSelect ? groupSelect.value : tigerGetLastGroup();
  tigerSetLastGroup(group);
  if (window.parent && window.parent !== window) {
    try {
      window.parent.postMessage({ type: 'tigerLink', symbol: symbol, exchangeKey: exchangeKey, market: market, group: group }, '*');
    } catch (e) {}
    return;
  }
  const exchange = (exchangeKey === 'binance')
    ? (market === 'futures' ? 'BINANCE-FUT' : 'BINANCE')
    : (exchangeKey === 'binance_alpha' ? 'BINANCE-ALPHA' : (tigerExchangeMap[exchangeKey] || 'BINANCE'));
  tigerLinkSymbol(symbol, exchange, market, group);
}

window.tigerIsEnabled = tigerIsEnabled;
window.tigerSetEnabled = tigerSetEnabled;
window.tigerLinkSymbol = tigerLinkSymbol;
window.tigerIsConnected = () => tigerConnected;
window.tigerSendFromMenu = tigerSendFromMenu;

document.addEventListener('change', (e) => {
  if (e.target && e.target.id === 'tigerGroupSelect') tigerSetLastGroup(e.target.value);
});
document.addEventListener('change', (e) => {
  if (e.target && e.target.id === 'tigerLinkEnabled') {
    tigerSetEnabled(e.target.checked);
    if (e.target.checked) connect();
  }
});
document.addEventListener('DOMContentLoaded', () => {
  const cb = document.getElementById('tigerLinkEnabled');
  if (cb) cb.checked = tigerIsEnabled();
});
if (tigerIsEnabled()) {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', connect);
  } else {
    connect();
  }
}
// если расширение загрузится позже — переключимся
window.addEventListener('tiger-ext-ready', () => {
  useExtension = true;
  connect();
});
