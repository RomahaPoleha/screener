// ==========================================
// ПРЕДЗАГРУЗКА АУДИО ФАЙЛОВ
// ==========================================
const sound5min = new Audio('/api/sound/alert_5min.mp3');
const sound1min = new Audio('/api/sound/alert_1min.mp3');
sound5min.preload = 'auto';
sound1min.preload = 'auto';

function playHourSound(minutesLeft) {
    if (!soundEnabled) return;

    const sound = minutesLeft === 5 ? sound5min : sound1min;
    sound.currentTime = 0;

    sound.play().catch(err => {
        console.warn('Не удалось воспроизвести звук:', err);
        speak(minutesLeft === 5
            ? 'До перехода на новый час осталось 5 минут'
            : 'Внимание, до перехода на новый час осталась 1 минута');
    });
}

// ==========================================
// УТИЛИТЫ И ФОРМАТИРОВАНИЕ
// ==========================================
const fmtThreshold = (v) => v >= 1000 ? `$${(v/1000).toFixed(0)}K` : `$${v}`;
const fmt = (num) => {
    if (!num) return '0';
    if (num >= 1e9) return (num / 1e9).toFixed(1) + 'B';
    if (num >= 1e6) return (num / 1e6).toFixed(1) + 'M';
    if (num >= 1e3) return (num / 1e3).toFixed(1) + 'K';
    return Number(num).toFixed(2);
};
const safeTime = (t) => t > 10000000000 ? Math.floor(t / 1000) : Math.floor(t);
const getNatrClass = (val) => val > 1.0 ? 'natr-high' : val > 0.3 ? 'natr-mid' : 'natr-low';

function formatAge(seconds) {
    const totalMinutes = Math.floor(seconds / 60);
    const hours = Math.floor(totalMinutes / 60);
    const mins = totalMinutes % 60;
    return hours > 0 ? `${hours}ч ${mins}м` : `${mins}м`;
}

function formatVolumeText(volume) {
    if (volume >= 1000000) return `${(volume / 1000000).toFixed(2)}M`;
    if (volume >= 1000) return `${(volume / 1000).toFixed(1)}K`;
    return `${volume.toFixed(0)}`;
}

// ==========================================
// ЗВУК И ОПОВЕЩЕНИЯ
// ==========================================
function playAlertSound() {
    try {
        if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.connect(gain); gain.connect(audioCtx.destination);
        osc.frequency.value = 880; osc.type = 'sine';
        gain.gain.setValueAtTime(0.3, audioCtx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.5);
        osc.start(audioCtx.currentTime); osc.stop(audioCtx.currentTime + 0.5);
    } catch(e) { console.warn('Ошибка звука:', e); }
}

function showPriceAlertToast(currentPrice, alertPrice, direction) {
    const toast = document.createElement('div');
    toast.className = 'hour-toast show';
    toast.style.background = 'linear-gradient(135deg, #f0b90b 0%, #f59e0b 100%)';
    toast.innerHTML = `<div class="toast-icon">🔔</div><div class="toast-content"><div class="toast-title">Алерт сработал!</div><div style="font-size:12px; margin-top:4px;">Цена пересекла ${alertPrice.toFixed(currentPrecision)}<br>Направление: ${direction}</div></div>`;
    document.body.appendChild(toast);
    setTimeout(() => { toast.classList.remove('show'); setTimeout(() => toast.remove(), 500); }, 5000);
}

function checkAlerts(currentPrice, prevPrice) {
    activeAlerts.forEach((alert) => {
        if (!alert.active) return;
        const crossedAbove = (prevPrice < alert.price && currentPrice >= alert.price);
        const crossedBelow = (prevPrice > alert.price && currentPrice <= alert.price);
        if (crossedAbove || crossedBelow) {
            const direction = crossedAbove ? 'вверх ↑' : 'вниз ↓';
            playAlertSound();
            showPriceAlertToast(currentPrice, alert.price, direction);
            alert.active = false;
        }
    });
}

// ==========================================
// ЗАГРУЗКА ДАННЫХ И API
// ==========================================
async function loadAllData() {
    try {
        const res = await fetch(`/api/data/`);
        if (!res.ok) throw new Error(`Ошибка сети: ${res.status}`);
        allCoins = await res.json();
        applyLocalFilters();
    } catch (err) {
        console.error('❌ Ошибка загрузки:', err);
        els.table.innerHTML = `<div style="color:#ef4444; text-align:center; padding:20px;">${err.message}</div>`;
    }
}

async function loadNatrData() {
    try {
        const res = await fetch(`/api/natr/`);
        if (!res.ok) throw new Error('Ошибка NATR');
        const response = await res.json();
        natrData = response.natr || {};
        applyLocalFilters();
    } catch (err) { console.error(err); }
}

function startNatrAutoUpdate() {
    if (natrAutoUpdateTimer) return;
    loadNatrData();
    natrAutoUpdateTimer = setInterval(loadNatrData, 15000);
}

// ==========================================
// ТАБЛИЦА И ФИЛЬТРЫ
// ==========================================
function showSearchDropdown(query) {
    if (!query || query.length === 0) { hideSearchDropdown(); return; }
    const filtered = allCoins.filter(coin => coin.symbol.toUpperCase().includes(query.toUpperCase())).slice(0, 10);
    const dropdown = document.getElementById('searchDropdown');
    if (filtered.length === 0) { hideSearchDropdown(); return; }
    dropdown.innerHTML = filtered.map(coin => `<div class="search-dropdown-item" onclick="selectCoinFromSearch('${coin.symbol}')"><span class="symbol">${coin.symbol}</span><span class="name">Vol: $${fmt(coin.volume)}</span></div>`).join('');
    dropdown.classList.add('active');
}
function hideSearchDropdown() { document.getElementById('searchDropdown').classList.remove('active'); }
function selectCoinFromSearch(symbol) {
    document.getElementById('searchInput').value = symbol;
    hideSearchDropdown();
    applyLocalFilters();
    openChart(symbol);
}
function resetFilters() {
    document.getElementById('searchInput').value = '';
    document.getElementById('volRange').value = 0;
    document.getElementById('changeRange').value = -100;
    document.getElementById('volVal').textContent = '$0';
    document.getElementById('changeVal').textContent = '-100%';
    applyLocalFilters();
}

function renderTable(data) {
    if (els.coinsCount) els.coinsCount.textContent = data.length;
    if (!data.length) {
        els.table.innerHTML = '<div style="color:#6b7280; text-align:center; padding:20px;">Нет данных</div>';
        return;
    }
    els.table.innerHTML = data.map(coin => {
        const isUp = coin.change >= 0;
        const natr = natrData[coin.symbol] || {};
        const n1 = natr.natr_1m30;
        const n5 = natr.natr_5m14;
        const n1Txt = (n1 !== undefined && n1 !== null) ? n1.toFixed(1) : '-';
        const n5Txt = (n5 !== undefined && n5 !== null) ? n5.toFixed(1) : '-';
        return `<div class="coin-row" onclick="openChart('${coin.symbol}')">
            <div class="coin-symbol">${coin.symbol}</div>
            <div class="coin-change ${isUp ? 'text-up' : 'text-down'}">${isUp ? '+' : ''}${coin.change}%</div>
            <div class="coin-volume">$${fmt(coin.volume)}</div>
            <div class="coin-natr ${n1 ? getNatrClass(n1) : 'empty'}">${n1Txt}</div>
            <div class="coin-natr ${n5 ? getNatrClass(n5) : 'empty'}">${n5Txt}</div>
        </div>`;
    }).join('');
}

function sortBy(field) {
    if (sortState.field === field) sortState.direction = sortState.direction === 'asc' ? 'desc' : 'asc';
    else { sortState.field = field; sortState.direction = (field === 'natr_1m' || field === 'natr_5m') ? 'desc' : 'asc'; }
    document.querySelectorAll('.coins-header span').forEach(el => el.textContent = '');
    const arrow = document.getElementById(`sort-${field}`);
    if (arrow) arrow.textContent = sortState.direction === 'asc' ? '↑' : '↓';
    applyLocalFilters();
}

function applyLocalFilters() {
    const searchVal = els.search.value.toUpperCase();
    const minVol = parseFloat(els.vol.value);
    const minChange = parseFloat(els.change.value);
    let filtered = allCoins.filter(coin => {
        if (coin.volume < minVol || coin.change < minChange) return false;
        if (searchVal && !coin.symbol.includes(searchVal)) return false;
        return true;
    });
    if (sortState.field) {
        filtered.sort((a, b) => {
            let valA, valB;
            if (['change', 'volume'].includes(sortState.field)) { valA = a[sortState.field]; valB = b[sortState.field]; }
            else if (sortState.field === 'natr_1m') {
                valA = natrData[a.symbol]?.natr_1m30 ?? -1; valB = natrData[b.symbol]?.natr_1m30 ?? -1;
            } else if (sortState.field === 'natr_5m') {
                valA = natrData[a.symbol]?.natr_5m14 ?? -1; valB = natrData[b.symbol]?.natr_5m14 ?? -1;
            }
            return sortState.direction === 'asc' ? valA - valB : valB - valA;
        });
    } else {
        filtered.sort((a, b) => Math.abs(b.change) - Math.abs(a.change));
    }
    renderTable(filtered);
}

// ==========================================
// WEBSOCKET СВЕЧЕЙ И СДЕЛОК
// ==========================================
function startCandleWebSocket(symbol, tf) {
    if (wsCandles) { wsCandles.onclose = null; wsCandles.close(); wsCandles = null; }
    const streamName = `${symbol.toLowerCase()}usdt@kline_${tf}`;
    const wsUrl = `wss://fstream.binance.com/market/ws/${streamName}`;
    wsCandles = new WebSocket(wsUrl);
    wsCandles.onopen = () => console.log(`✅ WS свечей подключен: ${symbol} ${tf}`);
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

                if (activeAlerts.length > 0) {
                    const prevPrice = lastCandlePrice !== null ? lastCandlePrice : candle.open;
                    checkAlerts(candle.close, prevPrice);
                    lastCandlePrice = candle.close;
                }

                if (volumeSeries) {
                    volumeSeries.update({
                        time: candle.time, value: candle.volume,
                        color: candle.close >= candle.open ? 'rgba(200, 200, 200, 0.6)' : 'rgba(80, 80, 80, 0.7)'
                    });
                }
            }
        } catch (err) { console.error('❌ Ошибка парсинга WS свечи:', err); }
    };
    wsCandles.onerror = (e) => console.error('❌ WS свечей ошибка:', e);
    wsCandles.onclose = () => {
        if (currentSymbol === symbol) setTimeout(() => startCandleWebSocket(symbol, tf), 3000);
    };
}

function startTradesStream(symbol) {
    if (wsTrades) { wsTrades.onclose = null; wsTrades.onmessage = null; wsTrades.close(); wsTrades = null; }
    const tradesUrl = `wss://fstream.binance.com/ws/${symbol.toLowerCase()}usdt@trade`;
    wsTrades = new WebSocket(tradesUrl);
    wsTrades.onmessage = (e) => {
        try {
            const trade = JSON.parse(e.data);
            const price = parseFloat(trade.p);
            const qty = parseFloat(trade.q);
            const value = price * qty;
            if (value < currentThreshold) return;
            const isBuyerMaker = trade.m;
            const time = new Date(trade.T).toLocaleTimeString('ru-RU', { hour12: false });
            tradeBuffer.push({ time, price, qty, value, isBuyerMaker });
            if (tradeBuffer.length > 50) tradeBuffer.shift();
            if (els.tradesOverlay.classList.contains('active')) updateTradesOverlay();
        } catch (err) { console.warn('Trade parse error:', err); }
    };
    wsTrades.onerror = () => {
        if (els.tradesOverlayBody) els.tradesOverlayBody.innerHTML = '<div style="color:#ef4444; text-align:center; padding:20px;">⚠️ Разрыв связи</div>';
    };
    wsTrades.onclose = () => {
        setTimeout(() => { if (currentSymbol === symbol && els.tradesOverlay.classList.contains('active')) startTradesStream(symbol); }, 3000);
    };
}

// ==========================================
// ИНСТРУМЕНТЫ РИСОВАНИЯ
// ==========================================
function clearSpecificDrawings(type) {
    if (type === 'alerts') {
        activeAlerts.forEach(a => { try { candleSeries.removePriceLine(a.line); } catch(e){} });
        activeAlerts = [];
    } else if (type === 'trendlines') {
        activeTrendlines = [];
        redrawAllPersistentDrawings();
    } else if (type === 'pencil') {
        pencilStrokes = [];
        currentStroke = null;
        if (pencilCtx) pencilCtx.clearRect(0, 0, els.pencilCanvas.width, els.pencilCanvas.height);
    } else if (type === 'ruler') {
        isRulerDragging = false;
        rulerStartPoint = null;
        rulerCurrentPoint = null;
        rulerFixedMeasurement = null;
        els.rulerMeasurement.style.display = 'none';
        if (pencilCtx) {
            pencilCtx.clearRect(0, 0, els.pencilCanvas.width, els.pencilCanvas.height);
        }
    }
}

function updateToolUI(btnId, isActive) {
    const btn = document.getElementById(btnId);
    if (btn) btn.classList.toggle('active', isActive);
}

function toggleDrawingToolsVisibility() {
    showDrawingTools = !showDrawingTools;
    els.drawingToolsPanel.style.display = showDrawingTools ? 'flex' : 'none';
    localStorage.setItem('showDrawingTools', showDrawingTools);
}

function toggleMagnet() {
    isMagnetEnabled = !isMagnetEnabled;
    updateToolUI('magnetBtn', isMagnetEnabled);
    localStorage.setItem('magnetEnabled', isMagnetEnabled);
    if (isMagnetEnabled) createMagnetIndicator();
    else removeMagnetIndicator();
}

function toggleAlertMode() {
    isAlertModeEnabled = !isAlertModeEnabled;
    updateToolUI('alertBtn', isAlertModeEnabled);
    if (isAlertModeEnabled) {
        isTrendLineEnabled = false; isPencilEnabled = false; isRulerEnabled = false;
        updateToolUI('trendLineBtn', false); updateToolUI('pencilBtn', false); updateToolUI('rulerBtn', false);
    } else {
        clearSpecificDrawings('alerts');
    }
}

function toggleTrendLine() {
    isTrendLineEnabled = !isTrendLineEnabled;
    updateToolUI('trendLineBtn', isTrendLineEnabled);

    if (isTrendLineEnabled) {
        isAlertModeEnabled = false;
        isPencilEnabled = false;
        isRulerEnabled = false;
        updateToolUI('alertBtn', false);
        updateToolUI('pencilBtn', false);
        updateToolUI('rulerBtn', false);

        if (chart) {
            chart.applyOptions({
                handleScroll: { mouseWheel: true, pressedMouseMove: false }
            });
        }
    } else {
        trendLineStart = null;
        isDrawingTrendLine = false;
        trendLinePreview = null;

        // НЕ очищаем activeTrendlines — линии сохраняются!

        if (chart) {
            chart.applyOptions({
                handleScroll: { mouseWheel: true, pressedMouseMove: true }
            });
        }

        redrawAllPersistentDrawings();
    }
}

function togglePencil() {
    isPencilEnabled = !isPencilEnabled;
    updateToolUI('pencilBtn', isPencilEnabled);

    if (isPencilEnabled) {
        isAlertModeEnabled = false;
        isTrendLineEnabled = false;
        isRulerEnabled = false;
        updateToolUI('alertBtn', false);
        updateToolUI('trendLineBtn', false);
        updateToolUI('rulerBtn', false);

        if (chart) {
            chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: false } });
        }
        initPencilCanvas();
    } else {
        isDrawing = false;
        lastPencilPoint = null;

        // НЕ очищаем pencilStrokes — рисунки сохраняются!

        if (chart) {
            chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: true } });
        }

        redrawAllPersistentDrawings();
    }
}

function toggleRuler() {
    isRulerEnabled = !isRulerEnabled;
    updateToolUI('rulerBtn', isRulerEnabled);
    if (isRulerEnabled) {
        isAlertModeEnabled = false; isTrendLineEnabled = false; isPencilEnabled = false;
        updateToolUI('alertBtn', false); updateToolUI('trendLineBtn', false); updateToolUI('pencilBtn', false);

        if (chart) {
            chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: false } });
        }
    } else {
        clearSpecificDrawings('ruler');
        if (chart) {
            chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: true } });
        }
    }
}

function clearAllDrawings() {
    clearSpecificDrawings('alerts');
    clearSpecificDrawings('trendlines');
    clearSpecificDrawings('pencil');
    clearSpecificDrawings('ruler');
}

// ==========================================
// ЛОГИКА РИСОВАНИЯ
// ==========================================
function initPencilCanvas() {
    if (!chart || !els.pencilCanvas) return;
    const rect = els.chartWrapper.getBoundingClientRect();
    els.pencilCanvas.width = rect.width;
    els.pencilCanvas.height = rect.height;
    pencilCtx = els.pencilCanvas.getContext('2d');
    redrawAllPersistentDrawings();
}

// Получает время по X, даже в правой части (где rightOffset)
function getTimeByX(x) {
    let time = chart.timeScale().coordinateToTime(x);
    if (time !== null) return time;

    const candles = window.candleData || [];
    if (candles.length === 0) return null;

    const lastCandle = candles[candles.length - 1];
    const lastCandleX = chart.timeScale().timeToCoordinate(lastCandle.time);

    if (lastCandleX === null) return null;

    const visibleRange = chart.timeScale().getVisibleLogicalRange();
    if (!visibleRange) return null;

    const chartWidth = els.chartWrapper.clientWidth;
    const barsCount = visibleRange.to - visibleRange.from;
    const pixelsPerBar = chartWidth / barsCount;

    const barsOffset = Math.round((x - lastCandleX) / pixelsPerBar);
    const secondsPerBar = {'1m':60,'5m':300,'15m':900,'30m':1800,'1h':3600,'4h':14400}[currentTF] || 60;

    return lastCandle.time + (barsOffset * secondsPerBar);
}

function redrawPencilStrokes() {
    if (!pencilCtx || !chart || !candleSeries) return;

    pencilCtx.strokeStyle = '#f0b90b';
    pencilCtx.lineWidth = 2;
    pencilCtx.lineCap = 'round';
    pencilCtx.lineJoin = 'round';

    pencilStrokes.forEach(stroke => {
        if (stroke.length < 2) return;
        pencilCtx.beginPath();
        let started = false;

        for (const point of stroke) {
            const x = chart.timeScale().timeToCoordinate(point.time);
            const y = candleSeries.priceToCoordinate(point.price);

            if (x === null || y === null) {
                started = false;
                continue;
            }

            if (!started) {
                pencilCtx.moveTo(x, y);
                started = true;
            } else {
                pencilCtx.lineTo(x, y);
            }
        }
        pencilCtx.stroke();
    });

    if (currentStroke && currentStroke.length >= 2) {
        pencilCtx.beginPath();
        let started = false;

        for (const point of currentStroke) {
            const x = chart.timeScale().timeToCoordinate(point.time);
            const y = candleSeries.priceToCoordinate(point.price);

            if (x === null || y === null) {
                started = false;
                continue;
            }

            if (!started) {
                pencilCtx.moveTo(x, y);
                started = true;
            } else {
                pencilCtx.lineTo(x, y);
            }
        }
        pencilCtx.stroke();
    }
}

function drawRulerRectangle(start, end) {
    const x1 = chart.timeScale().timeToCoordinate(start.time);
    const y1 = candleSeries.priceToCoordinate(start.price);
    const x2 = chart.timeScale().timeToCoordinate(end.time);
    const y2 = candleSeries.priceToCoordinate(end.price);

    if (x1 === null || y1 === null || x2 === null || y2 === null) return;

    const isUp = end.price >= start.price;
    const color = isUp ? 'rgba(34, 197, 94, 0.2)' : 'rgba(239, 68, 68, 0.2)';
    const borderColor = isUp ? 'rgba(34, 197, 94, 0.8)' : 'rgba(239, 68, 68, 0.8)';

    const left = Math.min(x1, x2);
    const top = Math.min(y1, y2);
    const width = Math.abs(x2 - x1);
    const height = Math.abs(y2 - y1);

    pencilCtx.fillStyle = color;
    pencilCtx.fillRect(left, top, width, height);

    pencilCtx.strokeStyle = borderColor;
    pencilCtx.lineWidth = 1;
    pencilCtx.setLineDash([4, 4]);
    pencilCtx.strokeRect(left, top, width, height);
    pencilCtx.setLineDash([]);
}

function redrawAllPersistentDrawings() {
    if (!pencilCtx || !chart) return;
    pencilCtx.clearRect(0, 0, els.pencilCanvas.width, els.pencilCanvas.height);

    pencilCtx.strokeStyle = '#22c55e';
    pencilCtx.lineWidth = 2;
    pencilCtx.setLineDash([5, 5]);

    activeTrendlines.forEach(tl => {
        const x1 = chart.timeScale().timeToCoordinate(tl.time1);
        const y1 = candleSeries.priceToCoordinate(tl.price1);
        const x2 = chart.timeScale().timeToCoordinate(tl.time2);
        const y2 = candleSeries.priceToCoordinate(tl.price2);

        if (x1 !== null && y1 !== null && x2 !== null && y2 !== null) {
            pencilCtx.beginPath();
            pencilCtx.moveTo(x1, y1);
            pencilCtx.lineTo(x2, y2);
            pencilCtx.stroke();
        }
    });

    if (isDrawingTrendLine && trendLinePreview) {
        const x1 = chart.timeScale().timeToCoordinate(trendLinePreview.time1);
        const y1 = candleSeries.priceToCoordinate(trendLinePreview.price1);
        const x2 = chart.timeScale().timeToCoordinate(trendLinePreview.time2);
        const y2 = candleSeries.priceToCoordinate(trendLinePreview.price2);

        if (x1 !== null && y1 !== null && x2 !== null && y2 !== null) {
            pencilCtx.strokeStyle = 'rgba(156, 163, 175, 0.7)';
            pencilCtx.lineWidth = 1.5;
            pencilCtx.setLineDash([3, 3]);
            pencilCtx.beginPath();
            pencilCtx.moveTo(x1, y1);
            pencilCtx.lineTo(x2, y2);
            pencilCtx.stroke();
        }
    }

    if (isRulerEnabled && isRulerDragging && rulerStartPoint && rulerCurrentPoint) {
        drawRulerRectangle(rulerStartPoint, rulerCurrentPoint);
    }

    if (rulerFixedMeasurement) {
        drawRulerRectangle(rulerFixedMeasurement.start, rulerFixedMeasurement.end);
    }

    redrawPencilStrokes();

    pencilCtx.setLineDash([]);
}

function handleChartClick(param) {
    if (!param.point || typeof param.point.y !== 'number') return;
    if (isRulerEnabled) return;

    if (isAlertModeEnabled) {
        const price = candleSeries.coordinateToPrice(param.point.y);
        if (!price || isNaN(price)) return;
        const line = candleSeries.createPriceLine({
            price: price, color: 'rgba(240, 185, 11, 0.9)', lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true,
            axisLabelColor: '#ffffff', axisLabelBackgroundColor: 'rgba(240, 185, 11, 0.8)',
            title: `🔔 ${price.toFixed(currentPrecision)}`
        });
        activeAlerts.push({ price: price, line: line, active: true });
    }
    else if (isTrendLineEnabled) {
        const price = candleSeries.coordinateToPrice(param.point.y);
        const time = param.time || getTimeByX(param.point.x);

        if (!price || isNaN(price) || !time) return;

        if (!isDrawingTrendLine) {
            trendLineStart = { time, price, x: param.point.x, y: param.point.y };
            isDrawingTrendLine = true;
            trendLinePreview = { time1: time, price1: price, time2: time, price2: price };
        } else {
            activeTrendlines.push({
                time1: trendLineStart.time, price1: trendLineStart.price,
                time2: time, price2: price
            });
            isDrawingTrendLine = false;
            trendLineStart = null;
            trendLinePreview = null;
            redrawAllPersistentDrawings();
        }
    }
}

function handlePencilDraw(param) {
    if (!isPencilEnabled || !isDrawing || !pencilCtx || !param.point) return;

    const price = candleSeries.coordinateToPrice(param.point.y);
    const time = param.time || getTimeByX(param.point.x);

    if (!price || !time) {
        lastPencilPoint = param.point;
        return;
    }

    if (!currentStroke) {
        currentStroke = [{ time, price }];
    } else {
        currentStroke.push({ time, price });
    }

    if (lastPencilPoint) {
        pencilCtx.strokeStyle = '#f0b90b';
        pencilCtx.lineWidth = 2;
        pencilCtx.lineCap = 'round';
        pencilCtx.lineJoin = 'round';
        pencilCtx.beginPath();
        pencilCtx.moveTo(lastPencilPoint.x, lastPencilPoint.y);
        pencilCtx.lineTo(param.point.x, param.point.y);
        pencilCtx.stroke();
    }
    lastPencilPoint = param.point;
}

function showRulerMeasurement(start, end) {
    if (!start || !end) return;

    const priceDiff = Math.abs(end.price - start.price);
    const pricePercent = ((priceDiff / start.price) * 100).toFixed(2);
    const direction = end.price >= start.price ? '↑' : '↓';
    const color = end.price >= start.price ? '#22c55e' : '#ef4444';

    let barsCount = 0;
    if (start.time && end.time) {
        const t1 = typeof start.time === 'number' ? start.time : start.time.timestamp;
        const t2 = typeof end.time === 'number' ? end.time : end.time.timestamp;
        const tfSeconds = {'1m':60,'5m':300,'15m':900,'30m':1800,'1h':3600,'4h':14400}[currentTF] || 60;
        barsCount = Math.abs(Math.floor((t2 - t1) / tfSeconds));
    }

    const candles = window.candleData || [];
    const startTime = typeof start.time === 'number' ? start.time : start.time.timestamp;
    const endTime = typeof end.time === 'number' ? end.time : end.time.timestamp;

    const rangeCandles = candles.filter(c => {
        const candleTime = typeof c.time === 'number' ? c.time : c.time;
        return candleTime >= Math.min(startTime, endTime) && candleTime <= Math.max(startTime, endTime);
    });

    let volatilityPercent = '-';
    let volatilityValue = '-';
    let maxPrice = '-';
    let minPrice = '-';

    if (rangeCandles.length > 0) {
        let highest = -Infinity;
        let lowest = Infinity;
        rangeCandles.forEach(candle => {
            if (candle.high > highest) highest = candle.high;
            if (candle.low < lowest) lowest = candle.low;
        });
        maxPrice = highest.toFixed(currentPrecision);
        minPrice = lowest.toFixed(currentPrecision);
        const volatilityDiff = highest - lowest;
        volatilityValue = volatilityDiff.toFixed(currentPrecision);
        volatilityPercent = ((volatilityDiff / lowest) * 100).toFixed(2);
    }

    const formatTime = (t) => {
        const time = typeof t === 'number' ? t : t.timestamp;
        const date = new Date(time * 1000);
        return date.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
    };

    els.rulerMeasurement.innerHTML = `
        <div style="font-weight:700; color:${color}; margin-bottom:6px; font-size:12px;">
            ${direction} ${pricePercent}% | ${priceDiff.toFixed(currentPrecision)}
        </div>
        <div style="font-size:10px; color:#9ca3af; margin-bottom:6px;">
            ${barsCount} баров<br>
            ${formatTime(start.time)} → ${formatTime(end.time)}
        </div>
        ${volatilityPercent !== '-' ? `
        <div style="border-top:1px solid #2d3748; margin:6px -12px -6px -12px; padding:6px 12px 0 12px;">
            <div style="color:#f0b90b; font-weight:600; font-size:11px; margin-bottom:4px;">
                📊 Волатильность: ${volatilityPercent}%
            </div>
            <div style="font-size:10px; color:#9ca3af;">
                ${volatilityValue} pts<br>
                <span style="color:#22c55e">Max:</span> ${maxPrice} |
                <span style="color:#ef4444">Min:</span> ${minPrice}
            </div>
        </div>
        ` : ''}
    `;

    const centerX = (start.x + end.x) / 2;
    const centerY = (start.y + end.y) / 2;
    els.rulerMeasurement.style.left = `${centerX - 80}px`;
    els.rulerMeasurement.style.top = `${centerY - 50}px`;
    els.rulerMeasurement.style.display = 'block';
}

// ==========================================
// МАГНИТ
// ==========================================
function createMagnetIndicator() {
    if (!chart || !els.chartWrapper) return;
    removeMagnetIndicator();
    magnetIndicator = document.createElement('div');
    magnetIndicator.className = 'magnet-indicator';
    els.chartWrapper.appendChild(magnetIndicator);
}
function removeMagnetIndicator() {
    if (magnetIndicator && magnetIndicator.parentNode) {
        magnetIndicator.parentNode.removeChild(magnetIndicator);
        magnetIndicator = null;
    }
}
function updateMagnetIndicator(param) {
    if (!isMagnetEnabled || !magnetIndicator || !param || !param.point) {
        if (magnetIndicator) magnetIndicator.style.display = 'none';
        return;
    }
    const candles = window.candleData || [];
    if (candles.length === 0) return;

    let cursorTime = param.time || chart.timeScale().coordinateToTime(param.point.x);
    let nearestCandle = candles[candles.length - 1];
    let minTimeDiff = Infinity;

    for (const candle of candles) {
        const timeDiff = Math.abs(candle.time - cursorTime);
        if (timeDiff < minTimeDiff) { minTimeDiff = timeDiff; nearestCandle = candle; }
    }

    const priceAtCursor = candleSeries.coordinateToPrice(param.point.y);
    if (priceAtCursor === null || priceAtCursor === undefined) return;

    const magnetPoints = [
        { type: 'ohlc', price: nearestCandle.open, distance: Math.abs(nearestCandle.open - priceAtCursor) },
        { type: 'ohlc', price: nearestCandle.high, distance: Math.abs(nearestCandle.high - priceAtCursor) },
        { type: 'ohlc', price: nearestCandle.low, distance: Math.abs(nearestCandle.low - priceAtCursor) },
        { type: 'ohlc', price: nearestCandle.close, distance: Math.abs(nearestCandle.close - priceAtCursor) }
    ];
    activeAlerts.forEach(a => {
        if (a.active) magnetPoints.push({ type: 'alert', price: a.price, distance: Math.abs(a.price - priceAtCursor) });
    });

    magnetPoints.sort((a, b) => a.distance - b.distance);
    const nearest = magnetPoints[0];
    const snapX = chart.timeScale().timeToCoordinate(nearestCandle.time);
    const snapY = candleSeries.priceToCoordinate(nearest.price);

    if (snapX !== null && snapY !== null) {
        magnetIndicator.style.display = 'block';
        magnetIndicator.style.left = `${snapX - 5}px`;
        magnetIndicator.style.top = `${snapY - 5}px`;
        magnetIndicator.classList.toggle('alert-magnet', nearest.type === 'alert');
    } else {
        magnetIndicator.style.display = 'none';
    }
}

// ==========================================
// НАСТРОЙКИ И RECON/SCALP
// ==========================================
function openSettingsModal() {
    document.getElementById('densityMarketFuture').checked = densityMarkets.future;
    document.getElementById('densityMarketSpot').checked = densityMarkets.spot;
    document.getElementById('densityMinVolumeFuture').value = densityMinVolumeFuture;
    document.getElementById('densityMinVolumeSpot').value = densityMinVolumeSpot;

    document.getElementById('scalpMarketFuture').checked = scalpMarkets.future;
    document.getElementById('scalpMarketSpot').checked = scalpMarkets.spot;
    document.getElementById('scalpMinVolumeFuture').value = scalpMinVolumeFuture;
    document.getElementById('scalpMinVolumeSpot').value = scalpMinVolumeSpot;

    document.getElementById('showVolumeHistogram').checked = volumeHistogramEnabled;
    document.getElementById('showDrawingTools').checked = showDrawingTools;

    const soundBtnModal = document.getElementById('soundToggleModal');
    if (soundBtnModal) {
        soundBtnModal.textContent = soundEnabled ? '🔊 Голосовое оповещение' : '🔇 Голосовое оповещение';
        soundBtnModal.classList.toggle('muted', !soundEnabled);
    }

    const modal = new bootstrap.Modal(document.getElementById('settingsModal'));
    modal.show();
}

function applySettings() {
    densityMarkets = {
        future: document.getElementById('densityMarketFuture').checked,
        spot: document.getElementById('densityMarketSpot').checked
    };
    densityMinVolumeFuture = Math.max(50000, parseInt(document.getElementById('densityMinVolumeFuture').value) || 50000);
    densityMinVolumeSpot = Math.max(10000, parseInt(document.getElementById('densityMinVolumeSpot').value) || 10000);
    densityEnabled = densityMarkets.future || densityMarkets.spot;
    localStorage.setItem('densityMinVolumeFuture', densityMinVolumeFuture);
    localStorage.setItem('densityMinVolumeSpot', densityMinVolumeSpot);

    scalpMarkets = {
        future: document.getElementById('scalpMarketFuture').checked,
        spot: document.getElementById('scalpMarketSpot').checked
    };
    scalpMinVolumeFuture = Math.max(200000, parseInt(document.getElementById('scalpMinVolumeFuture').value) || 200000);
    scalpMinVolumeSpot = Math.max(100000, parseInt(document.getElementById('scalpMinVolumeSpot').value) || 100000);
    scalpEnabled = scalpMarkets.future || scalpMarkets.spot;
    localStorage.setItem('scalpMinVolumeFuture', scalpMinVolumeFuture);
    localStorage.setItem('scalpMinVolumeSpot', scalpMinVolumeSpot);

    volumeHistogramEnabled = document.getElementById('showVolumeHistogram').checked;
    localStorage.setItem('volumeHistogramEnabled', volumeHistogramEnabled);
    if (volumeSeries) volumeSeries.applyOptions({ visible: volumeHistogramEnabled });

    showDrawingTools = document.getElementById('showDrawingTools').checked;
    localStorage.setItem('showDrawingTools', showDrawingTools);
    els.drawingToolsPanel.style.display = showDrawingTools ? 'flex' : 'none';

    const btn = document.getElementById('settingsBtn');
    if (densityEnabled || scalpEnabled) {
        btn.style.background = '#f0b90b'; btn.style.color = '#000';
    } else {
        btn.style.background = '#2d3748'; btn.style.color = '#9ca3af';
    }

    if (currentSymbol) {
        if (densityEnabled) {
            previousDensities = { future: [], spot: [] };
            startDensityUpdates(currentSymbol);
        } else {
            if (densityUpdateTimer) { clearInterval(densityUpdateTimer); densityUpdateTimer = null; }
            clearDensityLines();
        }
        if (scalpEnabled) {
            previousScalpData = { futures: [], spot: [] };
            startScalpUpdates(currentSymbol);
        } else {
            if (scalpUpdateTimer) { clearInterval(scalpUpdateTimer); scalpUpdateTimer = null; }
            clearScalpLines();
        }
    }
    bootstrap.Modal.getInstance(document.getElementById('settingsModal')).hide();
}

async function loadDensities(symbol) {
    if (!densityEnabled || !candleSeries) return;
    let hasChanges = false;
    const marketsToLoad = [];
    if (densityMarkets.future) marketsToLoad.push('future');
    if (densityMarkets.spot) marketsToLoad.push('spot');
    const allNewData = {};

    for (const market of marketsToLoad) {
        try {
            const url = market === 'future'
                ? `https://fapi.binance.com/fapi/v1/depth?symbol=${symbol}USDT&limit=1000`
                : `https://api.binance.com/api/v3/depth?symbol=${symbol}USDT&limit=1000`;
            const res = await fetch(url);
            if (!res.ok) continue;
            const data = await res.json();
            const densities = [];
            const minVolume = market === 'future' ? densityMinVolumeFuture : densityMinVolumeSpot;
            const processSide = (sideArr, sideType) => {
                for (const [priceStr, qtyStr] of sideArr) {
                    const price = parseFloat(priceStr);
                    const qty = parseFloat(qtyStr);
                    const val = price * qty;
                    if (val >= minVolume) densities.push({ price, volume: val, side: sideType });
                }
            };
            if (data.bids) processSide(data.bids, 'buy');
            if (data.asks) processSide(data.asks, 'sell');
            densities.sort((a, b) => b.volume - a.volume);
            allNewData[market] = densities.slice(0, 20);
        } catch (e) {
            console.error(`Densities error (${market}):`, e);
            allNewData[market] = previousDensities[market] || [];
        }
    }

    for (const market of marketsToLoad) {
        const newData = allNewData[market] || [];
        const currentData = JSON.stringify(newData.map(d => ({price: d.price, volume: d.volume, side: d.side})));
        const prevData = JSON.stringify((previousDensities[market] || []).map(d => ({price: d.price, volume: d.volume, side: d.side})));
        if (currentData !== prevData) { hasChanges = true; previousDensities[market] = newData; }
    }

    if (!hasChanges) return;
    clearDensityLines();
    for (const market of marketsToLoad) {
        const data = previousDensities[market] || [];
        data.forEach(d => {
           const line = candleSeries.createPriceLine({
                price: d.price, color: 'rgba(255, 255, 255, 0.5)', lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true,
                axisLabelColor: '#ffffff', axisLabelBackgroundColor: 'rgba(100, 100, 100, 0.7)',
                title: `${market === 'future' ? 'BI-F' : 'BI-S'} ${d.volume >= 1000 ? (d.volume/1000).toFixed(1)+'K' : d.volume}`
            });
            densityLines.push(line);
        });
    }
}
function clearDensityLines() {
    if (!candleSeries) return;
    densityLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch(e){} });
    densityLines = [];
}
function startDensityUpdates(symbol) {
    if (densityUpdateTimer) clearInterval(densityUpdateTimer);
    loadDensities(symbol);
    densityUpdateTimer = setInterval(() => {
        if (currentSymbol === symbol && densityEnabled) loadDensities(symbol);
    }, 3000);
}

async function loadScalpDensities(symbol) {
    if (!scalpEnabled || !candleSeries || isScalpLoading) return;
    isScalpLoading = true;
    try {
        const marketsToLoad = [];
        if (scalpMarkets.future) marketsToLoad.push('futures');
        if (scalpMarkets.spot) marketsToLoad.push('spot');
        const allNewData = {};
        let hasChanges = false;

        for (const market of marketsToLoad) {
            const minVol = market === 'futures' ? scalpMinVolumeFuture : scalpMinVolumeSpot;
            try {
                const res = await fetch(`/api/scalp/${symbol}/?min_volume=${minVol}&market=${market}`);
                if (!res.ok) continue;
                const data = await res.json();
                allNewData[market] = data.densities || [];
            } catch (e) {
                console.error(`Scalp load error (${market}):`, e);
                allNewData[market] = previousScalpData[market] || [];
            }
        }

        for (const market of marketsToLoad) {
            const newData = allNewData[market] || [];
            const prevData = previousScalpData[market] || [];
            const currentSignature = JSON.stringify(newData.map(d => ({ p: d.price, v: d.volume, s: d.side })));
            const prevSignature = JSON.stringify(prevData.map(d => ({ p: d.price, v: d.volume, s: d.side })));
            if (currentSignature !== prevSignature) { hasChanges = true; previousScalpData[market] = newData; }
        }

        if (!hasChanges) return;
        clearScalpLines();

        for (const market of marketsToLoad) {
            const densities = allNewData[market] || [];
            densities.forEach(d => {
                const ageSeconds = d.age_seconds || 0;
                const ageText = formatAge(ageSeconds);
                const volumeText = formatVolumeText(d.volume);
                const prefix = market === 'futures' ? 'BI-F' : 'BI-S';
                const volumeNum = parseFloat(d.volume) || 0;
                const lineColor = volumeNum < 500000 ? 'rgba(251, 191, 36, 0.9)' : 'rgba(186, 85, 211, 0.9)';
                const line = candleSeries.createPriceLine({
                    price: d.price, color: lineColor, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true, axisLabelColor: '#000000', axisLabelBackgroundColor: lineColor,
                    title: `${prefix} ${volumeText} ${ageText}`
                });
                scalpLines.push(line);
            });
        }
    } catch (err) { console.error('Scalp load error:', err); }
    finally { isScalpLoading = false; }
}
function clearScalpLines() {
    scalpLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch(e){} });
    scalpLines = [];
}
function startScalpUpdates(symbol) {
    if (scalpUpdateTimer) clearInterval(scalpUpdateTimer);
    loadScalpDensities(symbol);
    scalpUpdateTimer = setInterval(() => {
        if (currentSymbol === symbol && scalpEnabled) loadScalpDensities(symbol);
    }, 3000);
}

// ==========================================
// ГРАФИК И УПРАВЛЕНИЕ
// ==========================================
function updateWatermark() {
    if (currentSymbol && els.chartWatermark) {
        els.watermarkSymbol.textContent = currentSymbol;
        els.watermarkTF.textContent = currentTF;
        els.chartWatermark.style.display = 'block';
    }
}
function copySymbolToClipboard() {
    if (!currentSymbol) return;
    navigator.clipboard.writeText(`${currentSymbol}USDT`).then(() => {
        els.chartTitle.classList.add('copied'); els.chartTitle.textContent = `✅ ${currentSymbol}USDT скопирован!`;
        setTimeout(() => { els.chartTitle.classList.remove('copied'); els.chartTitle.textContent = `${currentSymbol}/USDT`; }, 1200);
    }).catch(err => console.error('Ошибка копирования:', err));
}

function closeChart() {
    clearAllDrawings();
    clearDensityLines();
    if (densityUpdateTimer) { clearInterval(densityUpdateTimer); densityUpdateTimer = null; }
    previousDensities = { future: [], spot: [] };
    clearScalpLines();
    previousScalpData = { futures: [], spot: [] };
    if (scalpUpdateTimer) { clearInterval(scalpUpdateTimer); scalpUpdateTimer = null; }
    if (wsCandles) { wsCandles.onclose = null; wsCandles.close(); wsCandles = null; }
    if (wsTrades) { wsTrades.onclose = null; wsTrades.onmessage = null; wsTrades.onerror = null; wsTrades.close(); wsTrades = null; }
    if (chart) { chart.remove(); chart = null; candleSeries = null; volumeSeries = null; }
    tradeBuffer = []; lastCandlePrice = null;
    els.chartTitle.textContent = ''; els.chartWrapper.classList.remove('active');
    els.chartHint.style.display = 'block'; els.chartWatermark.style.display = 'none';
    closeTradesOverlay(); currentSymbol = '';
}

async function openChart(symbol) {
    if (wsCandles) { wsCandles.onclose = null; wsCandles.close(); wsCandles = null; }
    if (wsTrades) { wsTrades.onclose = null; wsTrades.onmessage = null; wsTrades.onerror = null; wsTrades.close(); wsTrades = null; }
    clearDensityLines(); if (densityUpdateTimer) { clearInterval(densityUpdateTimer); densityUpdateTimer = null; }
    clearScalpLines(); previousScalpData = { futures: [], spot: [] }; if (scalpUpdateTimer) { clearInterval(scalpUpdateTimer); scalpUpdateTimer = null; }
    await new Promise(resolve => setTimeout(resolve, 150));

    currentSymbol = symbol;
    els.chartHint.style.display = 'none'; els.chartWrapper.classList.add('active');
    tradeBuffer = []; lastCandlePrice = null;
    if (chart) { chart.remove(); chart = null; candleSeries = null; volumeSeries = null; }

    try {
        chart = LightweightCharts.createChart(els.chartWrapper, {
            width: els.chartWrapper.clientWidth,
            height: els.chartWrapper.clientHeight,
            layout: { background: { color: '#0b0f19' }, textColor: '#d1d5db' },
            grid: { vertLines: { color: '#1e2538' }, horzLines: { color: '#1e2538' } },
            timeScale: {
                timeVisible: true,
                secondsVisible: false,
                borderColor: '#2d3748',
                rightOffset: 50,  // ← Пустое пространство справа для рисования
                barSpacing: 10
            },
            rightPriceScale: { borderColor: '#2d3748', scaleMargins: { top: 0.1, bottom: 0.25 }, autoScale: true },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        });
        candleSeries = chart.addCandlestickSeries({ upColor: '#22c55e', downColor: '#ef4444', borderVisible: false, wickUpColor: '#22c55e', wickDownColor: '#ef4444' });
        volumeSeries = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'volume', scaleMargins: { top: 0.85, bottom: 0 } });
        chart.priceScale('volume').applyOptions({ visible: false, scaleMargins: { top: 0.85, bottom: 0 } });
        if (volumeSeries) volumeSeries.applyOptions({ visible: volumeHistogramEnabled });

        chart.subscribeCrosshairMove((param) => {
            if (isMagnetEnabled) updateMagnetIndicator(param);
            if (isPencilEnabled && isDrawing) handlePencilDraw(param);

            if (isTrendLineEnabled && isDrawingTrendLine && trendLinePreview && param.point) {
                const price = candleSeries.coordinateToPrice(param.point.y);
                const time = param.time || getTimeByX(param.point.x);
                if (price && time) {
                    trendLinePreview.time2 = time;
                    trendLinePreview.price2 = price;
                    redrawAllPersistentDrawings();
                }
            }
        });
        chart.subscribeClick(handleChartClick);
        chart.timeScale().subscribeVisibleTimeRangeChange(redrawAllPersistentDrawings);
        chart.timeScale().subscribeSizeChange(() => {
            setTimeout(() => {
                initPencilCanvas();
            }, 150);  // ← Увеличенная задержка для корректной перерисовки
        });

        els.chartWrapper.addEventListener('mousedown', (e) => {
            if (isPencilEnabled && e.button === 0) {
                isDrawing = true;
                initPencilCanvas();
            }
            if (isRulerEnabled && e.button === 1) {
                e.preventDefault();
                e.stopPropagation();
                rulerFixedMeasurement = null;
                els.rulerMeasurement.style.display = 'none';
                const rect = els.chartWrapper.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;
                const price = candleSeries.coordinateToPrice(y);
                const time = chart.timeScale().coordinateToTime(x) || getTimeByX(x);
                if (price && time) {
                    rulerStartPoint = { time, price, x, y };
                    rulerCurrentPoint = { time, price, x, y };
                    isRulerDragging = true;
                    initPencilCanvas();
                }
            }
        });

        els.chartWrapper.addEventListener('auxclick', (e) => {
            if (e.button === 1) e.preventDefault();
        });

        els.chartWrapper.addEventListener('mouseup', (e) => {
            if (isPencilEnabled && e.button === 0) {
                isDrawing = false;
                lastPencilPoint = null;

                if (currentStroke && currentStroke.length > 0) {
                    pencilStrokes.push(currentStroke);
                    currentStroke = null;
                }
            }
            if (isRulerEnabled && e.button === 1 && isRulerDragging) {
                e.preventDefault();
                isRulerDragging = false;
                if (rulerStartPoint && rulerCurrentPoint) {
                    rulerFixedMeasurement = { start: { ...rulerStartPoint }, end: { ...rulerCurrentPoint } };
                    showRulerMeasurement(rulerFixedMeasurement.start, rulerFixedMeasurement.end);
                }
                rulerStartPoint = null;
                rulerCurrentPoint = null;
            }
        });

        els.chartWrapper.addEventListener('mouseleave', () => {
            if (isPencilEnabled) {
                isDrawing = false;
                lastPencilPoint = null;

                if (currentStroke && currentStroke.length > 0) {
                    pencilStrokes.push(currentStroke);
                    currentStroke = null;
                }
            }
            if (isRulerDragging) {
                isRulerDragging = false;
                if (rulerStartPoint && rulerCurrentPoint) {
                    rulerFixedMeasurement = { start: { ...rulerStartPoint }, end: { ...rulerCurrentPoint } };
                    showRulerMeasurement(rulerFixedMeasurement.start, rulerFixedMeasurement.end);
                }
                rulerStartPoint = null;
                rulerCurrentPoint = null;
            }
        });

        els.chartWrapper.addEventListener('mousemove', (e) => {
            if (isRulerEnabled && isRulerDragging && rulerStartPoint) {
                const rect = els.chartWrapper.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;
                const price = candleSeries.coordinateToPrice(y);
                const time = chart.timeScale().coordinateToTime(x) || getTimeByX(x);
                if (price && time) {
                    rulerCurrentPoint = { time, price, x, y };
                    redrawAllPersistentDrawings();
                }
            }
        });

    } catch (e) { console.error('Chart init error:', e); return; }

    await loadChartData(symbol, currentTF);
    startCandleWebSocket(symbol, currentTF);
    updateWatermark();
    if (els.tradesOverlay.classList.contains('active')) startTradesStream(symbol);
    if (densityEnabled) startDensityUpdates(symbol);
    if (scalpEnabled) startScalpUpdates(symbol);
}

// ИСПРАВЛЕНО: только реальные свечи, без фиктивных
async function loadChartData(symbol, tf) {
    if (!chart || !candleSeries) return;
    els.chartTitle.textContent = `${symbol}/USDT`;
    try {
        const res = await fetch(`/api/candles/${symbol}/?tf=${tf}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const history = await res.json();
        if (!history || history.length === 0) throw new Error('Пустая история');

        const limitedHistory = history.slice(-500);
        const firstPrice = limitedHistory[0].close;
        currentPrecision = firstPrice < 1 ? (firstPrice < 0.01 ? 8 : 5) : 2;
        const minMove = firstPrice < 1 ? (firstPrice < 0.01 ? 0.00000001 : 0.00001) : 0.01;

        candleSeries.applyOptions({ priceFormat: { type: 'price', precision: currentPrecision, minMove: minMove } });

        // Только реальные свечи
        candleSeries.setData(limitedHistory.map(c => ({ ...c, time: safeTime(c.time) })));
        window.candleData = limitedHistory.map(c => ({ ...c, time: safeTime(c.time) }));

        if (limitedHistory[0].volume !== undefined) {
            volumeSeries.setData(limitedHistory.map(c => ({
                time: safeTime(c.time), value: c.volume,
                color: c.close >= c.open ? 'rgba(200, 200, 200, 0.6)' : 'rgba(80, 80, 80, 0.7)'
            })));
        }

        chart.timeScale().fitContent();
        chart.timeScale().scrollToPosition(12, false);

    } catch (err) {
        els.chartTitle.textContent = `Ошибка: ${err.message}`;
        console.error('loadChartData error:', err);
    }
}

// ==========================================
// КРУПНЫЕ СДЕЛКИ
// ==========================================
function toggleTradesOverlay() {
    els.tradesOverlay.classList.contains('active') ? closeTradesOverlay() : openTradesOverlay();
}
function openTradesOverlay() {
    els.tradesOverlay.classList.add('active'); els.tradesBtn.classList.add('active');
    els.tradesThresholdSlider.value = currentThreshold;
    els.tradesThresholdValue.textContent = fmtThreshold(currentThreshold);
    if (currentSymbol && !wsTrades) startTradesStream(currentSymbol);
}
function closeTradesOverlay() { els.tradesOverlay.classList.remove('active'); els.tradesBtn.classList.remove('active'); }
function updateTradesOverlay() {
    if (!els.tradesOverlayBody || tradeBuffer.length === 0) return;
    els.tradesOverlayBody.innerHTML = tradeBuffer.map(t => `<div class="trade-item-compact">
        <span class="trade-time">${t.time}</span><span class="trade-value">$${fmt(t.value)}</span>
        <span class="trade-price">${t.price.toFixed(currentPrecision)}</span><span class="trade-qty">${t.qty.toFixed(4)}</span>
        <span class="${t.isBuyerMaker ? 'trade-sell' : 'trade-buy'}">${t.isBuyerMaker ? 'S' : 'B'}</span>
    </div>`).join('');
    els.tradesOverlayBody.scrollTop = els.tradesOverlayBody.scrollHeight;
}

// ==========================================
// ГОЛОС И ВРЕМЯ
// ==========================================
function toggleSound() {
    soundEnabled = !soundEnabled;
    localStorage.setItem('soundEnabled', soundEnabled);
    const btn = document.getElementById('soundToggleModal');
    btn.textContent = soundEnabled ? '🔊 Голосовое оповещение' : ' Голосовое оповещение';
    btn.classList.toggle('muted', !soundEnabled);
    if (soundEnabled) speak('Оповещения включены');
}
function initVoices() {
    const voices = speechSynthesis.getVoices();
    russianVoice = voices.find(v => v.lang.startsWith('ru')) || voices.find(v => v.lang.includes('ru')) || null;
}
if ('speechSynthesis' in window) { initVoices(); speechSynthesis.onvoiceschanged = initVoices; }
function speak(text) {
    if (!soundEnabled || !('speechSynthesis' in window)) return;
    try {
        speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(text);
        u.lang = 'ru-RU'; u.rate = 0.9; u.pitch = 0.7; u.volume = 0.8;
        if (russianVoice) u.voice = russianVoice;
        speechSynthesis.speak(u);
    } catch (e) { console.warn('Ошибка озвучки:', e); }
}
function showHourToast(title) {
    document.getElementById('toastTitle').textContent = title;
    const toast = document.getElementById('hourToast');
    toast.classList.add('show');
    setTimeout(() => { toast.classList.remove('show'); }, 5000);
}

function checkHourTransition() {
    const now = new Date();
    const currentMinuteKey = now.getHours() * 60 + now.getMinutes();

    if (now.getMinutes() === 55 && now.getSeconds() < 3 && lastNotifiedMinute !== currentMinuteKey) {
        lastNotifiedMinute = currentMinuteKey;
        showHourToast('⏰ До нового часа 5 минут');
        playHourSound(5);
    }

    if (now.getMinutes() === 59 && now.getSeconds() < 3 && lastNotifiedMinute !== currentMinuteKey) {
        lastNotifiedMinute = currentMinuteKey;
        showHourToast(' До нового часа 1 минута');
        playHourSound(1);
    }
}
setInterval(checkHourTransition, 1000);

// ==========================================
// ИНИЦИАЛИЗАЦИЯ И ОБРАБОТЧИКИ СОБЫТИЙ
// ==========================================
els.tradesThresholdSlider.addEventListener('input', (e) => {
    currentThreshold = parseInt(e.target.value);
    els.tradesThresholdValue.textContent = fmtThreshold(currentThreshold);
});

document.getElementById('chart-title').addEventListener('click', copySymbolToClipboard);

document.querySelectorAll('.tf-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
        const newTF = e.target.dataset.tf;
        if (newTF === currentTF) return;
        document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
        e.target.classList.add('active');
        currentTF = newTF;
        if (currentSymbol && chart) {
            loadChartData(currentSymbol, currentTF);
            startCandleWebSocket(currentSymbol, currentTF);
            updateWatermark();
        }
    });
});

els.vol.addEventListener('input', (e) => { els.volVal.innerText = '$' + fmt(e.target.value); applyLocalFilters(); });
els.change.addEventListener('input', (e) => { els.changeVal.innerText = e.target.value + '%'; applyLocalFilters(); });
els.search.addEventListener('input', (e) => { showSearchDropdown(e.target.value); applyLocalFilters(); });

document.addEventListener('click', (e) => { if (!e.target.closest('.search-wrapper')) hideSearchDropdown(); });

// ИСПРАВЛЕНО: увеличенная задержка для корректной перерисовки
window.addEventListener('resize', () => {
    if (chart && els.chartWrapper.classList.contains('active')) {
        chart.applyOptions({ width: els.chartWrapper.clientWidth, height: els.chartWrapper.clientHeight });
        setTimeout(() => {
            initPencilCanvas();
        }, 200);
    }
});

document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 's') { e.preventDefault(); toggleTrendLine(); }
    if (e.altKey && e.key === 'ArrowLeft') { e.preventDefault(); togglePencil(); }
    if (e.key === 'b' || e.key === 'B') { toggleAlertMode(); }
});

document.addEventListener('click', function initSpeech() {
    if ('speechSynthesis' in window) speechSynthesis.speak(new SpeechSynthesisUtterance(''));
    document.removeEventListener('click', initSpeech);
}, { once: true });

// Запуск приложения
els.drawingToolsPanel.style.display = showDrawingTools ? 'flex' : 'none';
loadAllData();
startNatrAutoUpdate();