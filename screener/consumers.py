import json
import asyncio
import ccxt.async_support as ccxt_async
import websockets
from channels.generic.websocket import AsyncWebsocketConsumer


class CandleConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.symbol = None
        self.tf = None
        self.market_type = None
        self.ws_binance = None
        self.poll_task = None
        self.exchange = None
        self.is_active = True

        await self.accept()
        print(f"✅ WebSocket подключен: {self.channel_name}")

    async def disconnect(self, close_code):
        print(f"🔌 WebSocket отключен: {self.channel_name}, код: {close_code}")
        self.is_active = False
        if self.poll_task:
            self.poll_task.cancel()
            try:
                await self.poll_task
            except asyncio.CancelledError:
                pass
        if self.ws_binance:
            await self.ws_binance.close()
        if self.exchange:
            await self.exchange.close()

    async def receive(self, text_data):
        print(f"📩 Получено сообщение: {text_data}")
        try:
            data = json.loads(text_data)
            self.symbol = data.get('symbol')
            self.tf = data.get('tf', '1m')
            self.market_type = data.get('market', 'future')

            print(f"📊 Запрос: {self.symbol} {self.tf} {self.market_type}")

            # Останавливаем старый поток
            if self.poll_task:
                self.poll_task.cancel()
                try:
                    await self.poll_task
                except asyncio.CancelledError:
                    pass
            if self.ws_binance:
                await self.ws_binance.close()
                self.ws_binance = None

            if self.symbol:
                await self.start_candle_stream()
        except Exception as e:
            print(f"❌ Ошибка receive: {e}")
            await self.send(text_data=json.dumps({'error': str(e)}))

    async def start_candle_stream(self):
        """Запускает поток свечей"""
        print(f"🚀 Запуск потока для {self.symbol} {self.tf} {self.market_type}")
        if self.market_type == 'spot':
            await self.start_spot_websocket()
        else:
            await self.start_futures_polling()

    async def start_spot_websocket(self):
        """WebSocket для Spot напрямую к Binance"""
        stream_name = f"{self.symbol.lower()}usdt@kline_{self.tf}"
        ws_url = f"wss://stream.binance.com:9443/ws/{stream_name}"
        print(f"🔗 Spot WS URL: {ws_url}")

        try:
            async with websockets.connect(ws_url, ping_interval=20) as websocket:
                self.ws_binance = websocket
                print(f"✅ Spot WS подключен: {self.symbol}")
                async for message in websocket:
                    if not self.is_active:
                        break
                    try:
                        data = json.loads(message)
                        k = data['k']
                        candle = {
                            'time': int(k['t']) // 1000,
                            'open': float(k['o']),
                            'high': float(k['h']),
                            'low': float(k['l']),
                            'close': float(k['c'])
                        }
                        print(f"📊 Spot свеча: {candle}")
                        await self.send(text_data=json.dumps(candle))
                    except Exception as e:
                        print(f"⚠️ Ошибка парсинга Spot: {e}")
        except Exception as e:
            print(f"❌ Spot WS ошибка: {e}")
            if self.is_active:
                await self.send(text_data=json.dumps({'error': f'Spot WS: {str(e)}'}))

    async def start_futures_polling(self):
        """Polling для Futures через АСИНХРОННЫЙ ccxt"""
        pair = f"{self.symbol}/USDT:USDT"
        print(f"🔗 Futures polling для: {pair}")

        try:
            # Используем асинхронную версию ccxt!
            self.exchange = ccxt_async.binance({
                'enableRateLimit': True,
                'options': {'defaultType': 'future'}
            })

            print(f"🚀 Futures polling запущен: {self.symbol} {self.tf}")

            while self.is_active:
                try:
                    ohlcv = await self.exchange.fetch_ohlcv(pair, timeframe=self.tf, limit=1)
                    if ohlcv:
                        ts, o, h, l, c, v = ohlcv[0]
                        candle = {
                            'time': int(ts / 1000),
                            'open': float(o),
                            'high': float(h),
                            'low': float(l),
                            'close': float(c)
                        }
                        print(f"📊 Futures свеча: {candle}")
                        await self.send(text_data=json.dumps(candle))
                except Exception as e:
                    print(f"⚠️ Futures polling ошибка: {e}")
                    await asyncio.sleep(2)
                    continue

                await asyncio.sleep(0.5)  # 500мс

        except asyncio.CancelledError:
            print(f"🛑 Futures polling остановлен: {self.symbol}")
        except Exception as e:
            print(f"❌ Futures polling критическая ошибка: {e}")
            if self.is_active:
                await self.send(text_data=json.dumps({'error': f'Futures: {str(e)}'}))