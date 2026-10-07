// ==========================================
// drawings.js — ИНСТРУМЕНТЫ РИСОВАНИЯ
// Тренды, карандаш, линейка, ластик, магнит, горизонтальные линии
// ==========================================

// --- Переменные состояния инструментов ---
let isDrawingTrendLine = false, trendLinePreview = null;
let isMagnetEnabled = false, isAlertModeEnabled = false, magnetIndicator = null, activeAlerts = [];
let isTrendLineEnabled = false, trendLineStart = null, activeTrendlines = [];
let isPencilEnabled = false, pencilCtx = null, isDrawing = false, lastPencilPoint = null;
let isRulerEnabled = false, isRulerDragging = false, isRulerMiddleClickDrag = false;
let rulerStartPoint = null, rulerCurrentPoint = null, rulerFixedMeasurement = null;
let showDrawingTools = true;
let isEraserEnabled = false;
let trendLineHotkeyActive = false;
let horizontalLineHotkeyActive = false;
let pencilHotkeyActive = false;
let isHorizontalLineEnabled = false, activeHorizontalLines = [];
let pencilStrokes = [];
let currentStroke = null;

// --- Хранение рисунков по символам (для коллажа) ---
let savedTrendLines = {};      // symbol -> array of trendlines
let savedHorizontalLines = {}; // symbol -> array of horizontal lines  
let savedPencilDrawings = {};  // symbol -> array of pencil strokes

// Загрузить из localStorage
try {
    savedTrendLines = JSON.parse(localStorage.getItem('savedTrendLines') || '{}');
    savedHorizontalLines = JSON.parse(localStorage.getItem('savedHorizontalLines') || '{}');
    savedPencilDrawings = JSON.parse(localStorage.getItem('savedPencilDrawings') || '{}');
} catch(e) {
    savedTrendLines = {}; savedHorizontalLines = {}; savedPencilDrawings = {};
}

if (localStorage.getItem('magnetEnabled') !== null) isMagnetEnabled = localStorage.getItem('magnetEnabled') === 'true';
if (localStorage.getItem('showDrawingTools') !== null) showDrawingTools = localStorage.getItem('showDrawingTools') === 'true';

// ==========================================
// УТИЛИТЫ ИНСТРУМЕНТОВ
// ==========================================
function clearSpecificDrawings(type) {
    if (type === 'alerts') {
        if (currentSymbol) AlertManager.clearSymbol(currentSymbol);
    } else if (type === 'trendlines') {
        if (currentSymbol) savedTrendLines[currentSymbol] = [];
        activeTrendlines = [];
        redrawAllPersistentDrawings();
    } else if (type === 'horizontalLines') {
        if (currentSymbol) savedHorizontalLines[currentSymbol] = [];
        activeHorizontalLines.forEach(hl => { try { candleSeries.removePriceLine(hl.line); } catch(e){} });
        activeHorizontalLines = [];
    } else if (type === 'pencil') {
        if (currentSymbol) savedPencilDrawings[currentSymbol] = [];
        pencilStrokes = [];
        currentStroke = null;
        if (pencilCtx) pencilCtx.clearRect(0, 0, els.pencilCanvas.width, els.pencilCanvas.height);
    } else if (type === 'ruler') {
        isRulerDragging = false;
        rulerStartPoint = null;
        rulerCurrentPoint = null;
        rulerFixedMeasurement = null;
        els.rulerMeasurement.style.display = 'none';
        if (pencilCtx) pencilCtx.clearRect(0, 0, els.pencilCanvas.width, els.pencilCanvas.height);
    }
    
    // Сохраняем изменения в localStorage
    if (currentSymbol) {
        localStorage.setItem('savedTrendLines', JSON.stringify(savedTrendLines));
        localStorage.setItem('savedHorizontalLines', JSON.stringify(savedHorizontalLines));
        localStorage.setItem('savedPencilDrawings', JSON.stringify(savedPencilDrawings));
    }
}

function updateToolUI(btnId, isActive) {
    const btn = document.getElementById(btnId);
    if (btn) btn.classList.toggle('active', isActive);
}

// ==========================================
// СОХРАНЕНИЕ И ВОССТАНОВЛЕНИЕ РИСУНКОВ ПО СИМВОЛАМ
// ==========================================
function saveCurrentDrawings(symbol) {
    if (!symbol) return;
    
    // Сохраняем текущие рисунки для символа
    savedTrendLines[symbol] = activeTrendlines;
    savedHorizontalLines[symbol] = activeHorizontalLines;
    savedPencilDrawings[symbol] = pencilStrokes;
    
    // Сохраняем в localStorage
    localStorage.setItem('savedTrendLines', JSON.stringify(savedTrendLines));
    localStorage.setItem('savedHorizontalLines', JSON.stringify(savedHorizontalLines));
    localStorage.setItem('savedPencilDrawings', JSON.stringify(savedPencilDrawings));
}

function restoreDrawingsForSymbol(symbol) {
    // Восстанавливаем рисунки для символа
    activeTrendlines = savedTrendLines[symbol] || [];
    activeHorizontalLines = savedHorizontalLines[symbol] || [];
    pencilStrokes = savedPencilDrawings[symbol] || [];
    
    // Перерисовываем горизонтальные линии
    activeHorizontalLines.forEach(hl => {
        try { candleSeries.removePriceLine(hl.line); } catch(e){}
    });
    activeHorizontalLines.forEach(hl => {
        const line = candleSeries.createPriceLine({
            price: hl.price, color: hl.color || '#f59e0b', lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true,
            title: `${hl.price.toFixed(currentPrecision)}`
        });
        hl.line = line;
    });
    
    // Перерисовываем все рисунки
    redrawAllPersistentDrawings();
}

function clearDrawingsForSymbol(symbol) {
    if (savedTrendLines[symbol]) delete savedTrendLines[symbol];
    if (savedHorizontalLines[symbol]) delete savedHorizontalLines[symbol];
    if (savedPencilDrawings[symbol]) delete savedPencilDrawings[symbol];
    
    localStorage.setItem('savedTrendLines', JSON.stringify(savedTrendLines));
    localStorage.setItem('savedHorizontalLines', JSON.stringify(savedHorizontalLines));
    localStorage.setItem('savedPencilDrawings', JSON.stringify(savedPencilDrawings));
}

function toggleDrawingToolsVisibility() {
    showDrawingTools = !showDrawingTools;
    els.drawingToolsPanel.style.display = showDrawingTools ? 'flex' : 'none';
    localStorage.setItem('showDrawingTools', showDrawingTools);
}

function clearAllDrawings() {
    clearSpecificDrawings('alerts');
    clearSpecificDrawings('trendlines');
    clearSpecificDrawings('horizontalLines');
    clearSpecificDrawings('pencil');
    clearSpecificDrawings('ruler');
}

// ==========================================
// TOGGLE ИНСТРУМЕНТОВ
// ==========================================
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
        isTrendLineEnabled = false; isPencilEnabled = false; isRulerEnabled = false; isHorizontalLineEnabled = false; isEraserEnabled = false;
        updateToolUI('trendLineBtn', false); updateToolUI('pencilBtn', false); updateToolUI('rulerBtn', false);
        updateToolUI('horizontalLineBtn', false); updateToolUI('eraserBtn', false);
    }
}

function toggleTrendLine() {
    isTrendLineEnabled = !isTrendLineEnabled;
    updateToolUI('trendLineBtn', isTrendLineEnabled);
    if (isTrendLineEnabled) {
        isAlertModeEnabled = false; isPencilEnabled = false; isRulerEnabled = false; isHorizontalLineEnabled = false; isEraserEnabled = false;
        updateToolUI('alertBtn', false); updateToolUI('pencilBtn', false); updateToolUI('rulerBtn', false);
        updateToolUI('horizontalLineBtn', false); updateToolUI('eraserBtn', false);
        if (chart) chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: false } });
    } else {
        trendLineStart = null; isDrawingTrendLine = false; trendLinePreview = null;
        if (chart) chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: true } });
        redrawAllPersistentDrawings();
    }
}

function toggleHorizontalLine() {
    isHorizontalLineEnabled = !isHorizontalLineEnabled;
    updateToolUI('horizontalLineBtn', isHorizontalLineEnabled);
    if (isHorizontalLineEnabled) {
        isAlertModeEnabled = false; isTrendLineEnabled = false; isPencilEnabled = false; isRulerEnabled = false; isEraserEnabled = false;
        updateToolUI('alertBtn', false); updateToolUI('trendLineBtn', false); updateToolUI('pencilBtn', false);
        updateToolUI('rulerBtn', false); updateToolUI('eraserBtn', false);
        if (chart) chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: false } });
    } else {
        horizontalLinePreview = null;
        if (chart) chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: true } });
        redrawAllPersistentDrawings();
    }
}

function togglePencil() {
    isPencilEnabled = !isPencilEnabled;
    updateToolUI('pencilBtn', isPencilEnabled);
    if (isPencilEnabled) {
        isAlertModeEnabled = false; isTrendLineEnabled = false; isRulerEnabled = false; isHorizontalLineEnabled = false; isEraserEnabled = false;
        updateToolUI('alertBtn', false); updateToolUI('trendLineBtn', false); updateToolUI('rulerBtn', false);
        updateToolUI('horizontalLineBtn', false); updateToolUI('eraserBtn', false);
        if (chart) chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: false } });
        initPencilCanvas();
    } else {
        isDrawing = false; lastPencilPoint = null;
        if (chart) chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: true } });
        redrawAllPersistentDrawings();
    }
}

function toggleRuler() {
    isRulerEnabled = !isRulerEnabled;
    updateToolUI('rulerBtn', isRulerEnabled);
    if (isRulerEnabled) {
        isAlertModeEnabled = false; isTrendLineEnabled = false; isPencilEnabled = false; isHorizontalLineEnabled = false; isEraserEnabled = false;
        updateToolUI('alertBtn', false); updateToolUI('trendLineBtn', false); updateToolUI('pencilBtn', false);
        updateToolUI('horizontalLineBtn', false); updateToolUI('eraserBtn', false);
        if (chart) chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: false } });
    } else {
        if (chart) chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: true } });
        clearSpecificDrawings('ruler');
    }
}

function toggleEraser() {
    isEraserEnabled = !isEraserEnabled;
    updateToolUI('eraserBtn', isEraserEnabled);
    if (isEraserEnabled) {
        isAlertModeEnabled = false; isTrendLineEnabled = false; isPencilEnabled = false;
        isRulerEnabled = false; isHorizontalLineEnabled = false;
        updateToolUI('alertBtn', false); updateToolUI('trendLineBtn', false); updateToolUI('pencilBtn', false);
        updateToolUI('rulerBtn', false); updateToolUI('horizontalLineBtn', false);
        if (chart) chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: false } });
    } else {
        if (chart) chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: true } });
    }
}

// ==========================================
// КООРДИНАТНЫЕ ПРЕОБРАЗОВАНИЯ
// ==========================================
function initPencilCanvas() {
    if (!chart || !els.pencilCanvas) return;
    const rect = els.chartWrapper.getBoundingClientRect();
    els.pencilCanvas.width = rect.width;
    els.pencilCanvas.height = rect.height;
    pencilCtx = els.pencilCanvas.getContext('2d');
    redrawAllPersistentDrawings();
}

function getTimeByX(x) {
    let time = chart.timeScale().coordinateToTime(x);
    if (time !== null) return time;
    const logicalIndex = getLogicalIndexByX(x);
    if (logicalIndex === null) return null;
    const candles = window.candleData || [];
    if (candles.length === 0) return null;
    const lastCandle = candles[candles.length - 1];
    const secondsPerBar = {'1m':60,'5m':300,'15m':900,'30m':1800,'1h':3600,'4h':14400}[currentTF] || 60;
    const indexDiff = logicalIndex - (candles.length - 1);
    return lastCandle.time + (indexDiff * secondsPerBar);
}

function getLogicalIndexByX(x) {
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
    const lastIndex = candles.length - 1;
    const barsOffset = (x - lastCandleX) / pixelsPerBar;
    return lastIndex + barsOffset;
}

function getXByTime(time) {
    let x = chart.timeScale().timeToCoordinate(time);
    if (x !== null) return x;
    const candles = window.candleData || [];
    if (candles.length === 0) return null;
    const lastCandle = candles[candles.length - 1];
    const lastCandleX = chart.timeScale().timeToCoordinate(lastCandle.time);
    if (lastCandleX === null) return null;
    const timeDiff = time - lastCandle.time;
    const secondsPerBar = {'1m':60,'5m':300,'15m':900,'30m':1800,'1h':3600,'4h':14400}[currentTF] || 60;
    const barsOffset = timeDiff / secondsPerBar;
    const visibleRange = chart.timeScale().getVisibleLogicalRange();
    if (!visibleRange || visibleRange.to === visibleRange.from) return null;
    const chartWidth = els.chartWrapper.clientWidth;
    const pixelsPerLogicalUnit = chartWidth / (visibleRange.to - visibleRange.from);
    return lastCandleX + (barsOffset * pixelsPerLogicalUnit);
}

// ==========================================
// ОТРИСОВКА
// ==========================================
function redrawPencilStrokes() {
    if (!pencilCtx || !chart || !candleSeries) return;
    pencilCtx.strokeStyle = '#f59e0b';
    pencilCtx.lineWidth = 2;
    pencilCtx.lineCap = 'round';
    pencilCtx.lineJoin = 'round';
    const drawStroke = (stroke) => {
        if (stroke.length < 2) return;
        pencilCtx.beginPath();
        let started = false;
        for (const point of stroke) {
            const x = getXByTime(point.time);
            const y = candleSeries.priceToCoordinate(point.price);
            if (x === null || y === null) { started = false; continue; }
            if (!started) { pencilCtx.moveTo(x, y); started = true; }
            else { pencilCtx.lineTo(x, y); }
        }
        pencilCtx.stroke();
    };
    pencilStrokes.forEach(drawStroke);
    if (currentStroke && currentStroke.length >= 2) drawStroke(currentStroke);
}

function drawRulerRectangle(start, end) {
    const x1 = getXByTime(start.time);
    const y1 = candleSeries.priceToCoordinate(start.price);
    const x2 = getXByTime(end.time);
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
    pencilCtx.strokeStyle = '#3b82f6';
    pencilCtx.lineWidth = 2;
    pencilCtx.setLineDash([5, 5]);
    activeTrendlines.forEach(tl => {
        const x1 = getXByTime(tl.time1);
        const x2 = getXByTime(tl.time2);
        const y1 = candleSeries.priceToCoordinate(tl.price1);
        const y2 = candleSeries.priceToCoordinate(tl.price2);
        if (x1 !== null && y1 !== null && x2 !== null && y2 !== null) {
            pencilCtx.beginPath();
            pencilCtx.moveTo(x1, y1);
            pencilCtx.lineTo(x2, y2);
            pencilCtx.stroke();
        }
    });
    if (isDrawingTrendLine && trendLinePreview) {
        const x1 = getXByTime(trendLinePreview.time1);
        const x2 = getXByTime(trendLinePreview.time2);
        const y1 = candleSeries.priceToCoordinate(trendLinePreview.price1);
        const y2 = candleSeries.priceToCoordinate(trendLinePreview.price2);
        if (x1 !== null && y1 !== null && x2 !== null && y2 !== null) {
            pencilCtx.strokeStyle = 'rgba(59, 130, 246, 0.7)';
            pencilCtx.lineWidth = 1.5;
            pencilCtx.setLineDash([3, 3]);
            pencilCtx.beginPath();
            pencilCtx.moveTo(x1, y1);
            pencilCtx.lineTo(x2, y2);
            pencilCtx.stroke();
        }
    }
    if (isRulerDragging && rulerStartPoint && rulerCurrentPoint) drawRulerRectangle(rulerStartPoint, rulerCurrentPoint);
    if (rulerFixedMeasurement) drawRulerRectangle(rulerFixedMeasurement.start, rulerFixedMeasurement.end);
    redrawPencilStrokes();
    pencilCtx.setLineDash([]);
}

// ==========================================
// УДАЛЕНИЕ (ЛАСТИК)
// ==========================================
function pointToLineDistance(px, py, x1, y1, x2, y2) {
    const A = px - x1; const B = py - y1; const C = x2 - x1; const D = y2 - y1;
    const dot = A * C + B * D;
    const lenSq = C * C + D * D;
    let param = -1;
    if (lenSq !== 0) param = dot / lenSq;
    let xx, yy;
    if (param < 0) { xx = x1; yy = y1; }
    else if (param > 1) { xx = x2; yy = y2; }
    else { xx = x1 + param * C; yy = y1 + param * D; }
    const dx = px - xx; const dy = py - yy;
    return Math.sqrt(dx * dx + dy * dy);
}

function deleteLineAtPoint(x, y) {
    const clickPrice = candleSeries.coordinateToPrice(y);
    if (!clickPrice) return;
    const threshold = 50;
    const savedList = savedAlerts[currentSymbol] || [];
    for (let i = savedList.length - 1; i >= 0; i--) {
        const alert = savedList[i];
        const alertY = candleSeries.priceToCoordinate(alert.price);
        if (alertY && Math.abs(alertY - y) < threshold) {
            AlertManager.remove(currentSymbol, alert.id);
            return;
        }
    }
    for (let i = activeHorizontalLines.length - 1; i >= 0; i--) {
        const hl = activeHorizontalLines[i];
        const hlY = candleSeries.priceToCoordinate(hl.price);
        if (hlY && Math.abs(hlY - y) < threshold) {
            try { candleSeries.removePriceLine(hl.line); } catch(e) {}
            activeHorizontalLines.splice(i, 1);
            return;
        }
    }
    for (let i = activeTrendlines.length - 1; i >= 0; i--) {
        const tl = activeTrendlines[i];
        const x1 = getXByTime(tl.time1);
        const y1 = candleSeries.priceToCoordinate(tl.price1);
        const x2 = getXByTime(tl.time2);
        const y2 = candleSeries.priceToCoordinate(tl.price2);
        if (x1 !== null && y1 !== null && x2 !== null && y2 !== null) {
            const distance = pointToLineDistance(x, y, x1, y1, x2, y2);
            if (distance < threshold) {
                activeTrendlines.splice(i, 1);
                redrawAllPersistentDrawings();
                return;
            }
        }
    }
    for (let i = pencilStrokes.length - 1; i >= 0; i--) {
        const stroke = pencilStrokes[i];
        for (const point of stroke) {
            const px = getXByTime(point.time);
            const py = candleSeries.priceToCoordinate(point.price);
            if (px !== null && py !== null) {
                const distance = Math.sqrt((px - x) ** 2 + (py - y) ** 2);
                if (distance < threshold) {
                    pencilStrokes.splice(i, 1);
                    redrawAllPersistentDrawings();
                    return;
                }
            }
        }
    }
}

// ==========================================
// ОБРАБОТЧИКИ СОБЫТИЙ ГРАФИКА
// ==========================================
function handleChartClick(param) {
    if (!param.point || typeof param.point.y !== 'number') return;
    if (isEraserEnabled) { deleteLineAtPoint(param.point.x, param.point.y); return; }
    if (isRulerEnabled) return;
    if (isAlertModeEnabled) {
        const price = candleSeries.coordinateToPrice(param.point.y);
        if (!price || isNaN(price)) return;
        AlertManager.add(currentSymbol, price);
    }
    else if (isHorizontalLineEnabled) {
        const price = candleSeries.coordinateToPrice(param.point.y);
        if (!price || isNaN(price)) return;
        const line = candleSeries.createPriceLine({
            price: price, color: '#f59e0b', lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true,
            title: `${price.toFixed(currentPrecision)}`
        });
        activeHorizontalLines.push({ price: price, line: line });
        
        // Сохраняем горизонтальные линии для текущего символа
        if (currentSymbol) {
            savedHorizontalLines[currentSymbol] = activeHorizontalLines;
            localStorage.setItem('savedHorizontalLines', JSON.stringify(savedHorizontalLines));
        }
    }
    else if (isTrendLineEnabled) {
        const price = candleSeries.coordinateToPrice(param.point.y);
        const time = param.time || getTimeByX(param.point.x);
        const logicalIndex = getLogicalIndexByX(param.point.x);
        if (!price || isNaN(price) || !time) return;
        if (!isDrawingTrendLine) {
            trendLineStart = { time, price, logicalIndex, x: param.point.x, y: param.point.y };
            isDrawingTrendLine = true;
            trendLinePreview = { time1: time, price1: price, logicalIndex1: logicalIndex, time2: time, price2: price, logicalIndex2: logicalIndex };
        } else {
            activeTrendlines.push({
                time1: trendLineStart.time, price1: trendLineStart.price, logicalIndex1: trendLineStart.logicalIndex,
                time2: time, price2: price, logicalIndex2: logicalIndex
            });
            isDrawingTrendLine = false;
            trendLineStart = null;
            trendLinePreview = null;
            redrawAllPersistentDrawings();
            
            // Сохраняем трендовые линии для текущего символа
            if (currentSymbol) {
                savedTrendLines[currentSymbol] = activeTrendlines;
                localStorage.setItem('savedTrendLines', JSON.stringify(savedTrendLines));
            }
        }
    }
}

function handlePencilDraw(param) {
    if (!isPencilEnabled || !isDrawing || !pencilCtx || !param.point) return;
    const price = candleSeries.coordinateToPrice(param.point.y);
    const time = param.time || getTimeByX(param.point.x);
    const logicalIndex = getLogicalIndexByX(param.point.x);
    if (!price || !time) { lastPencilPoint = param.point; return; }
    if (!currentStroke) currentStroke = [{ time, price, logicalIndex }];
    else currentStroke.push({ time, price, logicalIndex });
    if (lastPencilPoint) {
        pencilCtx.strokeStyle = '#f59e0b'; pencilCtx.lineWidth = 2;
        pencilCtx.lineCap = 'round'; pencilCtx.lineJoin = 'round';
        pencilCtx.beginPath(); pencilCtx.moveTo(lastPencilPoint.x, lastPencilPoint.y);
        pencilCtx.lineTo(param.point.x, param.point.y); pencilCtx.stroke();
    }
    lastPencilPoint = param.point;
}

// ==========================================
// ЛИНЕЙКА — ПОКАЗ ИЗМЕРЕНИЙ
// ==========================================
function showRulerMeasurement(start, end) {
    if (!start || !end || !candleSeries) return;
    const priceDiff = Math.abs(end.price - start.price);
    const pricePercent = ((priceDiff / start.price) * 100).toFixed(2);
    const direction = end.price >= start.price ? '↑' : '↓';
    const color = end.price >= start.price ? '#22c55e' : '#ef4444';
    const candles = window.candleData || [];
    const lastRealCandle = candles[candles.length - 1];
    const lastRealTime = lastRealCandle ? lastRealCandle.time : 0;
    const getTimeValue = (point) => {
        if (!point) return 0;
        if (typeof point.time === 'number') return point.time;
        if (point.time && typeof point.time === 'object' && point.time.timestamp) return point.time.timestamp;
        if (point.logicalIndex !== undefined) {
            const lastCandle = candles[candles.length - 1];
            if (lastCandle) {
                const secondsPerBar = {'1m':60,'5m':300,'15m':900,'30m':1800,'1h':3600,'4h':14400}[currentTF] || 60;
                const indexDiff = point.logicalIndex - (candles.length - 1);
                return lastCandle.time + (indexDiff * secondsPerBar);
            }
        }
        return 0;
    };
    const startTime = getTimeValue(start);
    const endTime = getTimeValue(end);
    const startInRealArea = startTime <= lastRealTime && startTime > 0;
    const endInRealArea = endTime <= lastRealTime && endTime > 0;
    const bothInRealArea = startInRealArea && endInRealArea;
    let barsCount = 0;
    let totalVolume = 0;
    let maxPrice = '-';
    let minPrice = '-';
    let hasRealData = false;
    const rangeStart = Math.min(startTime, endTime);
    const rangeEnd = Math.max(startTime, endTime);
    if (rangeStart > 0 && rangeEnd > 0) {
        const rangeCandles = candles.filter(c => {
            const candleTime = typeof c.time === 'number' ? c.time : (c.time && c.time.timestamp ? c.time.timestamp : 0);
            return candleTime >= rangeStart && candleTime <= rangeEnd && candleTime <= lastRealTime;
        });
        if (rangeCandles.length > 0) {
            hasRealData = true;
            barsCount = rangeCandles.length;
            let highest = -Infinity;
            let lowest = Infinity;
            rangeCandles.forEach(candle => {
                if (candle.high > highest) highest = candle.high;
                if (candle.low < lowest) lowest = candle.low;
                totalVolume += candle.volume || 0;
            });
            maxPrice = highest.toFixed(currentPrecision);
            minPrice = lowest.toFixed(currentPrecision);
        }
    }
    const formatTime = (t) => {
        if (!t || t === 0) return '---';
        const date = new Date(t * 1000);
        return date.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
    };
    const volumeFormatted = totalVolume >= 1000000 ? `${(totalVolume / 1000000).toFixed(2)}M` :
                            totalVolume >= 1000 ? `${(totalVolume / 1000).toFixed(1)}K` :
                            totalVolume > 0 ? totalVolume.toFixed(2) : '0';
    if (hasRealData) {
        els.rulerMeasurement.innerHTML = `<div style="font-weight:700; color:${color}; margin-bottom:8px; font-size:13px;">${direction} ${pricePercent}% | ${priceDiff.toFixed(currentPrecision)}</div><div style="font-size:11px; color:#d1d5db; line-height:1.6;"><div style="display:flex; justify-content:space-between; margin-bottom:4px;"><span style="color:#94a3b8;">Бары:</span><span style="font-weight:600;">${barsCount}</span></div><div style="display:flex; justify-content:space-between; margin-bottom:4px;"><span style="color:#94a3b8;">Цена:</span><span style="font-weight:600;">${start.price.toFixed(currentPrecision)} → ${end.price.toFixed(currentPrecision)}</span></div><div style="display:flex; justify-content:space-between; margin-bottom:4px;"><span style="color:#94a3b8;">Изменение:</span><span style="font-weight:600; color:${color};">${direction} ${pricePercent}%</span></div><div style="display:flex; justify-content:space-between; margin-bottom:4px;"><span style="color:#94a3b8;">Объем:</span><span style="font-weight:600;">${volumeFormatted}</span></div><div style="border-top:1px solid #475569; margin-top:6px; padding-top:6px;"><div style="display:flex; justify-content:space-between; font-size:10px; color:#94a3b8;"><span>Max: <span style="color:#22c55e;">${maxPrice}</span></span><span>Min: <span style="color:#ef4444;">${minPrice}</span></span></div></div><div style="font-size:9px; color:#6b7280; margin-top:4px; text-align:center;">${formatTime(startTime)} → ${formatTime(endTime)}</div>${!bothInRealArea ? '<div style="font-size:9px; color:#f59e0b; margin-top:4px; text-align:center; font-style:italic;">Часть в пустой зоне</div>' : ''}</div>`;
    } else {
        els.rulerMeasurement.innerHTML = `<div style="font-weight:700; color:${color}; margin-bottom:8px; font-size:13px;">${direction} ${pricePercent}% | ${priceDiff.toFixed(currentPrecision)}</div><div style="font-size:11px; color:#d1d5db; line-height:1.6;"><div style="display:flex; justify-content:space-between; margin-bottom:4px;"><span style="color:#94a3b8;">Цена:</span><span style="font-weight:600;">${start.price.toFixed(currentPrecision)} → ${end.price.toFixed(currentPrecision)}</span></div><div style="display:flex; justify-content:space-between; margin-bottom:4px;"><span style="color:#94a3b8;">Изменение:</span><span style="font-weight:600; color:${color};">${direction} ${pricePercent}%</span></div><div style="border-top:1px solid #475569; margin-top:6px; padding-top:6px; text-align:center;"><div style="font-size:9px; color:#f59e0b; font-style:italic;">Зона будущих свечей</div></div><div style="font-size:9px; color:#6b7280; margin-top:4px; text-align:center;">${formatTime(startTime)} → ${formatTime(endTime)}</div></div>`;
    }
    const measurementWidth = 230;
    const measurementHeight = 220;
    const chartWidth = els.chartWrapper.clientWidth;
    const chartHeight = els.chartWrapper.clientHeight;
    let displayX = end.x - measurementWidth - 15;
    if (displayX < 10) displayX = 10;
    let displayY = end.y - (measurementHeight / 2);
    if (displayY < 10) displayY = 10;
    if (displayY + measurementHeight > chartHeight - 10) displayY = chartHeight - measurementHeight - 10;
    els.rulerMeasurement.style.left = `${displayX}px`;
    els.rulerMeasurement.style.top = `${displayY}px`;
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
    getActiveAlertsFor(currentSymbol).forEach(a => {
        magnetPoints.push({ type: 'alert', price: a.price, distance: Math.abs(a.price - priceAtCursor) });
    });
    magnetPoints.sort((a, b) => a.distance - b.distance);
    const nearest = magnetPoints[0];
    const snapX = chart.timeScale().timeToCoordinate(nearestCandle.time);
    const snapY = candleSeries.priceToCoordinate(nearest.price);
    if (snapX !== null && snapY !== null) {
        magnetIndicator.style.display = 'block';
        magnetIndicator.style.left = `${snapX - 3}px`;
        magnetIndicator.style.top = `${snapY - 3}px`;
        magnetIndicator.classList.toggle('alert-magnet', nearest.type === 'alert');
    } else {
        magnetIndicator.style.display = 'none';
    }
}

let horizontalLinePreview = null;