// ==========================================
// candle_ws.js — WEBSOCKET СВЕЧЕЙ (ВРЕМЕННАЯ ВЕРСИЯ)
// Добавляем старую функцию обратно для совместимости
// ==========================================

// НОВЫЕ ФУНКЦИИ (исправленные)
let wsCandles = null;
let activeStreams = new Set();
let currentSymbol = null;
let currentTf = '1m';

function initCandleWebSocket() {
    if (wsCandles && wsCandles.readyState === WebSocket.OPEN) return;
    
    if (wsCandles) {
        wsCandles.onclose = null;
        wsCandles.close();
        wsCandles = null;
    }
    
    if (activeStreams.size === 0) return;
    
    const streams = Array.from(activeStreams).join('/');
    const wsUrl = `wss://fstream.binance.com/stream?streams=${streams}`;
    
    wsCandles = new WebSocket(wsUrl);
    
    wsCandles.onopen = () => {
        console.log(`✅ Candle WS подключен: ${activeStreams.size} потоков`);
    };
    
    wsCandles.onmessage = (e) => {
        try {
            const data = JSON.parse(e.data);
            if (data.stream && data.data && data.data.k) {
                const streamName = data.stream;
                const symbol = streamName.split('@')[0].replace('usdt', '').toUpperCase();
                
                if (symbol === currentSymbol && candleSeries) {
                    const k = data.data.k;
                    const candle = {
                        time: Math.floor(k.t / 1000),
                        open: parseFloat(k.o),
                        high: parseFloat(k.h),
                        low: parseFloat(k.l),
                        close: parseFloat(k.c),
                        volume: parseFloat(k.v)
                    };
                    
                    candleSeries.update(candle);
                    
                    if (window.candleData) {
                        const lastCandle = window.candleData[window.candleData.length - 1];
                        if (lastCandle && lastCandle.time === candle.time) {
                            window.candleData[window.candleData.length - 1] = candle;
                        } else {
                            window.candleData.push(candle);
                        }
                    }
                    
                    if (volumeSeries) {
                        volumeSeries.update({
                            time: candle.time,
                            value: candle.volume,
                            color: candle.close >= candle.open ? 'rgba(200, 200, 200, 0.6)' : 'rgba(80, 80, 80, 0.7)'
                        });
                    }
                }
            }
        } catch (err) {
            console.error('Ошибка парсинга WS свечи:', err);
        }
    };
    
    wsCandles.onerror = (e) => {
        console.error('WS свечей ошибка:', e);
    };
    
    wsCandles.onclose = () => {
        console.log('WS свечей закрыт, переподключение через 3 секунды...');
        setTimeout(initCandleWebSocket, 3000);
    };
}

function addCandleSubscription(symbol, tf = '1m') {
    const streamName = `${symbol.toLowerCase()}usdt@kline_${tf}`;
    
    if (!activeStreams.has(streamName)) {
        activeStreams.add(streamName);
        console.log(`➕ Добавлена свеча: ${symbol} ${tf}`);
        
        if (wsCandles) {
            wsCandles.onclose = null;
            wsCandles.close();
            wsCandles = null;
        }
        setTimeout(initCandleWebSocket, 100);
    }
}

function removeCandleSubscription(symbol, tf = '1m') {
    const streamName = `${symbol.toLowerCase()}usdt@kline_${tf}`;
    
    if (activeStreams.has(streamName)) {
        activeStreams.delete(streamName);
        console.log(`➖ Удалена свеча: ${symbol} ${tf}`);
        
        if (activeStreams.size === 0 && wsCandles) {
            wsCandles.onclose = null;
            wsCandles.close();
            wsCandles = null;
        }
    }
}

// СТАРАЯ ФУНКЦИЯ ДЛЯ СОВМЕСТИМОСТИ
function startCandleWebSocket(symbol, tf) {
    console.log(`⚠️ Используется старая startCandleWebSocket для ${symbol} ${tf}`);
    console.log(`⚠️ Переключитесь на updateCurrentCandleSymbol`);
    
    // Вызываем новую функцию для совместимости
    updateCurrentCandleSymbol(symbol, tf);
}

// НОВАЯ ФУНКЦИЯ
function updateCurrentCandleSymbol(symbol, tf = '1m') {
    const oldSymbol = currentSymbol;
    currentSymbol = symbol;
    currentTf = tf;
    
    if (oldSymbol && oldSymbol !== symbol) {
        removeCandleSubscription(oldSymbol, tf);
    }
    
    addCandleSubscription(symbol, tf);
}

function clearAllCandleSubscriptions() {
    activeStreams.clear();
    if (wsCandles) {
        wsCandles.onclose = null;
        wsCandles.close();
        wsCandles = null;
    }
    console.log('🧹 Все подписки свечей очищены');
}

// Инициализация
document.addEventListener('DOMContentLoaded', () => {
    window.addEventListener('beforeunload', clearAllCandleSubscriptions);
    
    setTimeout(() => {
        if (window.activeSymbol) {
            updateCurrentCandleSymbol(window.activeSymbol, '1m');
        }
    }, 1000);
});