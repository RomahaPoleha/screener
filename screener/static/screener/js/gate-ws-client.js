/**
 * Gate.io WebSocket Client для Recon
 * Основан на официальной документации: https://www.gate.com/docs/developers/futures/ws/en/
 *
 * ВАЖНО: WebSocket НЕ подпадает под CORS — подключение из браузера работает напрямую!
 */

class GateWsClient {
  constructor() {
    this.ws = null;
    this.connected = false;
    this.orderBooks = {
      futures: {}, // symbol -> {bids: Map, asks: Map}
      spot: {}
    };
    this.subscribedSymbols = { futures: new Set(), spot: new Set() };
    this.heartbeatInterval = null;
    this.reconnectTimeout = null;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 10;
    this.onUpdate = null; // callback при обновлении стакана

    // Gate.io WebSocket URLs
    this.FUTURES_WS_URL = 'wss://fx-ws.gateio.ws/v4/ws/usdt';
    this.SPOT_WS_URL = 'wss://api.gateio.ws/ws/v4/';
  }

  // Подключение к futures WebSocket
  connectFutures() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return;

    this.ws = new WebSocket(this.FUTURES_WS_URL);

    this.ws.onopen = () => {
      console.log('✅ Gate Futures WS подключен');
      this.connected = true;
      this.reconnectAttempts = 0;
      this._startHeartbeat();
      this._resubscribeAll('futures');
    };

    this.ws.onmessage = (event) => {
      this._handleMessage(event.data, 'futures');
    };

    this.ws.onclose = () => {
      console.log('⚠️ Gate Futures WS закрыт');
      this.connected = false;
      this._stopHeartbeat();
      this._scheduleReconnect();
    };

    this.ws.onerror = (err) => {
      console.error('❌ Gate Futures WS ошибка:', err);
    };
  }

  // Подключение к spot WebSocket
  connectSpot() {
    // Аналогично, но с SPOT_WS_URL и spot.order_book_update
    // ... (аналогичная логика)
  }

  // Подписаться на символ
  subscribe(symbol, market = 'futures') {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;

    const cleanSymbol = symbol.toUpperCase();
    const contract = market === 'futures'
      ? `${cleanSymbol}_USDT`
      : `${cleanSymbol}_USDT`;

    const msg = {
      time: Math.floor(Date.now() / 1000),
      channel: market === 'futures'
        ? 'futures.order_book_update'
        : 'spot.order_book_update',
      event: 'subscribe',
      payload: market === 'futures'
        ? [contract, '100ms', '20']  // 20 уровней, обновление каждые 100ms
        : [contract, '100ms']
    };

    this.ws.send(JSON.stringify(msg));
    this.subscribedSymbols[market].add(cleanSymbol);

    // Инициализируем пустой стакан
    this.orderBooks[market][cleanSymbol] = { bids: new Map(), asks: new Map() };
  }

  // Отписаться от символа
  unsubscribe(symbol, market = 'futures') {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;

    const cleanSymbol = symbol.toUpperCase();
    const contract = `${cleanSymbol}_USDT`;

    const msg = {
      time: Math.floor(Date.now() / 1000),
      channel: market === 'futures'
        ? 'futures.order_book_update'
        : 'spot.order_book_update',
      event: 'unsubscribe',
      payload: market === 'futures'
        ? [contract, '100ms']
        : [contract, '100ms']
    };

    this.ws.send(JSON.stringify(msg));
    this.subscribedSymbols[market].delete(cleanSymbol);
    delete this.orderBooks[market][cleanSymbol];
  }

  // Обработка входящего сообщения
  _handleMessage(raw, market) {
    let data;
    try { data = JSON.parse(raw); } catch { return; }

    // Пропускаем pong и служебные
    if (data.event === 'pong') return;
    if (data.channel === 'futures.ping' || data.channel === 'spot.ping') return;

    const expectedChannel = market === 'futures'
      ? 'futures.order_book_update'
      : 'spot.order_book_update';
    if (data.channel !== expectedChannel) return;

    const result = data.result;
    if (!result) return;

    // Определяем символ
    const contract = result.s; // "BTC_USDT"
    if (!contract) return;
    const symbol = contract.replace('_USDT', '');

    const book = this.orderBooks[market][symbol];
    if (!book) return;

    const isFull = result.full === true;

    if (isFull) {
      // Полный снапшот — заменяем стакан
      book.bids = this._parseLevels(result.b || []);
      book.asks = this._parseLevels(result.a || []);
    } else {
      // Дельта — обновляем
      this._applyDelta(book.bids, result.b || []);
      this._applyDelta(book.asks, result.a || []);
    }

    // Вызываем callback
    if (this.onUpdate) {
      this.onUpdate(symbol, market, book);
    }
  }

  // Парсинг уровней Gate (формат {p, s})
  _parseLevels(levels) {
    const map = new Map();
    for (const level of levels) {
      const price = parseFloat(level.p);
      const size = Math.abs(parseFloat(level.s));
      if (price > 0 && size > 0) {
        map.set(price, size);
      }
    }
    return map;
  }

  // Применение дельты
  _applyDelta(sideMap, levels) {
    for (const level of levels) {
      const price = parseFloat(level.p);
      const size = Math.abs(parseFloat(level.s));
      if (size === 0) {
        sideMap.delete(price); // Удалить уровень
      } else {
        sideMap.set(price, size); // Обновить/добавить
      }
    }
  }

  // Heartbeat (обязательно для Gate!)
  _startHeartbeat() {
    this._stopHeartbeat();
    this.heartbeatInterval = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({
          time: Math.floor(Date.now() / 1000),
          channel: 'futures.ping'
        }));
      }
    }, 15000); // Каждые 15 секунд (Gate требует < 17 сек)
  }

  _stopHeartbeat() {
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }
  }

  // Переподключение с exponential backoff
  _scheduleReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('❌ Gate WS: превышен лимит переподключений');
      return;
    }
    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
    this.reconnectAttempts++;
    console.log(`🔄 Gate WS: переподключение через ${delay}мс (попытка ${this.reconnectAttempts})`);
    this.reconnectTimeout = setTimeout(() => this.connectFutures(), delay);
  }

  // Получить плотности из локального стакана
  getDensities(symbol, market, minVolume) {
    const book = this.orderBooks[market]?.[symbol.toUpperCase()];
    if (!book) return [];

    const densities = [];

    for (const [price, size] of book.bids) {
      const volume = price * size;
      if (volume >= minVolume) {
        densities.push({ price, volume, side: 'buy' });
      }
    }

    for (const [price, size] of book.asks) {
      const volume = price * size;
      if (volume >= minVolume) {
        densities.push({ price, volume, side: 'sell' });
      }
    }

    return densities.sort((a, b) => b.volume - a.volume).slice(0, 20);
  }

  // Отключение
  disconnect() {
    this._stopHeartbeat();
    if (this.reconnectTimeout) clearTimeout(this.reconnectTimeout);
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.connected = false;
  }
}

// Глобальный экземпляр
const gateWsClient = new GateWsClient();