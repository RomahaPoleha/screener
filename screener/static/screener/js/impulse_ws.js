// ==========================================
// impulse_ws.js — WEBSOCKET ДЛЯ REAL-TIME ЦЕН
// Используется для импульсных алертов
// ==========================================

// --- Переменные ---
let impulseWs = null;
let impulseWsEnabled = false;
let priceHistory = {};  // symbol -> [{time, price}]

// ==========================================
// ЗАПУСК WEBSOCKET
// ==========================================
function startImpulseWebSocket() {
    if (impulseWsEnabled || impulseWs) return;
    try {
        impulseWs = new WebSocket('wss://fstream.binance.com/market/ws/!miniTicker@arr');
        impulseWsEnabled = true;

        impulseWs.onmessage = (e) => {
            try {
                const tickers = JSON.parse(e.data);
                const nowSec = Date.now() / 1000;
                for (const ticker of tickers) {
                    const symbol = ticker.s;
                    if (!symbol.endsWith('USDT')) continue;
                    const cleanSymbol = symbol.replace('USDT', '');
                    const price = parseFloat(ticker.c);
                    if (!price) continue;

                    // Обновляем priceHistory
                    if (!priceHistory[cleanSymbol]) priceHistory[cleanSymbol] = [];
                    const history = priceHistory[cleanSymbol];
                    history.push({ time: nowSec, price: price });

                    // Обрезаем до 5 минут
                    while (history.length > 0 && (nowSec - history[0].time) > 300) {
                        history.shift();
                    }
                }
            } catch (err) {
                console.warn('Impulse WS parse error:', err);
            }
        };

        impulseWs.onclose = () => {
            impulseWsEnabled = false;
            impulseWs = null;
            // Переподключение всегда если импульс включён
            if (volumeAlertEnabled) {
                setTimeout(startImpulseWebSocket, 3000);
            }
        };

        impulseWs.onerror = (err) => {
            console.warn('Impulse WS error:', err);
            try { impulseWs.close(); } catch(e) {}
        };

        console.log('✅ Impulse WebSocket started (real-time prices)');
    } catch (err) {
        console.error('Failed to start impulse WS:', err);
    }
}

// ==========================================
// ОСТАНОВКА WEBSOCKET
// ==========================================
function stopImpulseWebSocket() {
    if (impulseWs) {
        impulseWsEnabled = false;
        try { impulseWs.close(); } catch(e) {}
        impulseWs = null;
    }
}