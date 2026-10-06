// ==========================================
// candle_ws.js — WEBSOCKET СВЕЧЕЙ
// Real-time обновление свечей на графике
// ==========================================

function startCandleWebSocket(symbol, tf) {
    // ПРОСТОЙ И НАДЕЖНЫЙ КОД
    
    // Закрываем предыдущее соединение тихо
    if (wsCandles) {
        try {
            // Отключаем все обработчики, чтобы не было лишних событий
            wsCandles.onopen = null;
            wsCandles.onmessage = null;
            wsCandles.onerror = null;
            wsCandles.onclose = null;
            // Пытаемся закрыть
            if (wsCandles.readyState === WebSocket.OPEN || wsCandles.readyState === WebSocket.CONNECTING) {
                wsCandles.close(1000, 'Смена монеты');
            }
        } catch (e) {
            // Игнорируем ВСЕ ошибки при закрытии
        }
        wsCandles = null;
    }
    
    const streamName = `${symbol.toLowerCase()}usdt@kline_${tf}`;
    const wsUrl = `wss://fstream.binance.com/market/ws/${streamName}`;
    
    // Подавляем ОДНУ ошибку создания WebSocket, если она возникает при быстром создании после закрытия
    try {
        wsCandles = new WebSocket(wsUrl);
        
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
            // ПОЛНОСТЬЮ ТИХАЯ обработка - НИЧЕГО не выводим
            // Это подавляет ошибку: "WebSocket is closed before the connection is established"
        };
        
        wsCandles.onclose = () => {
            // Автоматическое переподключение только если это тот же символ
            if (currentSymbol === symbol) {
                // Добавляем небольшую задержку перед переподключением
                setTimeout(() => startCandleWebSocket(symbol, tf), 3000);
            }
        };
        
    } catch (e) {
        // Перехватываем и тихо игнорируем ошибку создания WebSocket
        // Это бывает при быстром переключении, когда браузер ещё не освободил предыдущее соединение
        // Переподключение через мгновение
        setTimeout(() => {
            if (currentSymbol === symbol) {
                startCandleWebSocket(symbol, tf);
            }
        }, 100);
    }
}