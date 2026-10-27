// ==========================================
// drawings.js — ИНСТРУМЕНТЫ РИСОВАНИЯ
// ==========================================

let isDrawingTrendLine = false, trendLinePreview = null;
let isMagnetEnabled = false, isAlertModeEnabled = false, magnetIndicator = null, activeAlerts = [];
let isTrendLineEnabled = false, trendLineStart = null, activeTrendlines = [];
let isPencilEnabled = false, pencilCtx = null, isDrawing = false, lastPencilPoint = null;
let isRulerEnabled = false, isRulerDragging = false, isRulerMiddleClickDrag = false;
let rulerStartPoint = null, rulerCurrentPoint = null, rulerFixedMeasurement = null;
let selectedDrawing = null; // { type, index, id?, handle }
let isDraggingDrawing = false;
let dragStart = null;
let gridEnabled = localStorage.getItem('gridEnabled') !== 'false';
let isEraserEnabled = false;
let trendLineHotkeyActive = false;
let horizontalLineHotkeyActive = false;
let pencilHotkeyActive = false;
let isHorizontalLineEnabled = false, activeHorizontalLines = [];
let pencilStrokes = [];
let currentStroke = null;
let horizontalLinePreview = null;

window.savedTrendLines = {};
window.savedHorizontalLines = {};
window.savedPencilDrawings = {};

try {
    window.savedTrendLines = JSON.parse(localStorage.getItem('savedTrendLines') || '{}');
    window.savedHorizontalLines = JSON.parse(localStorage.getItem('savedHorizontalLines') || '{}');
    window.savedPencilDrawings = JSON.parse(localStorage.getItem('savedPencilDrawings') || '{}');
} catch(e) {
    window.savedTrendLines = {};
    window.savedHorizontalLines = {};
    window.savedPencilDrawings = {};
}

if (localStorage.getItem('magnetEnabled') !== null) isMagnetEnabled = localStorage.getItem('magnetEnabled') === 'true';
if (localStorage.getItem('showDrawingTools') !== null) showDrawingTools = localStorage.getItem('showDrawingTools') === 'true';

function clearSpecificDrawings(type) {
    if (type === 'alerts') {
        if (currentSymbol) AlertManager.clearSymbol(currentSymbol);
    } else if (type === 'trendlines') {
        if (currentSymbol) window.savedTrendLines[currentSymbol] = [];
        activeTrendlines = [];
        redrawAllPersistentDrawings();
    } else if (type === 'horizontalLines') {
        if (currentSymbol) window.savedHorizontalLines[currentSymbol] = [];
        activeHorizontalLines = [];
        redrawAllPersistentDrawings();
    } else if (type === 'pencil') {
        if (currentSymbol) window.savedPencilDrawings[currentSymbol] = [];
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

    if (currentSymbol) {
        localStorage.setItem('savedTrendLines', JSON.stringify(window.savedTrendLines));
        localStorage.setItem('savedHorizontalLines', JSON.stringify(window.savedHorizontalLines));
        localStorage.setItem('savedPencilDrawings', JSON.stringify(window.savedPencilDrawings));
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

function clearAllDrawings() {
    clearSpecificDrawings('alerts');
    clearSpecificDrawings('trendlines');
    clearSpecificDrawings('horizontalLines');
    clearSpecificDrawings('pencil');
    clearSpecificDrawings('ruler');
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
        finishPencilStroke();
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

function initPencilCanvas() {
    if (!chart || !els.pencilCanvas) return;
    const rect = els.chartWrapper.getBoundingClientRect();
    els.pencilCanvas.width = rect.width;
    els.pencilCanvas.height = rect.height;
    pencilCtx = els.pencilCanvas.getContext('2d');
    redrawAllPersistentDrawings();

    // 🔹 ИСПРАВЛЕНИЕ: добавляем обработчики событий, чтобы isDrawing корректно переключался.
    // Без этого handlePencilDraw всегда сразу делал return, и рисование было невозможно.
    els.pencilCanvas.onmousedown = (e) => {
        if (!isPencilEnabled) return;
        isDrawing = true;
    };
    els.pencilCanvas.onmouseup = () => {
        if (!isPencilEnabled) return;
        finishPencilStroke();
        if (isPencilEnabled) {
            isPencilEnabled = false;
            updateToolUI('pencilBtn', false);
            if (chart) chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: true } });
            redrawAllPersistentDrawings();
        }
    };
    els.pencilCanvas.onmouseleave = () => {
        if (!isPencilEnabled) return;
        finishPencilStroke();
        if (isPencilEnabled) {
            isPencilEnabled = false;
            updateToolUI('pencilBtn', false);
            if (chart) chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: true } });
            redrawAllPersistentDrawings();
        }
    };
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

    activeHorizontalLines.forEach(hl => {
        const y = candleSeries.priceToCoordinate(hl.price);
        if (y === null) return;
        const x1 = hl.time ? getXByTime(hl.time) : null;
        const startX = (x1 !== null && x1 > 0) ? x1 : 0;
        pencilCtx.strokeStyle = hl.color || '#f59e0b';
        pencilCtx.lineWidth = 2;
        pencilCtx.setLineDash([5, 5]);
        pencilCtx.beginPath();
        pencilCtx.moveTo(startX, y);
        pencilCtx.lineTo(els.pencilCanvas.width, y);
        pencilCtx.stroke();
        pencilCtx.fillStyle = hl.color || '#f59e0b';
        pencilCtx.font = '10px sans-serif';
        pencilCtx.textAlign = 'right';
        pencilCtx.fillText(hl.price.toFixed(currentPrecision), els.pencilCanvas.width - 4, y - 4);
    });
    pencilCtx.setLineDash([]);
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

    if (selectedDrawing) {
        pencilCtx.fillStyle = '#ffffff';
        pencilCtx.strokeStyle = '#3b82f6';
        pencilCtx.lineWidth = 2;
        const drawHandle = (hx, hy) => {
            if (hx === null || hy === null) return;
            pencilCtx.beginPath();
            pencilCtx.arc(hx, hy, 5, 0, Math.PI * 2);
            pencilCtx.fill();
            pencilCtx.stroke();
        };
        if (selectedDrawing.type === 'horizontal') {
            const hl = activeHorizontalLines[selectedDrawing.index];
            if (hl) {
                const hx = hl.time ? getXByTime(hl.time) : 0;
                const hy = candleSeries.priceToCoordinate(hl.price);
                drawHandle(hx !== null ? hx : 0, hy);
            }
        } else if (selectedDrawing.type === 'alert') {
            const alerts = (currentSymbol && savedAlerts[currentSymbol]) ? savedAlerts[currentSymbol] : [];
            const alert = alerts.find(a => a.id === selectedDrawing.id);
            if (alert) {
                const hy = candleSeries.priceToCoordinate(alert.price);
                drawHandle(els.pencilCanvas.width / 2, hy);
            }
        } else if (selectedDrawing.type === 'trendline') {
            const tl = activeTrendlines[selectedDrawing.index];
            if (tl) {
                const x1 = getXByTime(tl.time1), y1 = candleSeries.priceToCoordinate(tl.price1);
                const x2 = getXByTime(tl.time2), y2 = candleSeries.priceToCoordinate(tl.price2);
                drawHandle(x1, y1);
                drawHandle(x2, y2);
                if (x1 !== null && y1 !== null && x2 !== null && y2 !== null) {
                    drawHandle((x1 + x2) / 2, (y1 + y2) / 2);
                }
            }
        }
    }

    redrawPencilStrokes();
    pencilCtx.setLineDash([]);
}

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

    const savedList = (window.savedAlerts && window.savedAlerts[currentSymbol]) || [];
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
            activeHorizontalLines.splice(i, 1);
            if (currentSymbol) {
                window.savedHorizontalLines[currentSymbol] = activeHorizontalLines.map(h => ({
                    price: h.price,
                    color: h.color || '#f59e0b',
                    time: h.time
                }));
                localStorage.setItem('savedHorizontalLines', JSON.stringify(window.savedHorizontalLines));
            }
            redrawAllPersistentDrawings();
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

function isAnyDrawingToolEnabled() {
    return isAlertModeEnabled || isTrendLineEnabled || isHorizontalLineEnabled || isPencilEnabled || isRulerEnabled || isEraserEnabled;
}

function findDrawingAt(x, y) {
    const threshold = 10;
    // Alerts (by price line Y)
    const alerts = (currentSymbol && savedAlerts[currentSymbol]) ? savedAlerts[currentSymbol] : [];
    for (let i = alerts.length - 1; i >= 0; i--) {
        const a = alerts[i];
        const ay = candleSeries.priceToCoordinate(a.price);
        if (ay !== null && Math.abs(ay - y) < threshold) {
            return { type: 'alert', index: i, id: a.id, handle: 'move' };
        }
    }
    // Horizontal lines
    for (let i = activeHorizontalLines.length - 1; i >= 0; i--) {
        const hl = activeHorizontalLines[i];
        const hy = candleSeries.priceToCoordinate(hl.price);
        if (hy !== null && Math.abs(hy - y) < threshold) {
            const hx = hl.time ? getXByTime(hl.time) : 0;
            if (hx === null || x >= hx - threshold) {
                return { type: 'horizontal', index: i, handle: 'move' };
            }
        }
    }
    // Trendlines
    for (let i = activeTrendlines.length - 1; i >= 0; i--) {
        const tl = activeTrendlines[i];
        const x1 = getXByTime(tl.time1), y1 = candleSeries.priceToCoordinate(tl.price1);
        const x2 = getXByTime(tl.time2), y2 = candleSeries.priceToCoordinate(tl.price2);
        if (x1 === null || y1 === null || x2 === null || y2 === null) continue;
        const dStart = Math.hypot(x - x1, y - y1);
        const dEnd = Math.hypot(x - x2, y - y2);
        if (dStart < 8) return { type: 'trendline', index: i, handle: 'start' };
        if (dEnd < 8) return { type: 'trendline', index: i, handle: 'end' };
        const dist = pointToLineDistance(x, y, x1, y1, x2, y2);
        if (dist < threshold) return { type: 'trendline', index: i, handle: 'move' };
    }
    return null;
}

function updateDrawing(sel, price, time) {
    if (!sel) return;
    if (sel.type === 'horizontal') {
        const hl = activeHorizontalLines[sel.index];
        if (hl) hl.price = price;
    } else if (sel.type === 'alert') {
        const alerts = (currentSymbol && savedAlerts[currentSymbol]) ? savedAlerts[currentSymbol] : [];
        const alert = alerts.find(a => a.id === sel.id);
        if (alert) {
            alert.price = price;
            if (chartAlertLines[alert.id]) {
                try { candleSeries.removePriceLine(chartAlertLines[alert.id]); } catch(e){}
                delete chartAlertLines[alert.id];
            }
            AlertManager.drawLine(alert);
        }
    } else if (sel.type === 'trendline') {
        const tl = activeTrendlines[sel.index];
        if (!tl) return;
        if (sel.handle === 'start') {
            tl.time1 = time; tl.price1 = price;
        } else if (sel.handle === 'end') {
            tl.time2 = time; tl.price2 = price;
        } else if (sel.handle === 'move') {
            if (dragStart && dragStart.orig) {
                const dPrice = price - dragStart.price;
                const dTime = time - dragStart.time;
                tl.price1 = dragStart.orig.price1 + dPrice;
                tl.price2 = dragStart.orig.price2 + dPrice;
                tl.time1 = dragStart.orig.time1 + dTime;
                tl.time2 = dragStart.orig.time2 + dTime;
            }
        }
    }
}

function saveDrawingChanges() {
    if (!currentSymbol) return;
    if (selectedDrawing && (selectedDrawing.type === 'horizontal' || selectedDrawing.type === 'trendline')) {
        saveCurrentDrawings(currentSymbol);
    }
    if (selectedDrawing && selectedDrawing.type === 'alert') {
        AlertManager.save();
    }
}

function handleChartClick(param) {
    if (!param.point || typeof param.point.y !== 'number') return;
    if (isEraserEnabled) { deleteLineAtPoint(param.point.x, param.point.y); if (isEraserEnabled) toggleEraser(); return; }
    if (isRulerEnabled) return;

    if (isAlertModeEnabled) {
        const price = candleSeries.coordinateToPrice(param.point.y);
        if (!price || isNaN(price)) return;
        AlertManager.add(currentSymbol, price);
        toggleAlertMode();
    }
    else if (isHorizontalLineEnabled) {
        const price = candleSeries.coordinateToPrice(param.point.y);
        const time = param.time || getTimeByX(param.point.x);
        if (!price || isNaN(price)) return;
        activeHorizontalLines.push({ price: price, color: '#f59e0b', time: time });

        if (currentSymbol) {
            window.savedHorizontalLines[currentSymbol] = activeHorizontalLines.map(hl => ({
                price: hl.price,
                color: hl.color || '#f59e0b',
                time: hl.time
            }));
            localStorage.setItem('savedHorizontalLines', JSON.stringify(window.savedHorizontalLines));
        }
        toggleHorizontalLine();
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

            if (currentSymbol) {
                window.savedTrendLines[currentSymbol] = activeTrendlines;
                localStorage.setItem('savedTrendLines', JSON.stringify(window.savedTrendLines));
            }
            toggleTrendLine();
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

function finishPencilStroke() {
    if (!isDrawing) return;
    if (currentStroke && currentStroke.length >= 2) {
        pencilStrokes.push(currentStroke);
        if (currentSymbol) {
            window.savedPencilDrawings[currentSymbol] = pencilStrokes;
            localStorage.setItem('savedPencilDrawings', JSON.stringify(window.savedPencilDrawings));
        }
    }
    isDrawing = false;
    currentStroke = null;
    lastPencilPoint = null;
}

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
                return lastCandle.time + indexDiff * secondsPerBar;
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

    (typeof getActiveAlertsFor === 'function' ? getActiveAlertsFor(currentSymbol) : []).forEach(a => {
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

function saveCurrentDrawings(symbol) {
    if (!symbol) return;
    window.savedTrendLines[symbol] = activeTrendlines;
    window.savedHorizontalLines[symbol] = activeHorizontalLines.map(hl => ({
        price: hl.price,
        color: hl.color || '#f59e0b',
        time: hl.time
    }));
    window.savedPencilDrawings[symbol] = pencilStrokes;

    localStorage.setItem('savedTrendLines', JSON.stringify(window.savedTrendLines));
    localStorage.setItem('savedHorizontalLines', JSON.stringify(window.savedHorizontalLines));
    localStorage.setItem('savedPencilDrawings', JSON.stringify(window.savedPencilDrawings));
}

function restoreDrawingsForSymbol(symbol) {
    activeTrendlines = window.savedTrendLines[symbol] || [];
    activeHorizontalLines = (window.savedHorizontalLines[symbol] || []).map(hl => ({
        price: hl.price,
        color: hl.color || '#f59e0b',
        time: hl.time || null
    }));
    pencilStrokes = window.savedPencilDrawings[symbol] || [];

    redrawAllPersistentDrawings();
}

function clearDrawingsForSymbol(symbol) {
    if (window.savedTrendLines[symbol]) delete window.savedTrendLines[symbol];
    if (window.savedHorizontalLines[symbol]) delete window.savedHorizontalLines[symbol];
    if (window.savedPencilDrawings[symbol]) delete window.savedPencilDrawings[symbol];
    localStorage.setItem('savedTrendLines', JSON.stringify(window.savedTrendLines));
    localStorage.setItem('savedHorizontalLines', JSON.stringify(window.savedHorizontalLines));
    localStorage.setItem('savedPencilDrawings', JSON.stringify(window.savedPencilDrawings));
}