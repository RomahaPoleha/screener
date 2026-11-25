// ==========================================
// tiger_chart_link.js — ЛИНКОВКА В TIGER ИЗ ГРАФИКА
// ==========================================

let tigerLinkPopup = null;

function openTigerLinkPopup() {
    if (!currentSymbol) return;
    const symbol = currentSymbol;
    if (typeof tigerSendFromMenu === 'function') {
        tigerSendFromMenu(symbol, 'binance', 'futures');
    }
}

function closeTigerLinkPopup() {
    // No-op: popup removed, direct link only
}

// Скрыть popup при закрытии графика
const _origCloseChart = (typeof closeChart === 'function') ? closeChart : null;
if (_origCloseChart) {
    const _wrappedCloseChart = closeChart;
    // Don't override, just ensure popup is closed
}

// Hook into closeChart to close popup
document.addEventListener('DOMContentLoaded', () => {
    const origCloseChart = window.closeChart;
    if (typeof origCloseChart === 'function') {
        window.closeChart = function() {
            closeTigerLinkPopup();
            origCloseChart();
        };
    }
});
