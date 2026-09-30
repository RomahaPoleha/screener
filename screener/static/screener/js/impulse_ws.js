// ==========================================
// impulse_ws.js — POLLING С КЛИЕНТСКОЙ ФИЛЬТРАЦИЕЙ
// ==========================================

// ✅ 1. ДЕЛАЕМ ПЕРЕМЕННУЮ ГЛОБАЛЬНОЙ (window.)
window.impulsePollingEnabled = false;
let impulsePollingTimer = null;
let impulseLastTimestamp = 0;

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

        impulseLastTimestamp = data.server_time || Date.now() / 1000;

        // ✅ 2. ЧИТАЕМ НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ ДЛЯ ФИЛЬТРАЦИИ
        const userThreshold = parseFloat(localStorage.getItem('priceImpulseThreshold') || '1.0');
        const userWindow = parseInt(localStorage.getItem('priceImpulseWindow') || '60');

        for (const alert of alerts) {
            // ✅ 3. ФИЛЬТР: показываем только выбранный таймфрейм и порог
            if (alert.window !== userWindow) continue;
            if (alert.price_change < userThreshold) continue;

            const direction = alert.direction === 'up' ? '↑' : '↓';

            // ✅ 4. ИСПРАВЛЕНО: передаем реальный объем с сервера, а не 0
            showVolumeAlertToast(
                alert.symbol,
                alert.volume || 0,
                direction,
                alert.price_change
            );
        }
    } catch (err) {
        console.warn('Impulse polling error:', err);
    }
}