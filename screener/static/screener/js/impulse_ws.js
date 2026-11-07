// ==========================================
// impulse_ws.js — POLLING С КЛИЕНТСКОЙ ФИЛЬТРАЦИЕЙ
// ==========================================

window.impulsePollingEnabled = false;
let impulsePollingTimer = null;
let impulseLastTimestamp = parseFloat(localStorage.getItem('impulseLastTimestamp') || '0');
const seenImpulses = new Set();

function startImpulseWebSocket() {
    if (window.impulsePollingEnabled) return;
    window.impulsePollingEnabled = true;
    console.log('✅ Impulse: серверный polling запущен');
    pollImpulseAlerts();
    impulsePollingTimer = setInterval(pollImpulseAlerts, 2000);
}

function stopImpulseWebSocket() {
    window.impulsePollingEnabled = false;
    if (impulsePollingTimer) {
        clearInterval(impulsePollingTimer);
        impulsePollingTimer = null;
    }
    console.log('⏹️ Impulse: polling остановлен');
}

Object.defineProperty(window, 'impulseWsEnabled', {
    get: () => window.impulsePollingEnabled,
    set: (v) => { window.impulsePollingEnabled = v; }
});

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

        const userThreshold = parseFloat(localStorage.getItem('priceImpulseThreshold') || '1.0');
        const userWindow = parseInt(localStorage.getItem('priceImpulseWindow') || '60');

        for (const alert of alerts) {
            const key = `${alert.symbol}_${alert.window}_${alert.timestamp}`;
            if (seenImpulses.has(key)) continue;
            seenImpulses.add(key);

            if (alert.window !== userWindow) continue;
            if (alert.price_change < userThreshold) continue;

            const direction = alert.direction === 'up' ? 'в†‘' : 'в†“';

            showVolumeAlertToast(
                alert.symbol,
                alert.volume || 0,
                direction,
                alert.price_change
            );

            // Авто-линковка в Tiger при импульсе
            if (typeof tigerAutoLink === 'function' && typeof tigerIsAutoLinkEnabled === 'function' && tigerIsAutoLinkEnabled()) {
                tigerAutoLink(alert.symbol);
            }
        }
        // Обновить историю импульсов, если панель открыта
        const _hp = document.getElementById('alertHistoryPanel');
        if (_hp && (_hp.classList.contains('active') || _hp.offsetParent !== null) && typeof renderAlertHistory === 'function') {
            renderAlertHistory();
        }
    } catch (err) {
        console.warn('Impulse polling error:', err);
    }
}