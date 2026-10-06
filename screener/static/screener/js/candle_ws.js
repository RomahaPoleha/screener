// ==========================================
// candle_ws.js — WEBSOCKET СВЕЧЕЙ
// Real-time обновление свечей на графике
// ==========================================

function startCandleWebSocket(symbol, tf) {
    // Быстрая проверка - если уже есть живое соединение для того же символа и TF, не создаем новое
    if (wsCandles && 
        wsCandles.readyState === WebSocket.OPEN && 
        wsCandles._currentSymbol === symbol && 
        wsCandles._currentTF === tf) {
        console.log(`WS свечей: уже подключен к ${symbol} ${tf}`);
        return;
    }
    
    // Закрываем предыдущее соединение
    if (wsCandles) {
        try {
            wsCandles.onclose = null; // Предотвращаем триггер автоматического переподключения
            if (wsCandles.readyState === WebSocket.OPEN || wsCandles.readyState === WebSocket.CONNECTING) {
                wsCandles.close(1000, 'Новое соединение');
            }
        } catch (e) {
            // Игнорируем ошибки при закрытии
        }
        wsCandles = null;
    }
    
    const streamName = `${symbol.toLowerCase()}usdt@kline_${tf}`;
    const wsUrl = `wss://fstream.binance.com/market/ws/${streamName}`;
    
    try {
        wsCandles = new WebSocket(wsUrl);
        // Сохраняем метаданные о подключении
        wsCandles._currentSymbol = symbol;
        wsCandles._currentTF = tf;
        
        wsCandles.onopen = () => {
            console.log(`✅ WS свечей подключен: ${symbol} ${tf}`);
        };
        
        wsCandles.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                if (data.k && candleSeries && currentSymbol === symbol) {
                    const k = data.k;
                    const candle = {
                        time: Math.floor(k.t / 1000), open: parseFloat(k.o), high: parseFloat(k.h),
                        low: parseFloat(k.l), close: parseFloat(k.c), volume: parseFloat(k.v)
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
                            time: candle.time, value: candle.volume,
                            color: candle.close >= candle.open ? 'rgba(200, 200, 200, 0.6)' : 'rgba(80, 80, 80, 0.7)'
                        });
                    }
                }
            } catch (err) { 
                // Тихая обработка ошибок парсинга
                console.error('Ошибка парсинга WS свечи:', err); 
            }
        };
        
        wsCandles.onerror = (e) => {
            // Тихая обработка ошибок WebSocket - не засоряем консоль
            // Event {isTrusted: true, type: 'error', target: WebSocket, currentTarget: WebSocket, …}
            // Это часто бывает при нормальной работе WebSocket
        };
        
        wsCandles.onclose = (event) => {
            console.log(`WS свечей закрыт: ${symbol} ${tf}, код: ${event.code}`);
            // Автоматическое переподключение только если это тот же символ
            if (currentSymbol === symbol && event.code !== 1000) {
                setTimeout(() => startCandleWebSocket(symbol, tf), 3000);
            }
        };
        
    } catch (e) {
        console.error('Ошибка создания WebSocket:', e);
        // Переподключение через 5 секунд при ошибке создания
        if (currentSymbol === symbol) {
            setTimeout(() => startCandleWebSocket(symbol, tf), 5000);
        }
    }
}