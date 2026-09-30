// ==========================================
// impulse_ws.js — POLLING С КЛИЕНТСКОЙ ФИЛЬТРАЦИЕЙ
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

        // ✅ Читаем настройки пользователя из localStorage
        const userThreshold = parseFloat(localStorage.getItem('priceImpulseThreshold') || '1.0');
        const userWindow = parseInt(localStorage.getItem('priceImpulseWindow') || '60');

        // Обрабатываем каждый алерт
        for (const alert of alerts) {
            // ✅ ФИЛЬТР 1: Показываем только выбранный пользователем таймфрейм
            if (alert.window !== userWindow) continue;

            // ✅ ФИЛЬТР 2: Показываем только если амплитуда выше порога пользователя
            if (alert.price_change < userThreshold) continue;

            const direction = alert.direction === 'up' ? '↑' : '↓';

            // ✅ 3. ИСПРАВЛЕНО: берём реальный объём с сервера
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