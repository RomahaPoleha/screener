import json
import asyncio
import websockets
from channels.generic.websocket import AsyncWebsocketConsumer


class CandleConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.symbol = None
        self.tf = None
        self.market_type = None
        self.ws_binance = None
        self.is_active = True

        await self.accept()
        print(f"✅ WebSocket подключен: {self.channel_name}")

    async def disconnect(self, close_code):
        print(f"🔌 WebSocket отключен: {self.channel_name}, код: {close_code}")
        self.is_active = False
        if self.ws_binance:
            await self.ws_binance.close()

    async def receive(self, text_data):
        print(f"📩 Получено сообщение: {text_data}")
        try:
            data = json.loads(text_data)
            self.symbol = data.get('symbol')
            self.tf = data.get('tf', '1m')
            self.market_type = data.get('market', 'future')

            print(f"📊 Запрос: {self.symbol} {self.tf} {self.market_type}")

            # Останавливаем старое соединение
            if self.ws_binance:
                await self.ws_binance.close()
                self.ws_binance = None

            if self.symbol:
                await self.start_candle_stream()
        except Exception as e:
            print(f"❌ Ошибка receive: {e}")
            await self.send(text_data=json.dumps({'error': str(e)}))

    async def start_candle_stream(self):
        """Запускает WebSocket поток свечей"""
        print(f"🚀 Запуск WebSocket для {self.symbol} {self.tf} {self.market_type}")

        if self.market_type == 'spot':
            # Spot WebSocket
            stream_name = f"{self.symbol.lower()}usdt@kline_{self.tf}"
            ws_url = f"wss://stream.binance.com:9443/ws/{stream_name}"
        else:
            # Futures WebSocket
            stream_name = f"{self.symbol.lower()}usdt@kline_{self.tf}"
            ws_url = f"wss://fstream.binance.com/ws/{stream_name}"

        print(f"🔗 WebSocket URL: {ws_url}")

        try:
            async with websockets.connect(ws_url, ping_interval=20) as websocket:
                self.ws_binance = websocket
                print(f"✅ WebSocket подключен: {self.symbol} ({self.market_type})")

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
                        print(f"📊 Свеча {self.symbol}: {candle}")
                        await self.send(text_data=json.dumps(candle))
                    except Exception as e:
                        print(f"⚠️ Ошибка парсинга: {e}")
        except Exception as e:
            print(f"❌ WebSocket ошибка: {e}")
            if self.is_active:
                await self.send(text_data=json.dumps({'error': f'WebSocket: {str(e)}'}))