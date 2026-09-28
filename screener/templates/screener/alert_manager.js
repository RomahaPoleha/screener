// ==========================================
// alert_manager.js — МЕНЕДЖЕР АЛЕРТОВ
// Ценовые алерты, импульсные алерты по объёму, история
// ==========================================

// --- Переменные состояния ---
let volumeAlertEnabled = localStorage.getItem('volumeAlertEnabled') === 'true';
let volumeAlertCooldown = {};  // symbol -> timestamp последнего алерта
let volumeAlertHistory = [];
try {
    volumeAlertHistory = JSON.parse(localStorage.getItem('volumeAlertHistory') || '[]');
} catch(e) { volumeAlertHistory = []; }
let unreadAlerts = 0;
let rvolAlertSoundEnabled = localStorage.getItem('rvolAlertSoundEnabled') !== 'false';

// Параметры импульса цены
let priceImpulseThreshold = parseFloat(localStorage.getItem('priceImpulseThreshold') || '1');
let priceImpulseWindow = parseInt(localStorage.getItem('priceImpulseWindow') || '60');

// Постоянные ценовые алерты (symbol -> массив алертов)
let savedAlerts = {};
try {
    savedAlerts = JSON.parse(localStorage.getItem('savedAlerts') || '{}');
} catch(e) { savedAlerts = {}; }

// Линии алертов на текущем графике (id алерта -> линия)
let chartAlertLines = {};

// ==========================================
// ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
// ==========================================
function getActiveAlertsFor(symbol) {
    return (savedAlerts[symbol] || []).filter(a => a.active);
}

function showTriggeredToast(symbol, price, direction) {
    const toast = document.createElement('div');
    toast.className = 'hour-toast show';
    toast.innerHTML = `<div class="toast-icon" style="color:#f59e0b;">&#9679;</div><div class="toast-content"><div class="toast-title">Алерт сработал</div><div style="font-size:12px; margin-top:4px;">${symbol}: цена пересекла ${price}<br>Направление: ${direction}</div></div>`;
    document.body.appendChild(toast);
    setTimeout(() => { toast.classList.remove('show'); setTimeout(() => toast.remove(), 500); }, 5000);
}

// ==========================================
// МЕНЕДЖЕР ЦЕНОВЫХ АЛЕРТОВ (фоновый мониторинг)
// ==========================================
const AlertManager = {
    streams: {},     // symbol -> WebSocket
    lastPrice: {},   // symbol -> последняя цена

    save() {
        localStorage.setItem('savedAlerts', JSON.stringify(savedAlerts));
    },

    add(symbol, price) {
        if (!savedAlerts[symbol]) savedAlerts[symbol] = [];
        const alert = {
            id: 'al_' + Date.now() + '_' + Math.random().toString(36).slice(2, 7),
            price: price,
            active: true,
            createdAt: Date.now()
        };
        savedAlerts[symbol].push(alert);
        this.save();
        this.ensureStream(symbol);
        if (currentSymbol === symbol) this.drawLine(alert);
        return alert;
    },

    remove(symbol, id) {
        if (!savedAlerts[symbol]) return;
        savedAlerts[symbol] = savedAlerts[symbol].filter(a => a.id !== id);
        if (savedAlerts[symbol].length === 0) delete savedAlerts[symbol];
        this.save();
        this.removeLine(id);
        this.refreshStream(symbol);
    },

    clearSymbol(symbol) {
        delete savedAlerts[symbol];
        this.save();
        for (const id of Object.keys(chartAlertLines)) this.removeLine(id);
        this.stopStream(symbol);
    },

    ensureStream(symbol) {
        if (this.streams[symbol]) return;
        if (!getActiveAlertsFor(symbol).length) return;
        const url = `wss://fstream.binance.com/market/ws/${symbol.toLowerCase()}usdt@kline_1m`;
        const ws = new WebSocket(url);
        this.streams[symbol] = ws;
        ws.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                if (!data.k) return;
                const price = parseFloat(data.k.c);
                const prev = this.lastPrice[symbol];
                this.lastPrice[symbol] = price;
                if (prev === undefined) return;
                this.checkCross(symbol, price, prev);
            } catch (err) {}
        };
        ws.onclose = () => {
            if (this.streams[symbol] === ws) {
                delete this.streams[symbol];
                if (getActiveAlertsFor(symbol).length) {
                    setTimeout(() => this.ensureStream(symbol), 3000);
                }
            }
        };
        ws.onerror = () => { try { ws.close(); } catch(e) {} };
    },

    stopStream(symbol) {
        const ws = this.streams[symbol];
        if (ws) {
            this.streams[symbol] = null;
            ws.onclose = null;
            ws.close();
            delete this.streams[symbol];
        }
    },

    refreshStream(symbol) {
        if (getActiveAlertsFor(symbol).length) this.ensureStream(symbol);
        else this.stopStream(symbol);
    },

    checkCross(symbol, price, prev) {
        for (const alert of (savedAlerts[symbol] || [])) {
            if (!alert.active) continue;
            const up = prev < alert.price && price >= alert.price;
            const down = prev > alert.price && price <= alert.price;
            if (up || down) this.trigger(symbol, alert, up ? 'вверх ↑' : 'вниз ↓');
        }
    },

    trigger(symbol, alert, direction) {
        alert.active = false;
        this.save();
        playAlertSound();
        showTriggeredToast(symbol, alert.price, direction);
        if (currentSymbol === symbol && candleSeries) {
            this.removeLine(alert.id);
            const faded = candleSeries.createPriceLine({
                price: alert.price, color: 'rgba(245, 158, 11, 0.3)', lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true,
                title: ` ${alert.price.toFixed(currentPrecision)}`
            });
            chartAlertLines[alert.id] = faded;
        }
        this.refreshStream(symbol);
    },

    drawLine(alert) {
        if (!candleSeries) return;
        const line = candleSeries.createPriceLine({
            price: alert.price,
            color: alert.active ? '#3b82f6' : 'rgba(245, 158, 11, 0.3)',
            lineWidth: 2,
            lineStyle: alert.active ? LightweightCharts.LineStyle.Dashed : LightweightCharts.LineStyle.Dotted,
            axisLabelVisible: true,
            title: ` ${alert.price.toFixed(currentPrecision)}`
        });
        chartAlertLines[alert.id] = line;
    },

    removeLine(id) {
        const line = chartAlertLines[id];
        if (line && candleSeries) { try { candleSeries.removePriceLine(line); } catch(e) {} }
        delete chartAlertLines[id];
    },

    restoreLines(symbol) {
        for (const id of Object.keys(chartAlertLines)) this.removeLine(id);
        for (const alert of (savedAlerts[symbol] || [])) this.drawLine(alert);
    },

    startAll() {
        for (const symbol of Object.keys(savedAlerts)) this.ensureStream(symbol);
    }
};

// ==========================================
// АЛЕРТЫ ПО ОБЪЁМУ (RVOL / ИМПУЛЬС)
// ==========================================
function showVolumeAlertToast(symbol, volume, direction, priceChange) {
    const now = new Date();
    const timeStr = now.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    volumeAlertHistory.unshift({ symbol, volume, time: timeStr, direction, priceChange });
    if (volumeAlertHistory.length > 20) volumeAlertHistory.pop();
    localStorage.setItem('volumeAlertHistory', JSON.stringify(volumeAlertHistory));

    unreadAlerts++;
    updateAlertBadge();

    const existing = document.querySelectorAll('.volume-alert-toast');
    if (existing.length >= 3) existing[0].remove();

    const offset = document.querySelectorAll('.volume-alert-toast').length * 90;
    const color = direction === '↑' ? '#22c55e' : '#ef4444';

    const toast = document.createElement('div');
    toast.className = 'volume-alert-toast';
    toast.style.cssText = `
        position:fixed; right:20px; bottom:${20 + offset}px;
        background:#1a1a1a; border:2px solid ${color};
        color:#ffffff; padding:14px 18px; border-radius:0;
        box-shadow:0 4px 16px rgba(0,0,0,0.5); z-index:10000;
        cursor:pointer; transition:all 0.3s ease;
        opacity:0; transform:translateX(400px); display:flex; align-items:center; gap:12px;
    `;
    toast.innerHTML = `
        <div style="font-size:22px; color:${color};">${direction === '↑' ? '▲' : '▼'}</div>
        <div style="display:flex; flex-direction:column; gap:3px;">
            <div style="font-size:13px; font-weight:700; text-transform:uppercase;">
                ${symbol} — импульс
            </div>
            <div style="font-size:12px;">
                Цена: ${direction} ${priceChange.toFixed(2)}% за ${priceImpulseWindow}с
            </div>
            <div style="font-size:10px; color:#999999;">Клик — открыть график</div>
        </div>
    `;
    toast.onclick = () => {
        openChart(symbol);
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(400px)';
        setTimeout(() => toast.remove(), 500);
    };
    document.body.appendChild(toast);
    playAlertSound();
    setTimeout(() => { toast.style.opacity = '1'; toast.style.transform = 'translateX(0)'; }, 50);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(400px)';
        setTimeout(() => toast.remove(), 500);
    }, 8000);
}

function updateAlertBadge() {
    const badge = document.getElementById('alertBadge');
    if (badge) {
        badge.textContent = unreadAlerts;
        badge.style.display = unreadAlerts > 0 ? 'inline-block' : 'none';
    }
}

function updateAlertHistoryVisibility() {
    const wrap = document.getElementById('alertHistoryWrap');
    if (wrap) {
        wrap.style.display = volumeAlertEnabled ? 'inline-block' : 'none';
    }
}

function toggleAlertHistory() {
    const panel = document.getElementById('alertHistoryPanel');
    if (!panel) return;
    const isOpen = panel.classList.toggle('active');
    if (isOpen) {
        unreadAlerts = 0;
        updateAlertBadge();
        renderAlertHistory();
    }
}

function renderAlertHistory() {
    const body = document.getElementById('alertHistoryBody');
    if (!body) return;
    if (volumeAlertHistory.length === 0) {
        body.innerHTML = '<div style="color:#6b7280; text-align:center; padding:20px;">Нет алертов</div>';
        return;
    }
    body.innerHTML = volumeAlertHistory.map(a => {
        const dir = a.direction || '';
        const changeTxt = a.priceChange ? `${dir}${a.priceChange.toFixed(1)}%` : '';
        return `<div class="alert-history-item" onclick="openChartFromHistory('${a.symbol}')">
            <span class="alert-time">${a.time}</span>
            <span class="alert-symbol">${a.symbol}</span>
            <span class="alert-rvol">${changeTxt}</span>
            <span class="alert-vol">$${fmt(a.volume)}</span>
        </div>`;
    }).join('');
}

function openChartFromHistory(symbol) {
    const panel = document.getElementById('alertHistoryPanel');
    if (panel) panel.classList.remove('active');
    openChart(symbol);
}

function checkVolumeAlerts() {
    if (!volumeAlertEnabled) return;
    const now = Date.now();
    const nowSec = now / 1000;
    const COOLDOWN = Math.max(30000, Math.min(300000, priceImpulseWindow * 2000));

    for (const coin of allCoins) {
        const history = priceHistory[coin.symbol] || [];
        if (history.length === 0) continue;

        const targetTime = nowSec - priceImpulseWindow;
        // Защита: если данных ещё нет за нужное окно
        if (history[0].time > targetTime) continue;

        let referencePrice = null;
        for (let i = history.length - 1; i >= 0; i--) {
            if (history[i].time <= targetTime) {
                referencePrice = history[i].price;
                break;
            }
        }
        if (!referencePrice || referencePrice === 0) continue;

        // Берём текущую цену из истории (real-time из WS), а не из coin.price
        const currentPrice = history[history.length - 1].price;
        const priceChange = ((currentPrice - referencePrice) / referencePrice) * 100;
        const absPriceChange = Math.abs(priceChange);

        if (absPriceChange < priceImpulseThreshold) continue;

        // Кулдаун
        const last = volumeAlertCooldown[coin.symbol] || 0;
        if (now - last < COOLDOWN) continue;

        volumeAlertCooldown[coin.symbol] = now;
        const direction = priceChange > 0 ? '↑' : '↓';
        showVolumeAlertToast(coin.symbol, coin.volume, direction, absPriceChange);
    }
}