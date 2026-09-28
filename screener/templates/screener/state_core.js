// ==========================================
// state_core.js — ЯДРО СОСТОЯНИЯ
// Глобальные переменные, используемые во всех модулях
// ==========================================

const els = {
    search: document.getElementById('searchInput'),
    vol: document.getElementById('volRange'),
    change: document.getElementById('changeRange'),
    table: document.getElementById('tableBody'),
    chartWrapper: document.getElementById('chart-container'),
    chartHint: document.getElementById('chart-hint'),
    chartTitle: document.getElementById('chart-title'),
    chartWatermark: document.getElementById('chartWatermark'),
    watermarkSymbol: document.getElementById('watermarkSymbol'),
    watermarkTF: document.getElementById('watermarkTF'),
    coinsCount: document.getElementById('coinsCount'),
    rightPanel: document.getElementById('rightPanel'),
    tradesOverlay: document.getElementById('tradesOverlay'),
    tradesOverlayBody: document.getElementById('tradesOverlayBody'),
    tradesThresholdSlider: document.getElementById('tradesThresholdSlider'),
    tradesThresholdValue: document.getElementById('tradesThresholdValue'),
    tradesBtn: document.getElementById('tradesBtn'),
    pencilCanvas: document.getElementById('pencilCanvas'),
    rulerMeasurement: document.getElementById('rulerMeasurement'),
    drawingToolsPanel: document.getElementById('drawingToolsPanel')
};

// График
let chart = null, candleSeries = null, volumeSeries = null;

// Текущая монета/таймфрейм
let currentSymbol = '', currentTF = '1m', currentPrecision = 2, lastCandlePrice = null;

// Данные
let allCoins = [];
let natrData = {};

// WebSocket'ы
let wsTrades = null, wsCandles = null;

// Крупные сделки
let tradeBuffer = [], currentThreshold = 10000;

// Сортировка таблицы
let sortState = { field: null, direction: 'asc' };

// Гистограмма объёма
let volumeHistogramEnabled = true;
if (localStorage.getItem('volumeHistogramEnabled') !== null) {
    volumeHistogramEnabled = localStorage.getItem('volumeHistogramEnabled') === 'true';
}

// Флаги загрузки
let isReconLoading = false;
let isScalpLoading = false;

// Таймер автообновления NATR
let natrAutoUpdateTimer = null;