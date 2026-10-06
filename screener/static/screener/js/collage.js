// ==========================================
// collage.js — КОЛЛАЖ ГРУПП МОНЕТ
// Мини-графики монет одной цветовой группы
// ==========================================

// --- Переменные состояния ---
let collageState = null;   // { colorId, symbols, page }
let collageCharts = [];    // { chart, candleSeries, volumeSeries, ws, symbol, container }

// ==========================================
// ОТКРЫТИЕ КОЛЛАЖА
// ==========================================
function openCollage(colorId) {
    const symbols = Object.keys(coinColors).filter(s => coinColors[s] === colorId).sort();
    if (symbols.length < 2) return;

    collageState = { colorId, symbols, page: 0 };
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
    if (!collageState) return;
    destroyCollageCharts();
    collageState = null;

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

// ==========================================
// НАВИГАЦИЯ ПО СТРАНИЦАМ
// ==========================================
function collagePrevPage() {
    if (!collageState || collageState.page === 0) return;
    collageState.page--;
    renderCollagePage();
}

function collageNextPage() {
    if (!collageState) return;
    const pages = Math.ceil(collageState.symbols.length / 4);
    if (collageState.page < pages - 1) {
        collageState.page++;
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
    info.textContent = `${collageState.page + 1}/${pages}`;
}

// ==========================================
// УНИЧТОЖЕНИЕ ГРАФИКОВ
// ==========================================
function destroyCollageCharts() {
    for (const entry of collageCharts) {
        try { if (entry.ws) { entry.ws.onclose = null; entry.ws.close(); } } catch(e) {}
        try { entry.chart.remove(); } catch(e) {}
    }
    collageCharts = [];
}

// ==========================================
// РЕНДЕР СТРАНИЦЫ
// ==========================================
function renderCollagePage() {
    destroyCollageCharts();

    const wrap = document.getElementById('collageWrap');
    if (!wrap || !collageState) return;

    wrap.innerHTML = '';

    const perPage = 4;
    const pages = Math.ceil(collageState.symbols.length / perPage);
    const pageSymbols = collageState.symbols.slice(collageState.page * perPage, collageState.page * perPage + perPage);
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
            cell.innerHTML = `<div class="collage-label" id="collageLabel_${i}"></div><div id="collageChart_${i}" style="width:100%;height:100%;"></div>`;
            cell.onclick = ((s) => () => { exitCollage(); openChart(s); })(sym);
            cell.title = 'Открыть в полном графике';
        } else {
            cell.innerHTML = '<div style="position:absolute; inset:0; display:flex; align-items:center; justify-content:center; color:#333333; font-size:11px; text-transform:uppercase; letter-spacing:1px;">—</div>';
        }

        wrap.appendChild(cell);
        if (sym) initCollageChart(i, sym);
    }

    updateCollageControls(pages);
}

// ==========================================
// ИНИЦИАЛИЗАЦИЯ МИНИ-ГРАФИКА
// ==========================================
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
        handleScroll: false,
        handleScale: false,
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

    const entry = { chart, candleSeries, volumeSeries, ws: null, symbol, container };
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
        })
        .catch(() => {});

    const ws = new WebSocket(`wss://fstream.binance.com/ws/${symbol.toLowerCase()}usdt@kline_${currentTF}`);
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