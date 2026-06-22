import json
import asyncio
import websockets
from channels.generic.websocket import AsyncWebsocketConsumer


class CandleConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.symbol = None
        self.tf = None
        self.market_type = None
        self.ws_binance = None
        self.is_active = False
        self.receive_task = None

    async def connect(self):
        await self.accept()
        self.is_active = True
        print(f"✅ WebSocket подключен: {self.channel_name}")

    async def disconnect(self, close_code):
        print(f" WebSocket отключен: {self.channel_name}, код: {close_code}")
        self.is_active = False

        # Отменяем задачу receive
        if self.receive_task:
            self.receive_task.cancel()
            try:
                await self.receive_task
            except asyncio.CancelledError:
                pass

        # Закрываем соединение с Binance
        if self.ws_binance:
            try:
                await self.ws_binance.close()
            except:
                pass
            self.ws_binance = None

    async def receive(self, text_data):
        """Получаем запрос от клиента"""
        try:
            data = json.loads(text_data)
            new_symbol = data.get('symbol')
            new_tf = data.get('tf', '1m')
            new_market = data.get('market', 'future')

            print(f"📩 Запрос: {new_symbol} {new_tf} {new_market}")

            # Если символ изменился — закрываем старое соединение
            if self.symbol != new_symbol or self.tf != new_tf or self.market_type != new_market:
                if self.ws_binance:
                    print(f"🔄 Закрываем старое соединение: {self.symbol}")
                    try:
                        await self.ws_binance.close()
                    except:
                        pass
                    self.ws_binance = None

                self.symbol = new_symbol
                self.tf = new_tf
                self.market_type = new_market

                # Запускаем новое соединение
                if self.symbol:
                    await self.start_candle_stream()
        except Exception as e:
            print(f"❌ Ошибка receive: {e}")
            await self.send(text_data=json.dumps({'error': str(e)}))

    async def start_candle_stream(self):
        """Запускает WebSocket поток свечей"""
        if not self.symbol or not self.is_active:
            return

        print(f"🚀 Запуск WebSocket для {self.symbol} {self.tf} {self.market_type}")

        if self.market_type == 'spot':
            stream_name = f"{self.symbol.lower()}usdt@kline_{self.tf}"
            ws_url = f"wss://stream.binance.com:9443/ws/{stream_name}"
        else:
            stream_name = f"{self.symbol.lower()}usdt@kline_{self.tf}"
            ws_url = f"wss://fstream.binance.com/ws/{stream_name}"

        print(f" WebSocket URL: {ws_url}")

        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as websocket:
                self.ws_binance = websocket
                print(f"✅ WebSocket подключен: {self.symbol} ({self.market_type})")

                async for message in websocket:
                    if not self.is_active:
                        print(f"🛑 Остановка потока: {self.symbol}")
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
        except Exception as e:
            print(f"❌ WebSocket ошибка: {e}")
            if self.is_active:
                await self.send(text_data=json.dumps({'error': f'WebSocket: {str(e)}'}))