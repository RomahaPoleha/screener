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

// ==========================================
// SCALP — настройки по биржам
// ==========================================
let scalpLines = [], scalpEnabled = false;
let scalpUpdateTimer = null, isScalpLoading = false;
let previousScalpData = {};

// Конфигурация бирж (легко расширяется — добавь строку)
const EXCHANGES_CONFIG = [
    { id: 'binance', name: 'Binance', domain: 'binance.com', color: '#f0b90b' },
    { id: 'bybit',   name: 'Bybit',   domain: 'bybit.com',   color: '#f7931a' },
    { id: 'okx',     name: 'OKX',     domain: 'okx.com',     color: '#ffffff' },
    { id: 'gate',    name: 'Gate.io',  domain: 'gate.io',     color: '#2354e6' },
    { id: 'mexc', name: 'MEXC', domain: 'mexc.com', color: '#1972ff' },
];

// Текущие настройки каждой биржи
let scalpExchanges = {
    binance: { enabled: true, markets: { futures: true, spot: false }, minVolumeFutures: 300000, minVolumeSpot: 200000 },
    bybit:   { enabled: true, markets: { futures: true, spot: false }, minVolumeFutures: 300000, minVolumeSpot: 200000 },
    okx:     { enabled: true, markets: { futures: true, spot: false }, minVolumeFutures: 300000, minVolumeSpot: 200000 },
    gate:    { enabled: true, markets: { futures: true, spot: true }, minVolumeFutures: 300000, minVolumeSpot: 200000 },
    mexc: { enabled: true, markets: { futures: true, spot: true }, minVolumeFutures: 300000, minVolumeSpot: 200000 },
};


// Миграция любого старого формата + подхват сохранённых значений
try {
    const saved = JSON.parse(localStorage.getItem('scalpExchanges') || 'null');
    if (saved && typeof saved === 'object') {
        for (const id of Object.keys(scalpExchanges)) {
            const s = saved[id];
            if (!s) continue;
            if (typeof s === 'boolean') {
                scalpExchanges[id].enabled = s;
            } else if (typeof s === 'object') {
                scalpExchanges[id].enabled = s.enabled !== false;
                if (s.markets) {
                    scalpExchanges[id].markets.futures = !!s.markets.futures;
                    scalpExchanges[id].markets.spot    = !!s.markets.spot;
                }
                if (Number(s.minVolumeFutures) > 0) scalpExchanges[id].minVolumeFutures = Number(s.minVolumeFutures);
                if (Number(s.minVolumeSpot)    > 0) scalpExchanges[id].minVolumeSpot    = Number(s.minVolumeSpot);
            }
        }
    }
} catch(e) { console.warn('⚠️ scalpExchanges повреждён'); }

// Вычисляем scalpEnabled при загрузке
scalpEnabled = Object.values(scalpExchanges).some(cfg => cfg.enabled && (cfg.markets.futures || cfg.markets.spot));

// Volume
let volumeHistogramEnabled = true;
if (localStorage.getItem('volumeHistogramEnabled') !== null) {
    volumeHistogramEnabled = localStorage.getItem('volumeHistogramEnabled') === 'true';
}
if (localStorage.getItem('densityMinVolumeFuture')) densityMinVolumeFuture = parseInt(localStorage.getItem('densityMinVolumeFuture'));
if (localStorage.getItem('densityMinVolumeSpot')) densityMinVolumeSpot = parseInt(localStorage.getItem('densityMinVolumeSpot'));

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
let isHorizontalLineEnabled = false, activeHorizontalLines = [];

let pencilStrokes = [];
let currentStroke = null;

if (localStorage.getItem('magnetEnabled') !== null) isMagnetEnabled = localStorage.getItem('magnetEnabled') === 'true';
if (localStorage.getItem('showDrawingTools') !== null) showDrawingTools = localStorage.getItem('showDrawingTools') === 'true';

let soundEnabled = localStorage.getItem('soundEnabled') !== 'false';
let lastNotifiedMinute = -1, russianVoice = null, audioCtx = null;
let sortState = { field: null, direction: 'asc' };
let natrAutoUpdateTimer = null;