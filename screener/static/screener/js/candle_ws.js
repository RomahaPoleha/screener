// ==========================================
// candle_ws.js — WEBSOCKET СВЕЧЕЙ (СТАБИЛЬНАЯ ВЕРСИЯ)
// Исправлены race conditions при быстром переключении символов
// ==========================================

let wsCandles = null;
let candleWsSymbol = null;        // Какой символ сейчас обслуживает WS
let lastCandleReconnectTime = 0;

function startCandleWebSocket(symbol, tf) {
    const now = Date.now();
    
    // Защита от слишком частых вызовов
    if (candleWsSymbol === symbol && now - lastCandleReconnectTime < 2000) {
        return;
    }

    // Закрываем предыдущее соединение
    if (wsCandles) {
        wsCandles.onclose = null;
        wsCandles.onerror = null;
        wsCandles.close();
        wsCandles = null;
    }

    candleWsSymbol = symbol;
    lastCandleReconnectTime = now;

    const streamName = `${symbol.toLowerCase()}usdt@kline_${tf}`;
    const wsUrl = `wss://fstream.binance.com/ws/${streamName}`;
    
    console.log(`🔌 [CandleWS] Подключение для ${symbol} (${tf}): ${wsUrl}`);
    
    wsCandles = new WebSocket(wsUrl);
    
    wsCandles.onopen = () => {
        console.log(`✅ [CandleWS] Подключен: ${symbol} ${tf}`);
        lastCandleReconnectTime = Date.now();
    };
    
    wsCandles.onmessage = (e) => {
        // Проверяем, что это сообщение для текущего символа
        if (candleWsSymbol !== symbol || !candleSeries || currentSymbol !== symbol) {
            return;
        }
        
        try {
            const data = JSON.parse(e.data);
            if (data.k) {
                const k = data.k;
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
                        color: candle.close >= candle.open 
                            ? 'rgba(200, 200, 200, 0.6)' 
                            : 'rgba(80, 80, 80, 0.7)'
                    });
                }
            }
        } catch (err) {
            console.error('Ошибка парсинга WS свечи:', err);
        }
    };
    
    wsCandles.onerror = (e) => {
        console.error(`❌ [CandleWS] Ошибка (${symbol} ${tf}):`, e.type || e);
    };
    
    wsCandles.onclose = (e) => {
        console.log(`🔄 [CandleWS] Закрыт (${symbol} ${tf}), code: ${e.code}`);
        
        if (currentSymbol === symbol && candleWsSymbol === symbol) {
            const delay = (e.code === 1006 || e.code === 1005) ? 1500 : 4000;
            setTimeout(() => {
                if (currentSymbol === symbol) {
                    startCandleWebSocket(symbol, tf);
                }
            }, delay);
        } else {
            candleWsSymbol = null;
        }
    };
}

// Для очистки при смене страницы/символа
window.stopCandleWebSocket = function() {
    if (wsCandles) {
        wsCandles.onclose = null;
        wsCandles.onerror = null;
        wsCandles.close();
        wsCandles = null;
    }
    candleWsSymbol = null;
};
