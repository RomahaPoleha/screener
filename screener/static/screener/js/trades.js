// ==========================================
// trades.js — КРУПНЫЕ СДЕЛКИ
// WebSocket сделок, оверлей, буфер
// ==========================================

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
        if (els.tradesOverlayBody) els.tradesOverlayBody.innerHTML = '<div style="color:#ef4444; text-align:center; padding:20px;">Разрыв связи</div>';
    };
    wsTrades.onclose = () => {
        setTimeout(() => { if (currentSymbol === symbol && els.tradesOverlay.classList.contains('active')) startTradesStream(symbol); }, 3000);
    };
}

function toggleTradesOverlay() {
    els.tradesOverlay.classList.contains('active') ? closeTradesOverlay() : openTradesOverlay();
}

function openTradesOverlay() {
    els.tradesOverlay.classList.add('active');
    els.tradesBtn.classList.add('active');
    els.tradesThresholdSlider.value = currentThreshold;
    els.tradesThresholdValue.textContent = fmtThreshold(currentThreshold);
    if (currentSymbol && !wsTrades) startTradesStream(currentSymbol);
}

function closeTradesOverlay() {
    els.tradesOverlay.classList.remove('active');
    els.tradesBtn.classList.remove('active');
}

function updateTradesOverlay() {
    if (!els.tradesOverlayBody || tradeBuffer.length === 0) return;
    els.tradesOverlayBody.innerHTML = tradeBuffer.map(t =>
        `<div class="trade-item-compact">
            <span class="trade-time">${t.time}</span>
            <span class="trade-value">$${fmt(t.value)}</span>
            <span class="trade-price">${t.price.toFixed(currentPrecision)}</span>
            <span class="trade-qty">${t.qty.toFixed(4)}</span>
            <span class="${t.isBuyerMaker ? 'trade-sell' : 'trade-buy'}">${t.isBuyerMaker ? 'S' : 'B'}</span>
        </div>`
    ).join('');
    els.tradesOverlayBody.scrollTop = els.tradesOverlayBody.scrollHeight;
}