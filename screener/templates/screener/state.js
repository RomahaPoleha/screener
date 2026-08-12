const els = {
    search: document.getElementById('searchInput'),
    vol: document.getElementById('volRange'),
    change: document.getElementById('changeRange'),
    volVal: document.getElementById('volVal'),
    changeVal: document.getElementById('changeVal'),
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

let allCoins = [];
let natrData = {};
let chart = null, candleSeries = null, volumeSeries = null;
let wsTrades = null, wsCandles = null;

let currentPrecision = 2, tradeBuffer = [], currentThreshold = 10000;
let currentTF = '1m', currentSymbol = '', lastCandlePrice = null;

// RECON
let densityLines = [], densityEnabled = false;
let densityMarkets = { future: false, spot: false };
let densityMinVolumeFuture = 50000, densityMinVolumeSpot = 10000;
let densityUpdateTimer = null, previousDensities = { future: [], spot: [] };

// Scalp
let scalpLines = [], scalpEnabled = false;
let scalpMarkets = { future: false, spot: false };
let scalpExchanges = { binance: true, bybit: true };  // ← добавить эту строку
let scalpMinVolumeFuture = 200000, scalpMinVolumeSpot = 100000;
let scalpUpdateTimer = null, isScalpLoading = false;
let previousScalpData = { futures: [], spot: [] };

// Volume
let volumeHistogramEnabled = true;
if (localStorage.getItem('volumeHistogramEnabled') !== null) {
    volumeHistogramEnabled = localStorage.getItem('volumeHistogramEnabled') === 'true';
}
if (localStorage.getItem('densityMinVolumeFuture')) densityMinVolumeFuture = parseInt(localStorage.getItem('densityMinVolumeFuture'));
if (localStorage.getItem('densityMinVolumeSpot')) densityMinVolumeSpot = parseInt(localStorage.getItem('densityMinVolumeSpot'));
if (localStorage.getItem('scalpMinVolumeFuture')) scalpMinVolumeFuture = parseInt(localStorage.getItem('scalpMinVolumeFuture'));
if (localStorage.getItem('scalpMinVolumeSpot')) scalpMinVolumeSpot = parseInt(localStorage.getItem('scalpMinVolumeSpot'));
if (localStorage.getItem('scalpExchanges')) {
    try {
        scalpExchanges = JSON.parse(localStorage.getItem('scalpExchanges'));
    } catch(e) {
        console.warn('⚠️ scalpExchanges в localStorage повреждён, используем значения по умолчанию');
    }
}

// Drawings
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
// Горизонтальная линия
let isHorizontalLineEnabled = false, activeHorizontalLines = [];

let pencilStrokes = [];
let currentStroke = null;

if (localStorage.getItem('magnetEnabled') !== null) isMagnetEnabled = localStorage.getItem('magnetEnabled') === 'true';
if (localStorage.getItem('showDrawingTools') !== null) showDrawingTools = localStorage.getItem('showDrawingTools') === 'true';

let soundEnabled = localStorage.getItem('soundEnabled') !== 'false';
let lastNotifiedMinute = -1, russianVoice = null, audioCtx = null;
let sortState = { field: null, direction: 'asc' };
let natrAutoUpdateTimer = null;