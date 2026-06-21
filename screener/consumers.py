import json
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import ccxt
import time


class CandleConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.symbol = None
        self.tf = None
        self.market_type = None
        self.exchange = None
        self.ws = None
        self.poll_task = None

        await self.accept()
        print("✅ WebSocket подключен")

    async def disconnect(self, close_code):
        if self.poll_task:
            self.poll_task.cancel()
        if self.ws:
            await self.ws.close()
        print("🔌 WebSocket отключен")

    async def receive(self, text_data):
        data = json.loads(text_data)
        self.symbol = data.get('symbol')
        self.tf = data.get('tf', '1m')
        self.market_type = data.get('market', 'future')

        print(f"📊 Запрос: {self.symbol} {self.tf} {self.market_type}")

        if self.symbol:
            await self.start_candle_stream()

    async def start_candle_stream(self):
        """Запускает поток свечей (WebSocket или polling)"""
        if self.market_type == 'spot':
            # Spot: используем WebSocket Binance
            await self.start_spot_websocket()
        else:
            # Futures: polling с отправкой в WebSocket
            await self.start_futures_polling()

    async def start_spot_websocket(self):
        """WebSocket для Spot"""
        stream_name = f"{self.symbol.lower()}usdt@kline_{self.tf}"
        ws_url = f"wss://stream.binance.com:9443/ws/{stream_name}"

        import websockets
        try:
            async with websockets.connect(ws_url) as websocket:
                self.ws = websocket
                async for message in websocket:
                    data = json.loads(message)
                    k = data['k']
                    candle = {
                        'time': int(k['t']) // 1000,
                        'open': float(k['o']),
                        'high': float(k['h']),
                        'low': float(k['l']),
                        'close': float(k['c'])
                    }
                    await self.send(text_data=json.dumps(candle))
        except Exception as e:
            print(f"❌ Spot WS ошибка: {e}")
            await self.send(text_data=json.dumps({'error': str(e)}))

    async def start_futures_polling(self):
        """Polling для Futures (быстрый)"""
        pair = f"{self.symbol}/USDT:USDT"

        exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })

        try:
            while True:
                ohlcv = exchange.fetch_ohlcv(pair, timeframe=self.tf, limit=1)
                if ohlcv:
                    ts, o, h, l, c, v = ohlcv[0]
                    candle = {
                        'time': int(ts / 1000),
                        'open': float(o),
                        'high': float(h),
                        'low': float(l),
                        'close': float(c)
                    }
                    await self.send(text_data=json.dumps(candle))

                await asyncio.sleep(0.5)  # 500мс

        except asyncio.CancelledError:
            print("🛑 Futures polling остановлен")
        except Exception as e:
            print(f"❌ Futures polling ошибка: {e}")
            await self.send(text_data=json.dumps({'error': str(e)}))