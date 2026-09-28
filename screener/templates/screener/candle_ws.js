// ==========================================
// candle_ws.js — WEBSOCKET СВЕЧЕЙ
// Real-time обновление свечей на графике
// ==========================================

function startCandleWebSocket(symbol, tf) {
    if (wsCandles) { wsCandles.onclose = null; wsCandles.close(); wsCandles = null; }
    const streamName = `${symbol.toLowerCase()}usdt@kline_${tf}`;
    const wsUrl = `wss://fstream.binance.com/market/ws/${streamName}`;
    wsCandles = new WebSocket(wsUrl);
    wsCandles.onopen = () => console.log(`WS свечей подключен: ${symbol} ${tf}`);
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
        } catch (err) { console.error('Ошибка парсинга WS свечи:', err); }
    };
    wsCandles.onerror = (e) => console.error('WS свечей ошибка:', e);
    wsCandles.onclose = () => {
        if (currentSymbol === symbol) setTimeout(() => startCandleWebSocket(symbol, tf), 3000);
    };
}