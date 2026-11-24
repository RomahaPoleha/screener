// ==========================================
// impulse_ws.js — WebSocket + POLLING FALLBACK
// ==========================================

window.impulsePollingEnabled = false;
let impulsePollingTimer = null;
let impulseWs = null;
let impulseWsReconnectTimer = null;
let impulseLastTimestamp = parseFloat(localStorage.getItem('impulseLastTimestamp') || '0');
const seenImpulses = new Set();

function getImpulseWsUrl() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${location.host}/ws/impulses/`;
}

function startImpulseWebSocket() {
    if (window.impulsePollingEnabled) return;
    window.impulsePollingEnabled = true;
    console.log('✅ Impulse: WebSocket подключение...');
    connectImpulseWs();
}

function connectImpulseWs() {
    if (impulseWs && (impulseWs.readyState === WebSocket.OPEN || impulseWs.readyState === WebSocket.CONNECTING)) return;
    try {
        impulseWs = new WebSocket(getImpulseWsUrl());
    } catch (e) {
        console.warn('Impulse WS creation error:', e);
        startPollingFallback();
        return;
    }

    impulseWs.onopen = () => {
        console.log('✅ Impulse WS подключен');
        // При подключении — запросить пропущенные импульсы через polling
        pollImpulseAlerts();
    };

    impulseWs.onmessage = (e) => {
        try {
            const alert = JSON.parse(e.data);
            handleImpulseAlert(alert);
        } catch (err) {
            console.warn('Impulse WS parse error:', err);
        }
    };

    impulseWs.onclose = () => {
        console.warn('Impulse WS закрыт, reconnect через 3с');
        impulseWs = null;
        if (window.impulsePollingEnabled) {
            // Fallback на polling пока WS не восстановится
            startPollingFallback();
            impulseWsReconnectTimer = setTimeout(connectImpulseWs, 3000);
        }
    };

    impulseWs.onerror = (e) => {
        console.warn('Impulse WS ошибка:', e);
        if (impulseWs) { try { impulseWs.close(); } catch (err) {} }
    };
}

function startPollingFallback() {
    if (impulsePollingTimer) return;
    console.log('📡 Impulse: polling fallback запущен');
    pollImpulseAlerts();
    impulsePollingTimer = setInterval(pollImpulseAlerts, 2000);
}

function stopPollingFallback() {
    if (impulsePollingTimer) {
        clearInterval(impulsePollingTimer);
        impulsePollingTimer = null;
    }
}

function handleImpulseAlert(alert) {
    const key = `${alert.symbol}_${alert.window}_${alert.timestamp}`;
    if (seenImpulses.has(key)) return;
    seenImpulses.add(key);

    const userThreshold = parseFloat(localStorage.getItem('priceImpulseThreshold') || '1.0');
    const userWindow = parseInt(localStorage.getItem('priceImpulseWindow') || '60');

    if (alert.window !== userWindow) return;
    if (alert.price_change < userThreshold) return;

    const direction = alert.direction === 'up' ? '↑' : '↓';

    showVolumeAlertToast(
        alert.symbol,
        alert.volume || 0,
        direction,
        alert.price_change
    );

    if (typeof tigerAutoLink === 'function' && typeof tigerIsAutoLinkEnabled === 'function' && tigerIsAutoLinkEnabled()) {
        tigerAutoLink(alert.symbol);
    }

    const _hp = document.getElementById('alertHistoryPanel');
    if (_hp && (_hp.classList.contains('active') || _hp.offsetParent !== null) && typeof renderAlertHistory === 'function') {
        renderAlertHistory();
    }
}

function stopImpulseWebSocket() {
    window.impulsePollingEnabled = false;
    stopPollingFallback();
    if (impulseWsReconnectTimer) {
        clearTimeout(impulseWsReconnectTimer);
        impulseWsReconnectTimer = null;
    }
    if (impulseWs) {
        impulseWs.onclose = null;
        try { impulseWs.close(); } catch (e) {}
        impulseWs = null;
    }
    console.log('⏹️ Impulse: остановлено');
}

Object.defineProperty(window, 'impulseWsEnabled', {
    get: () => window.impulsePollingEnabled,
    set: (v) => { window.impulsePollingEnabled = v; }
});

// Polling fallback (оставляем для обратной совместимости)
async function pollImpulseAlerts() {
    if (!window.impulsePollingEnabled) return;
    try {
        const res = await fetch(`/api/impulse-alerts/?since=${impulseLastTimestamp}`);
        if (!res.ok) return;

        const data = await res.json();
        const alerts = data.alerts || [];
        if (alerts.length === 0) return;

        const maxAlertTs = Math.max(...alerts.map(a => a.timestamp || 0));
        impulseLastTimestamp = Math.max(impulseLastTimestamp, data.server_time || 0, maxAlertTs);
        localStorage.setItem('impulseLastTimestamp', String(impulseLastTimestamp));

        for (const alert of alerts) {
            handleImpulseAlert(alert);
        }
        const _hp = document.getElementById('alertHistoryPanel');
        if (_hp && (_hp.classList.contains('active') || _hp.offsetParent !== null) && typeof renderAlertHistory === 'function') {
            renderAlertHistory();
        }
    } catch (err) {
        console.warn('Impulse polling error:', err);
    }
}
