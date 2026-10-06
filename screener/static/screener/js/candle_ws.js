// ==========================================
// candle_ws.js — WEBSOCKET СВЕЧЕЙ
// Real-time обновление свечей на графике
// ==========================================

function startCandleWebSocket(symbol, tf) {
    // Проверка входных параметров
    if (!symbol || typeof symbol !== 'string') {
        console.error('Некорректный символ для WebSocket:', symbol);
        return;
    }
    
    if (!tf || !['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'].includes(tf)) {
        console.error('Некорректный таймфрейм для WebSocket:', tf);
        return;
    }
    
    // Защита от быстрых повторных вызовов
    if (window._wsCandlesReconnecting && Date.now() - window._wsCandlesReconnecting < 1000) {
        console.log('WebSocket: пропускаем быстрый реконнект');
        return;
    }
    
    // Закрываем предыдущее соединение
    if (wsCandles) {
        try {
            wsCandles.onopen = null;
            wsCandles.onmessage = null;
            wsCandles.onerror = null;
            wsCandles.onclose = null;
            if (wsCandles.readyState === WebSocket.OPEN || wsCandles.readyState === WebSocket.CONNECTING) {
                wsCandles.close(1000, 'Новое соединение');
            }
        } catch (e) {
            console.log('Ошибка при закрытии старого WebSocket:', e);
        }
        wsCandles = null;
    }
    
    const streamName = `${symbol.toLowerCase()}usdt@kline_${tf}`;
    const wsUrl = `wss://fstream.binance.com/ws/${streamName}`;
    
    try {
        wsCandles = new WebSocket(wsUrl);
        console.log(`WS свечей: создано соединение для ${symbol} ${tf}`);
    } catch (e) {
        console.error('Ошибка создания WebSocket:', e);
        // Планируем повторную попытку
        if (currentSymbol === symbol) {
            window._wsCandlesReconnecting = Date.now();
            setTimeout(() => startCandleWebSocket(symbol, tf), 5000);
        }
        return;
    }
    
    wsCandles.onopen = () => {
        console.log(`✅ WS свечей подключен: ${symbol} ${tf}`);
        window._wsCandlesReconnecting = 0;
    };
    
    wsCandles.onmessage = (e) => {
        try {
            const data = JSON.parse(e.data);
            if (data.k && candleSeries && currentSymbol === symbol) {
                const k = data.k;
                const candle = {
                    time: Math.floor(k.t / 1000), 
                    open: parseFloat(k.o), 
                    high: parseFloat(k.h),
                    low: parseFloat(k.l), 
                    close: parseFloat(k.c), 
                    volume: parseFloat(k.v)
                };
                
                // Проверка корректности данных свечи
                if (isNaN(candle.time) || isNaN(candle.open) || !candle.time || !candle.open) {
                    console.warn('Получены некорректные данные свечи:', candle);
                    return;
                }
                
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
        } catch (err) { 
            console.error('Ошибка парсинга WS свечи:', err, e.data);
        }
    };
    
    wsCandles.onerror = (e) => {
        console.error('WS свечей ошибка:', e);
        window._wsCandlesReconnecting = Date.now();
    };
    
    wsCandles.onclose = (event) => {
        console.log(`WS свечей закрыт: ${symbol} ${tf}, код: ${event.code}, причина: ${event.reason || 'нет'}`);
        window._wsCandlesReconnecting = Date.now();
        if (currentSymbol === symbol) {
            const delay = event.code === 1006 ? 5000 : 3000; // Более долгая задержка при ошибках сети
            setTimeout(() => startCandleWebSocket(symbol, tf), delay);
        }
    };
}