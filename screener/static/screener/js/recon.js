// ==========================================
// RECON — МУЛЬТИ-БИРЖЕВАЯ ЛОГИКА + GATE.IO WEBSOCKET
// 🔥 Gate.io переведен на WebSocket (100ms). Остальные биржи на REST (без изменений).
// ==========================================

// --- Глобальные переменные для Gate Recon WS ---
let wsGateRecon = null;
let gateReconBook = { bids: {}, asks: {} };
let gateReconIsInitialized = false;
let gateReconCurrentSymbol = '';
let gateReconCurrentMarket = '';

function getReconUrl(exId, symbol, market) {
    if (exId === 'binance') return market === 'futures'
        ? `https://fapi.binance.com/fapi/v1/depth?symbol=${symbol}USDT&limit=1000`
        : `https://api.binance.com/api/v3/depth?symbol=${symbol}USDT&limit=1000`;
    if (exId === 'bybit') return market === 'futures'
        ? `https://api.bybit.com/v5/market/orderbook?category=linear&symbol=${symbol}USDT&limit=200`
        : `https://api.bybit.com/v5/market/orderbook?category=spot&symbol=${symbol}USDT&limit=200`;
    if (exId === 'okx') return market === 'futures'
        ? `https://www.okx.com/api/v5/market/books?instId=${symbol}-USDT-SWAP&sz=200`
        : `https://www.okx.com/api/v5/market/books?instId=${symbol}-USDT&sz=200`;
    if (exId === 'bitget') return market === 'futures'
        ? `https://api.bitget.com/api/v2/mix/market/merge-depth?symbol=${symbol}USDT&productType=USDT-FUTURES&limit=100`
        : `https://api.bitget.com/api/v2/spot/market/merge-depth?symbol=${symbol}USDT&limit=100`;

    // 🔥 GATE.IO теперь обрабатывается через WebSocket, REST URL не нужен
    if (exId === 'gate') return null;
    if (exId === 'mexc') return `/api/mexc-depth/?market=${market}&symbol=${symbol}`;
    return null;
}

// 🔥 НОВАЯ ФУНКЦИЯ: Прямой WebSocket для Recon Gate.io
function startGateReconWS(symbol, market) {
    if (!reconEnabled || !reconMarkets.gate[market]) return;

    // Если уже подключены к этому символу и рынку — не переподключаемся
    if (wsGateRecon && gateReconCurrentSymbol === symbol && gateReconCurrentMarket === market) {
        return;
    }

    // 🔥 Безопасное закрытие старого соединения (отключаем onclose, чтобы не было ложного реконнекта)
    if (wsGateRecon) {
        wsGateRecon.onclose = null;
        wsGateRecon.close();
        wsGateRecon = null;
    }

    gateReconBook = { bids: {}, asks: {} };
    gateReconIsInitialized = false;
    gateReconCurrentSymbol = symbol;
    gateReconCurrentMarket = market;

    const isFutures = market === 'futures';
    const wsUrl = isFutures ? "wss://fx-ws.gateio.ws/v4/ws/usdt" : "wss://api.gateio.ws/ws/v4/";
    const channel = isFutures ? "futures.order_book_update" : "spot.order_book_update";
    const cleanSymbol = `${symbol.toUpperCase()}_USDT`;

    try {
        wsGateRecon = new WebSocket(wsUrl);
    } catch (err) {
        console.warn('Gate Recon WS create error:', err);
        return;
    }

    wsGateRecon.onopen = () => {
        console.log(`✅ Gate Recon WS подключен: ${cleanSymbol} (${market})`);
        const subscribeMsg = {
            time: Math.floor(Date.now() / 1000),
            channel: channel,
            event: "subscribe",
            payload: isFutures ? [cleanSymbol, "100ms", "100"] : [cleanSymbol, "100ms"]
        };
        try {
            wsGateRecon.send(JSON.stringify(subscribeMsg));
        } catch (err) {
            console.warn('Gate Recon WS send error:', err);
        }
    };

    wsGateRecon.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);

            // Игнорируем heartbeat-ответы
            if (msg.event === 'pong' || msg.channel === 'futures.ping' || msg.channel === 'spot.ping') {
                return;
            }

            if (msg.event === 'update' && msg.channel === channel) {
                const result = msg.result;
                if (!result || result.s !== cleanSymbol) return;

                // 🔥 Первый пакет после подписки ВСЕГДА является полным снапшотом
                if (!gateReconIsInitialized) {
                    gateReconBook.bids = {};
                    gateReconBook.asks = {};
                    gateReconIsInitialized = true;
                }

                // Применяем дельты
                const updateBook = (side, deltas) => {
                    if (!Array.isArray(deltas)) return;
                    deltas.forEach(row => {
                        try {
                            const price = parseFloat(row[0]);
                            const qty = Math.abs(parseFloat(row[1]));
                            if (!isFinite(price) || price <= 0) return;
                            if (qty === 0) {
                                delete side[price];
                            } else {
                                side[price] = qty;
                            }
                        } catch (err) {}
                    });
                };

                if (result.b) updateBook(gateReconBook.bids, result.b);
                if (result.a) updateBook(gateReconBook.asks, result.a);

                // Мгновенная перерисовка линий Gate на графике
                updateGateReconLines(symbol, market);
            }
        } catch (err) {
            console.warn('Gate Recon WS parse error:', err);
        }
    };

    wsGateRecon.onclose = () => {
        console.log(`⚠️ Gate Recon WS закрыт`);
        wsGateRecon = null;
        gateReconIsInitialized = false;

        // Попытка переподключения через 3 секунды, ТОЛЬКО если это всё ещё актуальная монета и рынок
        if (reconEnabled && reconMarkets.gate[market] && currentSymbol === symbol) {
            setTimeout(() => {
                if (reconEnabled && reconMarkets.gate[market] && currentSymbol === symbol) {
                    startGateReconWS(symbol, market);
                }
            }, 3000);
        }
    };
}

function stopGateReconWS() {
    if (wsGateRecon) {
        try {
            wsGateRecon.onclose = null;
            wsGateRecon.close();
        } catch (err) {}
        wsGateRecon = null;
    }
    gateReconBook = { bids: {}, asks: {} };
    gateReconIsInitialized = false;
    gateReconCurrentSymbol = '';
    gateReconCurrentMarket = '';
}

// 🔥 НОВАЯ ФУНКЦИЯ: Отрисовка линий только для Gate на основе локального WS-стакана
function updateGateReconLines(symbol, market) {
    if (!candleSeries || !reconEnabled || !reconMarkets.gate[market]) return;
    if (currentSymbol !== symbol) return; // Защита от обновления неактивной монеты

    const minVolume = reconMinVolumes.gate[market];
    const ex = RECON_EXCHANGES.find(e => e.id === 'gate');
    const suffix = market === 'futures' ? 'F' : 'S';

    // Собираем плотности из локального стакана
    const newGateLines = [];
    const processSide = (bookSide) => {
        for (const [priceStr, qty] of Object.entries(bookSide)) {
            const price = parseFloat(priceStr);
            const volume = price * qty;
            if (isFinite(price) && isFinite(volume) && volume >= minVolume) {
                newGateLines.push({ price, volume });
            }
        }
    };

    processSide(gateReconBook.bids);
    processSide(gateReconBook.asks);

    // Сортируем по объему, берем топ-20 для чистоты графика
    newGateLines.sort((a, b) => b.volume - a.volume);
    const topDensities = newGateLines.slice(0, 20);

    // ⚠️ ВАЖНО: Очищаем ТОЛЬКО старые линии Gate для конкретного рынка (чтобы не задеть Spot, если обновляем Futures)
    clearSpecificReconLines('gate', market);

    topDensities.forEach(d => {
        const line = candleSeries.createPriceLine({
            price: d.price,
            color: 'rgba(245, 158, 11, 0.6)', // Оранжевый для Gate
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            axisLabelColor: '#ffffff',
            axisLabelBackgroundColor: 'rgba(245, 158, 11, 0.7)',
            title: `${ex.label}-${suffix} ${d.volume >= 1000 ? (d.volume/1000).toFixed(1)+'K' : d.volume.toFixed(0)}`
        });
        // 🔥 Сохраняем market в объекте линии для безопасной фильтрации
        reconLines.push({ exchange: 'gate', market: market, line: line });
    });
}

// 🔥 Точечная очистка линий конкретной биржи и (опционально) рынка
function clearSpecificReconLines(exchangeId, market = null) {
    if (!candleSeries) return;
    const toKeep = [];
    reconLines.forEach(item => {
        // Если market передан, удаляем только совпадающие. Если null — все линии этой биржи.
        const isMatch = item.exchange === exchangeId && (market === null || item.market === market);
        if (isMatch) {
            try { candleSeries.removePriceLine(item.line); } catch(e) {}
        } else {
            toKeep.push(item);
        }
    });
    reconLines = toKeep;
}

// ==========================================
// ОБНОВЛЕННАЯ ФУНКЦИЯ ЗАГРУЗКИ RECON
// ==========================================
async function loadReconDensities(symbol) {
    if (!reconEnabled || !candleSeries || isReconLoading) return;
    isReconLoading = true;

    try {
        // 🔥 1. Запускаем/обновляем WebSocket для Gate.io, если он включен
        if (reconMarkets.gate.futures) startGateReconWS(symbol, 'futures');
        if (reconMarkets.gate.spot) startGateReconWS(symbol, 'spot');

        // 🔥 2. Для остальных бирж используем старый проверенный REST (абсолютно без изменений в логике)
        const tasks = [];
        for (const ex of RECON_EXCHANGES) {
            if (ex.id === 'gate') continue; // Gate теперь на WS

            for (const market of ['spot', 'futures']) {
                if (!reconMarkets[ex.id][market]) continue;

                const url = getReconUrl(ex.id, symbol, market);
                if (!url) continue;

                tasks.push(fetch(url)
                    .then(res => res.ok ? res.json() : null)
                    .then(data => ({ ex: ex.id, market, data }))
                    .catch(() => ({ ex: ex.id, market, data: null }))
                );
            }
        }

        if (tasks.length > 0) {
            const results = await Promise.all(tasks);
            for (const r of results) {
                if (!r.data) continue;

                // Парсинг для Binance, Bybit, OKX, Bitget (как было раньше)
                let rawBids = [], rawAsks = [];
                if (r.ex === 'binance') {
                    rawBids = r.data.bids || []; rawAsks = r.data.asks || [];
                } else if (r.ex === 'bybit') {
                    rawBids = (r.data.result && r.data.result.b) || [];
                    rawAsks = (r.data.result && r.data.result.a) || [];
                } else if (r.ex === 'okx') {
                    const d = (r.data.data || [])[0] || {};
                    rawBids = d.bids || []; rawAsks = d.asks || [];
                } else if (r.ex === 'bitget') {
                    const inner = r.data.data || {};
                    rawBids = inner.bids || []; rawAsks = inner.asks || [];
                }

                const minVolume = reconMinVolumes[r.ex][r.market];
                const exConfig = RECON_EXCHANGES.find(e => e.id === r.ex);
                const suffix = r.market === 'futures' ? 'F' : 'S';

                const densities = [];
                const process = (arr) => {
                    for (const row of arr) {
                        const p = parseFloat(Array.isArray(row) ? row[0] : (row.p || row.price));
                        const q = Math.abs(parseFloat(Array.isArray(row) ? row[1] : (row.v || row.vol)));
                        if (!isFinite(p) || !isFinite(q) || p <= 0) continue;
                        const vol = p * q;
                        if (vol >= minVolume) densities.push({ price: p, volume: vol });
                    }
                };
                process(rawBids); process(rawAsks);

                densities.sort((a, b) => b.volume - a.volume);
                const top = densities.slice(0, 20);

                top.forEach(d => {
                    const line = candleSeries.createPriceLine({
                        price: d.price, color: 'rgba(255, 255, 255, 0.5)', lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true,
                        axisLabelColor: '#ffffff', axisLabelBackgroundColor: 'rgba(100, 100, 100, 0.7)',
                        title: `${exConfig.label}-${suffix} ${d.volume >= 1000 ? (d.volume/1000).toFixed(1)+'K' : d.volume.toFixed(0)}`
                    });
                    // 🔥 Сохраняем market в объекте линии для безопасной фильтрации
                    reconLines.push({ exchange: r.ex, market: r.market, line: line });
                });
            }
        }
    } finally {
        isReconLoading = false;
    }
}

function clearReconLines() {
    if (!candleSeries) return;
    reconLines.forEach(item => {
        try { candleSeries.removePriceLine(item.line); } catch(e){}
    });
    reconLines = [];
    stopGateReconWS(); // 🔥 Обязательно закрываем WS при полной очистке
}

// ==========================================
// ОБНОВЛЕННЫЙ STOP RECON
// ==========================================
function stopReconUpdates() {
    if (reconUpdateTimer) { clearInterval(reconUpdateTimer); reconUpdateTimer = null; }
    removeReconPanel();
    clearReconLines(); // Внутри уже есть stopGateReconWS()
}