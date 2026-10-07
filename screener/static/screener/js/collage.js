// ==========================================
// collage.js — КОЛЛАЖ ГРУПП МОНЕТ
// ==========================================

window.collageState = null;
let collageCharts = [];

function openCollage(colorId) {
    const symbols = Object.keys(coinColors).filter(s => coinColors[s] === colorId).sort();
    if (symbols.length < 2) return;
    window.collageState = { colorId, symbols, page: 0 };
    els.chartWrapper.style.display = 'none';
    els.chartHint.style.display = 'none';
    const titleWrap = document.getElementById('chart-title') ? document.getElementById('chart-title').parentElement : null;
    if (titleWrap) titleWrap.style.display = 'none';
    const resetBtn = document.querySelector('.chart-reset-btn');
    if (resetBtn) resetBtn.style.display = 'none';
    els.drawingToolsPanel.style.display = 'none';
    els.chartWatermark.style.display = 'none';
    els.pencilCanvas.style.display = 'none';
    els.rulerMeasurement.style.display = 'none';
    const wrap = document.getElementById('collageWrap');
    if (wrap) wrap.style.display = 'grid';
    renderCollagePage();
}

function exitCollage() {
    if (!window.collageState) return;
    destroyCollageCharts();
    window.collageState = null;
    const wrap = document.getElementById('collageWrap');
    if (wrap) { wrap.style.display = 'none'; wrap.innerHTML = ''; }
    const controls = document.getElementById('collageControls');
    if (controls) controls.style.display = 'none';
    els.chartWrapper.style.display = '';
    const titleWrap = document.getElementById('chart-title') ? document.getElementById('chart-title').parentElement : null;
    if (titleWrap) titleWrap.style.display = 'flex';
    const resetBtn = document.querySelector('.chart-reset-btn');
    if (resetBtn) resetBtn.style.display = '';
    els.drawingToolsPanel.style.display = showDrawingTools ? 'flex' : 'none';
    els.pencilCanvas.style.display = '';
    if (chart) chart.applyOptions({ width: els.chartWrapper.clientWidth, height: els.chartWrapper.clientHeight });
}

function openCollageFromModal(colorId) {
    const inst = bootstrap.Modal.getInstance(document.getElementById('coinGroupsModal'));
    if (inst) inst.hide();
    openCollage(colorId);
}

function collagePrevPage() {
    if (!window.collageState || window.collageState.page === 0) return;
    window.collageState.page--;
    renderCollagePage();
}

function collageNextPage() {
    if (!window.collageState) return;
    const pages = Math.ceil(window.collageState.symbols.length / 4);
    if (window.collageState.page < pages - 1) {
        window.collageState.page++;
        renderCollagePage();
    }
}

function updateCollageControls(pages) {
    const controls = document.getElementById('collageControls');
    if (!controls) return;
    controls.style.display = 'flex';
    const multi = pages > 1;
    document.getElementById('collagePrev').style.display = multi ? 'inline-block' : 'none';
    document.getElementById('collageNext').style.display = multi ? 'inline-block' : 'none';
    const info = document.getElementById('collagePageInfo');
    info.style.display = multi ? 'inline-block' : 'none';
    info.textContent = `${window.collageState.page + 1}/${pages}`;
}

function destroyCollageCharts() {
    for (const entry of collageCharts) {
        try { if (entry.ws) { entry.ws.onclose = null; entry.ws.close(); } } catch(e) {}
        try { entry.chart.remove(); } catch(e) {}
        try {
            const container = entry.container;
            if (container) {
                container.querySelectorAll('.collage-drawing-canvas').forEach(canvas => canvas.remove());
            }
        } catch(e) {}
    }
    collageCharts = [];
}

function renderCollagePage() {
    destroyCollageCharts();
    const wrap = document.getElementById('collageWrap');
    if (!wrap || !window.collageState) return;
    wrap.innerHTML = '';
    const perPage = 4;
    const pages = Math.ceil(window.collageState.symbols.length / perPage);
    const pageSymbols = window.collageState.symbols.slice(window.collageState.page * perPage, window.collageState.page * perPage + perPage);
    const count = pageSymbols.length;
    let cols, rows;
    if (count === 1) { cols = 1; rows = 1; }
    else if (count === 2) { cols = 2; rows = 1; }
    else { cols = 2; rows = 2; }
    wrap.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
    wrap.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
    wrap.style.gap = '2px';

    for (let i = 0; i < cols * rows; i++) {
        const cell = document.createElement('div');
        cell.style.cssText = 'position:relative; background:#0f0f0f; overflow:hidden; min-height:0; min-width:0;';
        const sym = pageSymbols[i];

        if (sym) {
            cell.innerHTML = `
                <div class="collage-label" id="collageLabel_${i}"></div>
                <div id="collageChart_${i}" style="width:100%;height:100%;"></div>
                <div class="collage-expand-btn" data-symbol="${sym}" title="Открыть полный график">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>
                    </svg>
                </div>
            `;

            const expandBtn = cell.querySelector('.collage-expand-btn');
            expandBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                exitCollage();
                openChart(sym);
            });
        } else {
            cell.innerHTML = '<div style="position:absolute; inset:0; display:flex; align-items:center; justify-content:center; color:#333333; font-size:11px; text-transform:uppercase; letter-spacing:1px;">—</div>';
        }
        wrap.appendChild(cell);
        if (sym) initCollageChart(i, sym);
    }
    updateCollageControls(pages);
}

function initCollageChart(index, symbol) {
    const container = document.getElementById('collageChart_' + index);
    if (!container) return;

    const chart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: container.clientHeight,
        layout: { background: { color: '#0f0f0f' }, textColor: '#666666', fontSize: 9 },
        grid: { vertLines: { color: '#141414' }, horzLines: { color: '#141414' } },
        timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#222222', rightOffset: 6, barSpacing: 4 },
        rightPriceScale: { borderColor: '#222222', scaleMargins: { top: 0.1, bottom: 0.2 } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        handleScroll: {
            mouseWheel: true,
            pressedMouseMove: true,
            horzTouchDrag: true,
            vertTouchDrag: false
        },
        handleScale: {
            mouseWheel: true,
            pinch: true,
            axisPressedMouseMove: true,
            axisDoubleClickReset: true
        },
    });

    const candleSeries = chart.addCandlestickSeries({
        upColor: '#22c55e', downColor: '#ef4444',
        borderVisible: false,
        wickUpColor: '#22c55e', wickDownColor: '#ef4444'
    });
    const volumeSeries = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume'
    });
    chart.priceScale('volume').applyOptions({ visible: false, scaleMargins: { top: 0.85, bottom: 0 } });

    // 🔹 ИСПРАВЛЕНИЕ: добавлено свойство data: null для хранения локальных данных графика
    const entry = { chart, candleSeries, volumeSeries, ws: null, symbol, container, data: null };
    collageCharts.push(entry);

    const coin = allCoins.find(c => c.symbol === symbol);
    const change = coin ? coin.change : 0;
    const label = document.getElementById('collageLabel_' + index);
    if (label) {
        label.innerHTML = `<span style="font-weight:700; color:#ffffff;">${symbol}</span> <span style="color:${change >= 0 ? '#22c55e' : '#ef4444'};">${change >= 0 ? '+' : ''}${change}%</span>`;
    }

    fetch(`/api/candles/${symbol}/?tf=${currentTF}`)
        .then(r => r.ok ? r.json() : [])
        .then(history => {
            if (!history || !history.length) return;
            const data = history.slice(-200).map(c => ({ ...c, time: safeTime(c.time) }));

            // 🔹 ИСПРАВЛЕНИЕ: сохраняем данные в entry для последующего расчёта координат
            entry.data = data;

            const first = data[0].close;
            const precision = first < 1 ? (first < 0.01 ? 8 : 5) : 2;
            const minMove = first < 1 ? (first < 0.01 ? 0.00000001 : 0.00001) : 0.01;
            candleSeries.applyOptions({ priceFormat: { type: 'price', precision, minMove } });
            candleSeries.setData(data);
            volumeSeries.setData(data.map(c => ({
                time: c.time,
                value: c.volume,
                color: c.close >= c.open ? 'rgba(200,200,200,0.5)' : 'rgba(80,80,80,0.6)'
            })));
            chart.timeScale().fitContent();

            // 🔹 ИСПРАВЛЕНИЕ: передаём entry.data в функцию отрисовки
            requestAnimationFrame(() => {
                drawCollageDrawings(chart, candleSeries, container, symbol, entry.data);
            });

            // 🔹 ИСПРАВЛЕНИЕ: передаём entry.data при перерисовке во время скролла/зума
            chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
                requestAnimationFrame(() => {
                    drawCollageDrawings(chart, candleSeries, container, symbol, entry.data);
                });
            });
        })
        .catch(() => {});

    const ws = new WebSocket(`wss://fstream.binance.com/market/ws/${symbol.toLowerCase()}usdt@kline_${currentTF}`);
    entry.ws = ws;
    ws.onmessage = (e) => {
        try {
            const d = JSON.parse(e.data);
            if (!d.k) return;
            const k = d.k;
            const candle = {
                time: Math.floor(k.t / 1000),
                open: parseFloat(k.o),
                high: parseFloat(k.h),
                low: parseFloat(k.l),
                close: parseFloat(k.c),
                volume: parseFloat(k.v)
            };
            candleSeries.update(candle);
            volumeSeries.update({
                time: candle.time,
                value: candle.volume,
                color: candle.close >= candle.open ? 'rgba(200,200,200,0.5)' : 'rgba(80,80,80,0.6)'
            });
        } catch (err) {}
    };
}

function drawCollageDrawings(chart, candleSeries, container, symbol, chartData) {
    if (typeof window.savedTrendLines === 'undefined' ||
        typeof window.savedHorizontalLines === 'undefined' ||
        typeof window.savedPencilDrawings === 'undefined') return;

    const horizontalLines = window.savedHorizontalLines[symbol] || [];
    const trendLines = window.savedTrendLines[symbol] || [];
    const pencilStrokes = window.savedPencilDrawings[symbol] || [];

    const hasAnyDrawings = horizontalLines.length > 0 || trendLines.length > 0 || pencilStrokes.length > 0;
    if (!hasAnyDrawings) return;

    // 1. Горизонтальные линии (через встроенный API)
    horizontalLines.forEach(hl => {
        try {
            candleSeries.createPriceLine({
                price: hl.price,
                color: (hl.color && typeof hl.color === 'string') ? hl.color : '#f59e0b80',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: false
            });
        } catch(e) {}
    });

    const needCanvas = trendLines.length > 0 || pencilStrokes.length > 0;
    if (!needCanvas) return;

    let canvas = container.querySelector('.collage-drawing-canvas');
    if (!canvas) {
        canvas = document.createElement('canvas');
        canvas.className = 'collage-drawing-canvas';
        canvas.style.cssText = 'position:absolute; top:0; left:0; width:100%; height:100%; pointer-events:none; z-index:5;';
        container.appendChild(canvas);
    }

    const rect = container.getBoundingClientRect();
    canvas.width = rect.width;
    canvas.height = rect.height;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // 🔹 ИСПРАВЛЕНИЕ: Умный хелпер для получения координат с резервным расчётом
    const getXY = (time, price) => {
        let x = chart.timeScale().timeToCoordinate(time);
        let y = candleSeries.priceToCoordinate(price);

        // Если нативный метод сработал, возвращаем результат
        if (x !== null && y !== null) return { x, y };

        // 🔹 Резервный алгоритм (как в drawings.js, но для локальных данных коллажа)
        if (chartData && chartData.length > 1) {
            const lastCandle = chartData[chartData.length - 1];
            const firstCandle = chartData[0];

            const lastX = chart.timeScale().timeToCoordinate(lastCandle.time);
            const firstX = chart.timeScale().timeToCoordinate(firstCandle.time);

            if (lastX !== null && firstX !== null) {
                const secondsPerBar = {'1m':60,'5m':300,'15m':900,'30m':1800,'1h':3600,'4h':14400}[currentTF] || 60;
                const timeDiff = time - lastCandle.time;
                const barsOffset = timeDiff / secondsPerBar;
                const totalBars = chartData.length - 1;
                const pixelsPerBar = (lastX - firstX) / totalBars;

                x = lastX + (barsOffset * pixelsPerBar);
            }
        }
        return { x, y };
    };

    // 2. Трендовые линии
    ctx.strokeStyle = '#f59e0b80';
    ctx.lineWidth = 1;
    ctx.setLineDash([]);
    trendLines.forEach(tl => {
        try {
            const p1 = getXY(tl.time1, tl.price1);
            const p2 = getXY(tl.time2, tl.price2);
            if (p1.x !== null && p1.y !== null && p2.x !== null && p2.y !== null) {
                ctx.beginPath();
                ctx.moveTo(p1.x, p1.y);
                ctx.lineTo(p2.x, p2.y);
                ctx.stroke();
            }
        } catch(e) {}
    });

    // 3. Карандашные рисунки
    if (pencilStrokes.length > 0) {
        ctx.strokeStyle = '#f59e0b80';
        ctx.lineWidth = 1.5;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.setLineDash([]);

        pencilStrokes.forEach(stroke => {
            if (!Array.isArray(stroke) || stroke.length < 2) return;
            ctx.beginPath();
            let started = false;
            for (const point of stroke) {
                const p = getXY(point.time, point.price);
                if (p.x === null || p.y === null) { started = false; continue; }
                if (!started) { ctx.moveTo(p.x, p.y); started = true; }
                else { ctx.lineTo(p.x, p.y); }
            }
            ctx.stroke();
        });
    }
}