// ==========================================
// candle_ws.js — WEBSOCKET СВЕЧЕЙ (ИСПРАВЛЕННЫЙ)
// ОДНО соединение для всех монет (Binance поддерживает до 1024 stream в одном соединении)
// ==========================================

let wsCandles = null;
let activeStreams = new Set();  // Активные потоки: 'btcusdt@kline_1m'
let currentSymbol = null;       // Текущая монета на графике
let currentTf = '1m';           // Текущий таймфрейм

// ОДНО соединение для всех монет
function initCandleWebSocket() {
    if (wsCandles && wsCandles.readyState === WebSocket.OPEN) {
        return; // Уже подключены
    }
    
    // Закрываем старое соединение если есть
    if (wsCandles) {
        wsCandles.onclose = null;
        wsCandles.close();
        wsCandles = null;
    }
    
    // Если нет активных потоков, не подключаемся
    if (activeStreams.size === 0) {
        return;
    }
    
    // Binance позволяет подписаться на несколько stream в одном соединении
    // Формат: wss://fstream.binance.com/stream?streams=btcusdt@kline_1m/ethusdt@kline_1m
    const streams = Array.from(activeStreams).join('/');
    const wsUrl = `wss://fstream.binance.com/stream?streams=${streams}`;
    
    wsCandles = new WebSocket(wsUrl);
    
    wsCandles.onopen = () => {
        console.log(`✅ Candle WS подключен: ${activeStreams.size} потоков`);
    };
    
    wsCandles.onmessage = (e) => {
        try {
            const data = JSON.parse(e.data);
            
            // Binance возвращает { stream: 'btcusdt@kline_1m', data: {...} }
            if (data.stream && data.data && data.data.k) {
                const streamName = data.stream; // 'btcusdt@kline_1m'
                const symbol = streamName.split('@')[0].replace('usdt', '').toUpperCase();
                
                // Обновляем только если это текущая монета на графике
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
                    
                    // Обновляем кэш свечей
                    if (window.candleData) {
                        const lastCandle = window.candleData[window.candleData.length - 1];
                        if (lastCandle && lastCandle.time === candle.time) {
                            window.candleData[window.candleData.length - 1] = candle;
                        } else {
                            window.candleData.push(candle);
                        }
                    }
                    
                    // Обновляем volume series
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

// Добавить символ в отслеживание
function addCandleSubscription(symbol, tf = '1m') {
    const streamName = `${symbol.toLowerCase()}usdt@kline_${tf}`;
    
    if (!activeStreams.has(streamName)) {
        activeStreams.add(streamName);
        console.log(`➕ Добавлена свеча: ${symbol} ${tf}`);
        
        // Переподключаемся с новым списком потоков
        if (wsCandles) {
            wsCandles.onclose = null;
            wsCandles.close();
            wsCandles = null;
        }
        setTimeout(initCandleWebSocket, 100);
    }
}

// Удалить символ из отслеживание
function removeCandleSubscription(symbol, tf = '1m') {
    const streamName = `${symbol.toLowerCase()}usdt@kline_${tf}`;
    
    if (activeStreams.has(streamName)) {
        activeStreams.delete(streamName);
        console.log(`➖ Удалена свеча: ${symbol} ${tf}`);
        
        // Если потоков не осталось, закрываем соединение
        if (activeStreams.size === 0 && wsCandles) {
            wsCandles.onclose = null;
            wsCandles.close();
            wsCandles = null;
        }
    }
}

// Обновить текущий символ на графике (вместо старой startCandleWebSocket)
function updateCurrentCandleSymbol(symbol, tf = '1m') {
    const oldSymbol = currentSymbol;
    currentSymbol = symbol;
    currentTf = tf;
    
    // Удаляем старый символ если он был
    if (oldSymbol && oldSymbol !== symbol) {
        removeCandleSubscription(oldSymbol, tf);
    }
    
    // Добавляем новый
    addCandleSubscription(symbol, tf);
}

// Очистить все подписки (при закрытии вкладки)
function clearAllCandleSubscriptions() {
    activeStreams.clear();
    if (wsCandles) {
        wsCandles.onclose = null;
        wsCandles.close();
        wsCandles = null;
    }
    console.log('🧹 Все подписки свечей очищены');
}

// Инициализация при загрузке
document.addEventListener('DOMContentLoaded', () => {
    // Закрываем WS при закрытии вкладки
    window.addEventListener('beforeunload', clearAllCandleSubscriptions);
    
    // Пример: подписаться на первую монету при загрузке
    // Это будет переопределено когда пользователь выберет монету
    setTimeout(() => {
        if (window.activeSymbol) {
            updateCurrentCandleSymbol(window.activeSymbol, '1m');
        }
    }, 1000);
});