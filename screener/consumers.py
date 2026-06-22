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
        self.stream_task = None
        self.is_running = False

        await self.accept()
        print(f"✅ WebSocket подключен: {self.channel_name}")

    async def disconnect(self, close_code):
        print(f"🔌 WebSocket отключен: {self.channel_name}")
        self.is_running = False

        if self.stream_task and not self.stream_task.done():
            self.stream_task.cancel()
            try:
                await self.stream_task
            except asyncio.CancelledError:
                pass

        if self.ws_binance:
            try:
                await self.ws_binance.close()
            except:
                pass

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            new_symbol = data.get('symbol')
            new_tf = data.get('tf', '1m')
            new_market = data.get('market', 'future')

            print(f"📩 Запрос: {new_symbol} {new_tf} {new_market}")

            if (new_symbol != self.symbol or
                    new_tf != self.tf or
                    new_market != self.market_type):

                self.is_running = False
                if self.stream_task and not self.stream_task.done():
                    self.stream_task.cancel()
                    try:
                        await self.stream_task
                    except asyncio.CancelledError:
                        pass

                if self.ws_binance:
                    try:
                        await self.ws_binance.close()
                    except:
                        pass

                self.symbol = new_symbol
                self.tf = new_tf
                self.market_type = new_market
                self.is_running = True

                if self.symbol:
                    self.stream_task = asyncio.create_task(
                        self.start_candle_stream()
                    )
        except Exception as e:
            print(f"❌ Ошибка receive: {e}")

    async def start_candle_stream(self):
        if not self.symbol or not self.is_running:
            return

        print(f"🚀 Запуск WebSocket для {self.symbol} {self.tf} {self.market_type}")

        if self.market_type == 'spot':
            stream_name = f"{self.symbol.lower()}usdt@kline_{self.tf}"
            ws_url = f"wss://stream.binance.com:9443/ws/{stream_name}"
        else:
            stream_name = f"{self.symbol.lower()}usdt@kline_{self.tf}"
            ws_url = f"wss://fstream.binance.com/ws/{stream_name}"

        retry_count = 0
        max_retries = 3

        while retry_count < max_retries and self.is_running:
            try:
                print(f"🔗 Подключение к: {ws_url}")
                async with websockets.connect(
                        ws_url,
                        ping_interval=20,
                        ping_timeout=10,
                        close_timeout=5
                ) as websocket:
                    self.ws_binance = websocket
                    print(f"✅ WebSocket подключен: {self.symbol}")
                    retry_count = 0

                    async for message in websocket:
                        if not self.is_running:
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
                            await self.send(text_data=json.dumps(candle))
                        except Exception as e:
                            print(f"⚠️ Ошибка парсинга: {e}")

            except asyncio.CancelledError:
                print(f"🛑 Стрим отменен: {self.symbol}")
                break
            except Exception as e:
                print(f"❌ WebSocket ошибка: {e}")
                retry_count += 1
                if retry_count < max_retries and self.is_running:
                    await asyncio.sleep(2)