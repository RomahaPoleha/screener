// ==========================================
// impulse.js — POLLING СЕРВЕРНЫХ ИМПУЛЬСОВ
// ==========================================

// ✅ 1. ДЕЛАЕМ ПЕРЕМЕННУЮ ГЛОБАЛЬНОЙ, чтобы settings.js её видел
window.impulsePollingEnabled = false;
let impulsePollingTimer = null;
let impulseLastTimestamp = 0;

// ==========================================
// ЗАПУСК POLLING
// ==========================================
function startImpulseWebSocket() {
    if (window.impulsePollingEnabled) return;
    window.impulsePollingEnabled = true;

    console.log('✅ Impulse: серверный polling запущен');
    pollImpulseAlerts();
    impulsePollingTimer = setInterval(pollImpulseAlerts, 2000);
}

// ==========================================
// ОСТАНОВКА POLLING
// ==========================================
function stopImpulseWebSocket() {
    window.impulsePollingEnabled = false;
    if (impulsePollingTimer) {
        clearInterval(impulsePollingTimer);
        impulsePollingTimer = null;
    }
    console.log('⏹️ Impulse: polling остановлен');
}

// Совместимость: проверяем что "WS включён"
Object.defineProperty(window, 'impulseWsEnabled', {
    get: () => window.impulsePollingEnabled,
    set: (v) => { window.impulsePollingEnabled = v; }
});

// ==========================================
// POLLING ФУНКЦИЯ
// ==========================================
async function pollImpulseAlerts() {
    if (!window.impulsePollingEnabled) return;

    try {
        const res = await fetch(`/api/impulse-alerts/?since=${impulseLastTimestamp}`);
        if (!res.ok) return;

        const data = await res.json();
        const alerts = data.alerts || [];

        if (alerts.length === 0) return;

        // Обновляем timestamp
        impulseLastTimestamp = data.server_time || Date.now() / 1000;

        // Обрабатываем каждый алерт
        for (const alert of alerts) {
            const direction = alert.direction === 'up' ? '↑' : '↓';

            // ✅ 2. ИСПРАВЛЕНО: добавлена запятая после alert.volume || 0
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