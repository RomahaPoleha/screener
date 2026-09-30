// ==========================================
// impulse_ws.js — POLLING СЕРВЕРНЫХ ИМПУЛЬСОВ
// Было: WebSocket к Binance (клиент)
// Стало: Polling серверного API
// ==========================================

let impulsePollingEnabled = false;
let impulsePollingTimer = null;
let impulseLastTimestamp = 0;  // Для фильтрации новых алертов

// ==========================================
// ЗАПУСК POLLING
// ==========================================
function startImpulseWebSocket() {
    // Функция оставлена для совместимости имени
    if (impulsePollingEnabled) return;
    impulsePollingEnabled = true;

    console.log('✅ Impulse: серверный polling запущен');
    pollImpulseAlerts();  // Первый запрос сразу
    impulsePollingTimer = setInterval(pollImpulseAlerts, 2000);
}

// ==========================================
// ОСТАНОВКА POLLING
// ==========================================
function stopImpulseWebSocket() {
    impulsePollingEnabled = false;
    if (impulsePollingTimer) {
        clearInterval(impulsePollingTimer);
        impulsePollingTimer = null;
    }
    console.log('⏹️ Impulse: polling остановлен');
}

// Совместимость: проверяем что "WS включён"
// (используется в onclose для переподключения)
let impulseWsEnabled = false;
Object.defineProperty(window, 'impulseWsEnabled', {
    get: () => impulsePollingEnabled,
    set: (v) => { impulsePollingEnabled = v; }
});

// ==========================================
// POLLING ФУНКЦИЯ
// ==========================================
async function pollImpulseAlerts() {
    if (!impulsePollingEnabled) return;

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
         showVolumeAlertToast(
             alert.symbol,
             alert.volume
                direction,
                alert.price_change
            );
        }

    } catch (err) {
        console.warn('Impulse polling error:', err);
    }
}