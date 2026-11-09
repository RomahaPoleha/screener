// ==========================================
// tiger_link.js — ЛИНКОВКА С ТЕРМИНАЛОМ TIGER
// WebSocket локальный сервер терминала (порт 7819)
// ==========================================

const TIGER_WS_URL = 'ws://127.0.0.1:7819/';
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

// --- Auto-link on impulse ---
const TIGER_AUTO_LINK_GROUPS_KEY = 'tiger_auto_link_groups';
const TIGER_AUTO_LINK_ENABLED_KEY = 'tiger_auto_link_enabled';
const TIGER_AUTO_LINK_INDEX_KEY = 'tiger_auto_link_index';
const TIGER_AUTO_LINK_GROUP_COOLDOWN_KEY = 'tiger_auto_link_group_cooldown_ms';
const TIGER_AUTO_LINK_GROUP_LAST_USED_KEY = 'tiger_auto_link_group_last_used';
const TIGER_AUTO_LINK_GLOBAL_COOLDOWN_MS = 1000; // глобальный кулдаун 1 сек
let tigerAutoLinkLastTs = 0;

function tigerIsAutoLinkEnabled() {
  try { return localStorage.getItem(TIGER_AUTO_LINK_ENABLED_KEY) === '1'; } catch (e) { return false; }
}
function tigerSetAutoLinkEnabled(v) {
  try { localStorage.setItem(TIGER_AUTO_LINK_ENABLED_KEY, v ? '1' : '0'); } catch (e) {}
}
function tigerGetAutoLinkGroups() {
  try {
    const raw = localStorage.getItem(TIGER_AUTO_LINK_GROUPS_KEY);
    if (!raw) return ['A'];
    const arr = JSON.parse(raw);
    if (Array.isArray(arr) && arr.length) return arr.slice(0, 5);
  } catch (e) {}
  return ['A'];
}
function tigerSetAutoLinkGroups(arr) {
  try { localStorage.setItem(TIGER_AUTO_LINK_GROUPS_KEY, JSON.stringify((arr || []).slice(0, 5))); } catch (e) {}
}
function tigerGetGroupCooldownMs() {
  try {
    const v = parseInt(localStorage.getItem(TIGER_AUTO_LINK_GROUP_COOLDOWN_KEY), 10);
    if (!isNaN(v) && v >= 1000 && v <= 180000) return v;
  } catch (e) {}
  return 60000; // дефолт 60 сек
}
function tigerSetGroupCooldownMs(ms) {
  try { localStorage.setItem(TIGER_AUTO_LINK_GROUP_COOLDOWN_KEY, String(Math.max(1000, Math.min(180000, ms)))); } catch (e) {}
}

function tigerGetGroupLastUsed() {
  try {
    const raw = localStorage.getItem(TIGER_AUTO_LINK_GROUP_LAST_USED_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (e) { return {}; }
}
function tigerSetGroupLastUsed(group, ts) {
  try {
    const map = tigerGetGroupLastUsed();
    map[group] = ts;
    localStorage.setItem(TIGER_AUTO_LINK_GROUP_LAST_USED_KEY, JSON.stringify(map));
  } catch (e) {}
}

function tigerAutoLink(symbol) {
  if (!tigerIsEnabled() || !tigerIsConnected() || !tigerIsAutoLinkEnabled()) return;
  const now = Date.now();
  if (now - tigerAutoLinkLastTs < TIGER_AUTO_LINK_GLOBAL_COOLDOWN_MS) return;
  const groups = tigerGetAutoLinkGroups();
  if (!groups.length) return;
  const cooldownMs = tigerGetGroupCooldownMs();
  const lastUsed = tigerGetGroupLastUsed();
  let idx = parseInt(localStorage.getItem(TIGER_AUTO_LINK_INDEX_KEY) || '0', 10);
  if (isNaN(idx)) idx = 0;
  const startIdx = idx;
  let found = false;
  for (let i = 0; i < groups.length; i++) {
    const checkIdx = (startIdx + i) % groups.length;
    const group = groups[checkIdx];
    const last = lastUsed[group] || 0;
    if (now - last >= cooldownMs) {
      // группа свободна
      localStorage.setItem(TIGER_AUTO_LINK_INDEX_KEY, String((checkIdx + 1) % groups.length));
      tigerAutoLinkLastTs = now;
      tigerSetGroupLastUsed(group, now);
      tigerLinkSymbol(symbol, 'BINANCE-FUT', 'futures', group);
      found = true;
      break;
    }
  }
  if (!found) {
    // все группы в кулдауне — пропускаем этот импульс
    console.log('[Tiger] все группы в кулдауне, авто-линковка пропущена');
  }
}

function tigerConnect() {
  if (!tigerIsEnabled()) return;
  if (tigerSocket && (tigerSocket.readyState === WebSocket.OPEN || tigerSocket.readyState === WebSocket.CONNECTING)) return;
  try {
    tigerSocket = new WebSocket(TIGER_WS_URL);
    console.log('[Tiger] connecting to', TIGER_WS_URL);
  } catch (e) {
    console.error('[Tiger] WebSocket constructor error:', e);
    scheduleTigerReconnect();
    return;
  }

  tigerSocket.onopen = () => {
    tigerConnected = true;
    console.log('[Tiger] connected');
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
      } else if (msg.response) {
        console.log('[Tiger] ok:', msg.title || msg.response.type);
      }
    } catch (err) {
      console.log('[Tiger] raw:', e.data);
    }
  };

  tigerSocket.onclose = () => {
    tigerConnected = false;
    if (tigerPingTimer) { clearInterval(tigerPingTimer); tigerPingTimer = null; }
    scheduleTigerReconnect();
  };

  tigerSocket.onerror = (e) => {
    console.error('[Tiger] WebSocket error', e);
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
  console.log('[Tiger] tigerSend', payload);
  if (!tigerSocket || tigerSocket.readyState !== WebSocket.OPEN) {
    tigerConnect();
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
  let sym = String(symbol).trim().toUpperCase();
  if (!/USDT$/i.test(sym)) sym = sym + 'USDT';
  const payload = {
    type: 'setLinkSymbol',
    e: exchange,
    m: market === 'futures' ? 'FUTURES' : 'SPOT',
    symbol: sym,
    linkGroup: group
  };
  return tigerSend(payload);
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
window.tigerIsAutoLinkEnabled = tigerIsAutoLinkEnabled;
window.tigerSetAutoLinkEnabled = tigerSetAutoLinkEnabled;
window.tigerGetAutoLinkGroups = tigerGetAutoLinkGroups;
window.tigerSetAutoLinkGroups = tigerSetAutoLinkGroups;
window.tigerAutoLink = tigerAutoLink;
window.tigerGetGroupCooldownMs = tigerGetGroupCooldownMs;
window.tigerSetGroupCooldownMs = tigerSetGroupCooldownMs;
window.tigerGetGroupLastUsed = tigerGetGroupLastUsed;

document.addEventListener('change', (e) => {
  if (e.target && e.target.id === 'tigerGroupSelect') tigerSetLastGroup(e.target.value);
});
document.addEventListener('change', (e) => {
  if (e.target && e.target.id === 'tigerLinkEnabled') {
    tigerSetEnabled(e.target.checked);
    if (e.target.checked) tigerConnect();
  }
});
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
