// ==========================================
// chart.js — ГРАФИК
// Открытие/закрытие, загрузка данных, кэш, статистика
// ==========================================

// --- Кэш свечей ---
const candlesCache = new Map();
const CACHE_TTL = 60000;

// ==========================================
// ВОДЯНОЙ ЗНАК
// ==========================================
function updateWatermark() {
    if (currentSymbol && els.chartWatermark) {
        els.watermarkSymbol.textContent = currentSymbol;
        els.watermarkTF.textContent = currentTF;
        els.chartWatermark.style.display = 'block';
    }
}

// ==========================================
// КОПИРОВАНИЕ СИМВОЛА
// ==========================================
function copySymbolToClipboard() {
    if (!currentSymbol) return;
    navigator.clipboard.writeText(`${currentSymbol}USDT`).then(() => {
        els.chartTitle.classList.add('copied');
        setTimeout(() => { els.chartTitle.classList.remove('copied'); }, 600);
    }).catch(err => console.error('Ошибка копирования:', err));
}

// ==========================================
// СТАТИСТИКА НАД ГРАФИКОМ (объём + NATR)
// ==========================================
function updateChartStats() {
    const el = document.getElementById('chartStats');
    if (!el || !currentSymbol) return;
    const coin = allCoins.find(c => c.symbol === currentSymbol);
    const natr = natrData[currentSymbol] || {};
    const vol = coin ? `$${fmt(coin.volume)}` : '—';
    const n1 = (natr.natr_1m30 !== undefined && natr.natr_1m30 !== null) ? natr.natr_1m30 : null;
    const n5 = (natr.natr_5m14 !== undefined && natr.natr_5m14 !== null) ? natr.natr_5m14 : null;
    const natrColor = (v) => v > 1.0 ? '#ef4444' : v > 0.3 ? '#f59e0b' : '#22c55e';
    const n1Html = n1 !== null ? `<span style="color:${natrColor(n1)}; font-weight:600;">${n1.toFixed(1)}</span>` : '<span style="color:#6b7280;">-</span>';
    const n5Html = n5 !== null ? `<span style="color:${natrColor(n5)}; font-weight:600;">${n5.toFixed(1)}</span>` : '<span style="color:#6b7280;">-</span>';
    const change24h = coin && coin.change !== undefined && coin.change !== null ? coin.change : null;
    const changeColor = change24h !== null ? (change24h >= 0 ? '#22c55e' : '#ef4444') : '#6b7280';
    const changePrefix = change24h !== null ? (change24h >= 0 ? '+' : '') : '';
    const changeHtml = change24h !== null
        ? `<span style="color:${changeColor}; font-weight:700;">${changePrefix}${change24h.toFixed(2)}%</span>`
        : '<span style="color:#6b7280;">—</span>';
    el.innerHTML = `24ч: ${changeHtml}` +
                   `&nbsp;&nbsp;|&nbsp;&nbsp;` +
                   `Vol: <span style="color:#e5e5e5; font-weight:600;">${vol}</span>` +
                   `&nbsp;&nbsp;|&nbsp;&nbsp;NATR 1m: ${n1Html}` +
                   `&nbsp;&nbsp;|&nbsp;&nbsp;NATR 5m: ${n5Html}`;
}

// ==========================================
// ЗАКРЫТИЕ ГРАФИКА
// ==========================================
function closeChart() {
    clearSpecificDrawings('trendlines');
    clearSpecificDrawings('horizontalLines');
    clearSpecificDrawings('pencil');
    clearSpecificDrawings('ruler');
    for (const id of Object.keys(chartAlertLines)) AlertManager.removeLine(id);
    clearDensityLines();
    if (densityUpdateTimer) { clearInterval(densityUpdateTimer); densityUpdateTimer = null; }
    previousDensities = { future: [], spot: [] };
    clearScalpLines();
    previousScalpData = {};
    if (scalpUpdateTimer) { clearInterval(scalpUpdateTimer); scalpUpdateTimer = null; }
    stopReconUpdates();
    if (wsCandles) { wsCandles.onclose = null; wsCandles.close(); wsCandles = null; }
    if (wsTrades) { wsTrades.onclose = null; wsTrades.onmessage = null; wsTrades.onerror = null; wsTrades.close(); wsTrades = null; }
    if (chart) { chart.remove(); chart = null; candleSeries = null; volumeSeries = null; }
    chartAlertLines = {};
    tradeBuffer = []; lastCandlePrice = null;
    els.chartTitle.textContent = '';
    const statsEl = document.getElementById('chartStats');
    if (statsEl) statsEl.textContent = '';
    els.chartWrapper.classList.remove('active');
    els.chartHint.style.display = 'block'; els.chartWatermark.style.display = 'none';
    closeTradesOverlay(); currentSymbol = '';
    const tooltip = document.getElementById('volumeTooltip');
    if (tooltip) tooltip.classList.remove('visible');
}

// ==========================================
// ОТКРЫТИЕ ГРАФИКА
// ==========================================
async function openChart(symbol) {
    if (collageState) exitCollage();
    if (wsCandles) { wsCandles.onclose = null; wsCandles.close(); wsCandles = null; }
    if (wsTrades) { wsTrades.onclose = null; wsTrades.onmessage = null; wsTrades.onerror = null; wsTrades.close(); wsTrades = null; }
    clearDensityLines(); if (densityUpdateTimer) { clearInterval(densityUpdateTimer); densityUpdateTimer = null; }
    clearScalpLines(); previousScalpData = {}; if (scalpUpdateTimer) { clearInterval(scalpUpdateTimer); scalpUpdateTimer = null; }
    stopReconUpdates();
    currentSymbol = symbol;
    updateActiveCoinHighlight();
    els.chartHint.style.display = 'none'; els.chartWrapper.classList.add('active');
    tradeBuffer = []; lastCandlePrice = null;
    // Сбрасываем текст кнопки истории при открытии новой монеты
    const historyBtn = document.getElementById('loadHistoryBtn');
    if (historyBtn) {
        historyBtn.textContent = 'Загрузить историю';
        historyBtn.title = 'Загрузить глубокую историю свечей';
    }
    if (chart) { chart.remove(); chart = null; candleSeries = null; volumeSeries = null; }
    try {
        chart = LightweightCharts.createChart(els.chartWrapper, {
            width: els.chartWrapper.clientWidth,
            height: els.chartWrapper.clientHeight,
            layout: { background: { color: '#0f0f0f' }, textColor: '#999999' },
            grid: { vertLines: { color: '#1f1f1f' }, horzLines: { color: '#1f1f1f' } },
            timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#333333', rightOffset: 50, barSpacing: 10 },
            rightPriceScale: { borderColor: '#333333', scaleMargins: { top: 0.1, bottom: 0.25 }, autoScale: true },
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
                const logicalIndex = getLogicalIndexByX(param.point.x);
                if (price && time) {
                    trendLinePreview.time2 = time;
                    trendLinePreview.price2 = price;
                    trendLinePreview.logicalIndex2 = logicalIndex;
                    redrawAllPersistentDrawings();
                }
            }
            // ТУЛТИП ОБЪЁМА
            if (param.time && volumeSeries && param.point) {
                const chartHeight = els.chartWrapper.clientHeight;
                const volumeAreaTop = chartHeight * 0.85;
                if (param.point.y >= volumeAreaTop) {
                    const volumeData = param.seriesData.get(volumeSeries);
                    const candleData = param.seriesData.get(candleSeries);
                    if (volumeData && candleData) {
                        const tooltip = document.getElementById('volumeTooltip');
                        const isUp = candleData.close >= candleData.open;
                        const colorClass = isUp ? 'vol-up' : 'vol-down';
                        tooltip.innerHTML = `
                            <div class="vol-label">Объём</div>
                            <div class="vol-value ${colorClass}">${fmt(volumeData.value)}</div>
                            <div style="font-size:10px; color:#666666; margin-top:2px;">
                                ${candleData.open.toFixed(currentPrecision)} → ${candleData.close.toFixed(currentPrecision)}
                            </div>
                        `;
                        tooltip.classList.add('visible');
                        const rect = els.chartWrapper.getBoundingClientRect();
                        const x = param.point.x + rect.left + 15;
                        const y = param.point.y + rect.top - 40;
                        tooltip.style.left = x + 'px';
                        tooltip.style.top = y + 'px';
                    } else {
                        const tooltip = document.getElementById('volumeTooltip');
                        if (tooltip) tooltip.classList.remove('visible');
                    }
                } else {
                    const tooltip = document.getElementById('volumeTooltip');
                    if (tooltip) tooltip.classList.remove('visible');
                }
            } else {
                const tooltip = document.getElementById('volumeTooltip');
                if (tooltip) tooltip.classList.remove('visible');
            }
        });

        chart.subscribeClick(handleChartClick);
        chart.timeScale().subscribeVisibleTimeRangeChange(redrawAllPersistentDrawings);
        chart.timeScale().subscribeVisibleLogicalRangeChange(redrawAllPersistentDrawings);
        chart.timeScale().subscribeSizeChange(() => { setTimeout(() => { initPencilCanvas(); }, 150); });

        let isRedrawScheduled = false;
        els.chartWrapper.addEventListener('mousemove', () => {
            if (activeTrendlines.length > 0 || pencilStrokes.length > 0 || rulerFixedMeasurement) {
                if (!isRedrawScheduled) {
                    isRedrawScheduled = true;
                    requestAnimationFrame(() => { redrawAllPersistentDrawings(); isRedrawScheduled = false; });
                }
            }
        }, { passive: true });

        els.chartWrapper.addEventListener('mousedown', (e) => {
            if (isRulerEnabled && e.button === 0) {
                e.preventDefault(); e.stopPropagation();
                rulerFixedMeasurement = null;
                els.rulerMeasurement.style.display = 'none';
                const rect = els.chartWrapper.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;
                const price = candleSeries.coordinateToPrice(y);
                const time = chart.timeScale().coordinateToTime(x) || getTimeByX(x);
                const logicalIndex = getLogicalIndexByX(x);
                if (price && (time || logicalIndex !== null)) {
                    rulerStartPoint = { time: time || 0, price, x, y, logicalIndex };
                    rulerCurrentPoint = { time: time || 0, price, x, y, logicalIndex };
                    isRulerDragging = true;
                    isRulerMiddleClickDrag = false;
                    initPencilCanvas();
                    redrawAllPersistentDrawings();
                }
            }
            else if (e.button === 1) {
                e.preventDefault(); e.stopPropagation();
                rulerFixedMeasurement = null;
                els.rulerMeasurement.style.display = 'none';
                const rect = els.chartWrapper.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;
                const price = candleSeries.coordinateToPrice(y);
                const time = chart.timeScale().coordinateToTime(x) || getTimeByX(x);
                const logicalIndex = getLogicalIndexByX(x);
                if (price && (time || logicalIndex !== null)) {
                    rulerStartPoint = { time: time || 0, price, x, y, logicalIndex };
                    rulerCurrentPoint = { time: time || 0, price, x, y, logicalIndex };
                    isRulerDragging = true;
                    isRulerMiddleClickDrag = true;
                    initPencilCanvas();
                    redrawAllPersistentDrawings();
                }
            }
            else if (isPencilEnabled && e.button === 0) {
                isDrawing = true;
                initPencilCanvas();
            }
        });

        els.chartWrapper.addEventListener('auxclick', (e) => {
            if (e.button === 1) { e.preventDefault(); e.stopPropagation(); }
        });

        els.chartWrapper.addEventListener('mousemove', (e) => {
            if (isRulerDragging && rulerStartPoint) {
                const rect = els.chartWrapper.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;
                const price = candleSeries.coordinateToPrice(y);
                const time = chart.timeScale().coordinateToTime(x) || getTimeByX(x);
                const logicalIndex = getLogicalIndexByX(x);
                if (price && (time || logicalIndex !== null)) {
                    rulerCurrentPoint = {
                        time: time || 0, price, x, y,
                        logicalIndex: logicalIndex !== null ? logicalIndex : rulerCurrentPoint.logicalIndex
                    };
                    redrawAllPersistentDrawings();
                    showRulerMeasurement(rulerStartPoint, rulerCurrentPoint);
                }
            }
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
            if (isRulerEnabled && e.button === 0 && isRulerDragging) {
                isRulerDragging = false;
                rulerStartPoint = null;
                rulerCurrentPoint = null;
                els.rulerMeasurement.style.display = 'none';
                redrawAllPersistentDrawings();
            }
            if (!isRulerEnabled && e.button === 1 && isRulerDragging) {
                e.preventDefault(); e.stopPropagation();
                isRulerDragging = false;
                rulerStartPoint = null;
                rulerCurrentPoint = null;
                els.rulerMeasurement.style.display = 'none';
                redrawAllPersistentDrawings();
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
                rulerStartPoint = null;
                rulerCurrentPoint = null;
                els.rulerMeasurement.style.display = 'none';
                redrawAllPersistentDrawings();
                isRulerMiddleClickDrag = false;
            }
        });
    } catch (e) { console.error('Chart init error:', e); return; }

    await loadChartData(symbol, currentTF);
    AlertManager.restoreLines(symbol);
    startCandleWebSocket(symbol, currentTF);
    updateWatermark();
    updateChartStats();
    if (els.tradesOverlay.classList.contains('active')) startTradesStream(symbol);
    if (densityEnabled) startDensityUpdates(symbol);
    if (scalpEnabled) startScalpUpdates(symbol);
    if (reconEnabled) startReconUpdates(symbol);
}

// ==========================================
// ЗАГРУЗКА ДАННЫХ ГРАФИКА
// ==========================================
async function loadChartData(symbol, tf) {
    if (!chart || !candleSeries) return;
    els.chartTitle.textContent = `${symbol}/USDT`;
    const cacheKey = `${symbol}_${tf}`;
    const cached = candlesCache.get(cacheKey);
    if (cached && (Date.now() - cached.timestamp < CACHE_TTL)) {
        applyCandlesToChart(cached.data);
        return;
    }
    try {
        const res = await fetch(`/api/candles/${symbol}/?tf=${tf}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const history = await res.json();
        if (!history || history.length === 0) throw new Error('Пустая история');
        const limitedHistory = history.slice(-500);
        candlesCache.set(cacheKey, {
            data: limitedHistory,
            timestamp: Date.now()
        });
        applyCandlesToChart(limitedHistory);
    } catch (err) {
        els.chartTitle.textContent = `Ошибка: ${err.message}`;
        console.error('loadChartData error:', err);
    }
}

// ==========================================
// ПРИМЕНЕНИЕ СВЕЧЕЙ К ГРАФИКУ
// ==========================================
function applyCandlesToChart(history) {
    const firstPrice = history[0].close;
    currentPrecision = firstPrice < 1 ? (firstPrice < 0.01 ? 8 : 5) : 2;
    const minMove = firstPrice < 1 ? (firstPrice < 0.01 ? 0.00000001 : 0.00001) : 0.01;
    candleSeries.applyOptions({
        priceFormat: { type: 'price', precision: currentPrecision, minMove: minMove }
    });
    candleSeries.setData(history.map(c => ({ ...c, time: safeTime(c.time) })));
    window.candleData = history.map(c => ({ ...c, time: safeTime(c.time) }));
    if (history[0].volume !== undefined) {
        volumeSeries.setData(history.map(c => ({
            time: safeTime(c.time),
            value: c.volume,
            color: c.close >= c.open ? 'rgba(200, 200, 200, 0.6)' : 'rgba(80, 80, 80, 0.7)'
        })));
    }
    chart.timeScale().fitContent();
    chart.timeScale().scrollToPosition(12, false);
}
// ==========================================
// ЗАГРУЗКА ГЛУБОКОЙ ИСТОРИИ
// ==========================================

async function loadChartHistory() {
    if (!currentSymbol || !currentTF) {
        console.warn('Нет активного символа или таймфрейма для загрузки истории');
        return;
    }
    
    const btn = document.getElementById('loadHistoryBtn');
    if (btn) {
        btn.disabled = true;
        btn.classList.add('loading');
        btn.textContent = 'Загрузка...';
        btn.title = 'Загрузка истории...';
    }
    
    try {
        const url = `/api/candles-history/${currentSymbol}/?tf=${currentTF}`;
        const response = await fetch(url);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const history = await response.json();
        
        if (Array.isArray(history) && history.length > 0) {
            // Объединяем текущие свечи с историей (удаляем дубликаты)
            const currentData = candleSeries.data() || [];
            const historyMap = new Map();
            
            // Сначала добавляем всю историю
            history.forEach(candle => {
                const time = safeTime(candle.time);
                historyMap.set(time, candle);
            });
            
            // Затем добавляем текущие свечи (более новые)
            currentData.forEach(candle => {
                historyMap.set(candle.time, candle);
            });
            
            // Сортируем по времени
            const mergedData = Array.from(historyMap.values())
                .sort((a, b) => a.time - b.time);
            
            // Применяем к графику
            applyCandlesToChart(mergedData);
            
            // Обновляем кэш
            const cacheKey = `${currentSymbol}_${currentTF}`;
            candlesCache.set(cacheKey, {
                data: mergedData,
                timestamp: Date.now()
            });
            
            // Уведомление
            const oldCount = currentData.length;
            const newCount = mergedData.length;
            console.log(`✓ История загружена: ${oldCount} → ${newCount} свечей (+${history.length} исторических)`);
            
            if (btn) {
                // Обновляем текст кнопки на "История за [таймфрейм]"
                updateHistoryButtonTextAfterLoad();
                // Кратковременная подсветка успеха
                btn.style.color = '#3b82f6';
                btn.style.borderColor = '#3b82f6';
                setTimeout(() => {
                    if (btn) {
                        btn.style.color = '';
                        btn.style.borderColor = '';
                    }
                }, 2000);
            }
        } else {
            console.warn('История пуста или неверный формат данных');
            if (btn) {
                btn.title = 'Нет исторических данных';
                // Если данных нет, возвращаем исходный текст
                btn.textContent = 'Загрузить историю';
                btn.title = 'Загрузить глубокую историю свечей';
            }
        }
    } catch (error) {
        console.error('Ошибка загрузки истории:', error);
        // Не блокируем alert, пока оставим console.error
        if (btn) {
            btn.title = `Ошибка: ${error.message.substring(0, 50)}...`;
            // При ошибке возвращаем исходный текст
            btn.textContent = 'Загрузить историю';
            btn.title = 'Загрузить глубокую историю свечей';
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.classList.remove('loading');
            // Текст остается "История за [таймфрейм]" если загрузка успешна, 
            // или восстанавливается к "Загрузить историю" если ошибка
            // (это решает updateHistoryButtonTextAfterLoad())
        }
    }
}

// ==========================================
// ФУНКЦИЯ ДЛЯ ОБНОВЛЕНИЯ ТЕКСТА КНОПКИ ИСТОРИИ ПОСЛЕ ЗАГРУЗКИ
// ==========================================

function updateHistoryButtonTextAfterLoad() {
    const btn = document.getElementById('loadHistoryBtn');
    if (!btn || !currentTF) return;
    
    const tfMap = {
        '1m': '3 дня',
        '5m': 'неделю',
        '15m': 'месяц',
        '30m': 'месяц',
        '1h': '3 месяца',
        '4h': '6 месяцев',
        '1d': 'год',
        '1w': '5 лет'
    };
    
    const period = tfMap[currentTF] || 'историю';
    btn.textContent = `История за ${period}`;
    btn.title = `Загружена история за ${period}. Нажмите для обновления`;
}

// ==========================================
// ИНИЦИАЛИЗАЦИЯ КНОПКИ ЗАГРУЗКИ ИСТОРИИ
// ==========================================

// Кнопка уже есть в HTML шаблоне, просто добавляем слушатели если нужно
document.addEventListener('DOMContentLoaded', () => {
    // Убедимся, что кнопка существует
    const historyBtn = document.getElementById('loadHistoryBtn');
    if (historyBtn) {
        // Добавляем класс для визуального обозначения
        historyBtn.classList.add('chart-history-btn');
        
        // Устанавливаем изначальный текст
        historyBtn.textContent = 'Загрузить историю';
        historyBtn.title = 'Загрузить глубокую историю свечей';
    }
});