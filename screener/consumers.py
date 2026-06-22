import json
import asyncio
import websockets
from channels.generic.websocket import AsyncWebsocketConsumer

# Глобальное хранилище активных потоков
active_streams = {}
streams_lock = asyncio.Lock()


async def binance_stream_task(symbol, tf, market, queue):
    """Задача: подключается к Binance и шлёт свечи в queue"""
    if market == 'spot':
        stream_name = f"{symbol.lower()}usdt@kline_{tf}"
        ws_url = f"wss://stream.binance.com:9443/ws/{stream_name}"
    else:
        stream_name = f"{symbol.lower()}usdt@kline_{tf}"
        ws_url = f"wss://fstream.binance.com/ws/{stream_name}"

    print(f"🚀 Запуск потока: {symbol} {tf} {market}")

    retry_count = 0
    max_retries = 5

    while retry_count < max_retries:
        try:
            print(f"🔗 Подключение к Binance: {ws_url}")
            async with websockets.connect(
                    ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5
            ) as ws:
                print(f"✅ Binance WebSocket подключен: {symbol}")
                retry_count = 0

                async for message in ws:
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
                        # Отправляем всем подписчикам
                        await queue.put(candle)
                    except Exception as e:
                        print(f"⚠️ Ошибка парсинга: {e}")

        except asyncio.CancelledError:
            print(f"🛑 Поток отменен: {symbol}")
            break
        except Exception as e:
            print(f" Ошибка подключения: {e}")
            retry_count += 1
            if retry_count < max_retries:
                await asyncio.sleep(2)
            else:
                await queue.put({'error': f'Connection failed: {str(e)}'})
                break


class CandleConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.symbol = None
        self.tf = None
        self.market_type = None
        self.stream_key = None
        self.queue = None
        self.read_task = None

        await self.accept()
        print(f"✅ Клиент подключен: {self.channel_name}")

    async def disconnect(self, close_code):
        print(f"🔌 Клиент отключен: {self.channel_name}")

        # Отменяем задачу чтения
        if self.read_task and not self.read_task.done():
            self.read_task.cancel()
            try:
                await self.read_task
            except asyncio.CancelledError:
                pass

        # Отписываемся от потока
        if self.stream_key:
            await self.unsubscribe_stream()

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            new_symbol = data.get('symbol')
            new_tf = data.get('tf', '1m')
            new_market = data.get('market', 'future')

            print(f" Запрос: {new_symbol} {new_tf} {new_market}")

            # Если символ изменился — переподписываемся
            if (new_symbol != self.symbol or
                    new_tf != self.tf or
                    new_market != self.market_type):

                # Отписываемся от старого
                if self.stream_key:
                    await self.unsubscribe_stream()

                # Подписываемся на новый
                self.symbol = new_symbol
                self.tf = new_tf
                self.market_type = new_market

                if self.symbol:
                    await self.subscribe_stream()
        except Exception as e:
            print(f"❌ Ошибка receive: {e}")

    async def subscribe_stream(self):
        """Подписываемся на поток свечей"""
        self.stream_key = f"{self.symbol}_{self.tf}_{self.market_type}"

        async with streams_lock:
            if self.stream_key not in active_streams:
                # Создаём новый поток
                queue = asyncio.Queue()
                task = asyncio.create_task(
                    binance_stream_task(
                        self.symbol,
                        self.tf,
                        self.market_type,
                        queue
                    )
                )
                active_streams[self.stream_key] = {
                    'task': task,
                    'queue': queue,
                    'subscribers': set()
                }
                print(f"🆕 Создан новый поток: {self.stream_key}")

            # Добавляем себя в подписчики
            active_streams[self.stream_key]['subscribers'].add(self.channel_name)
            self.queue = active_streams[self.stream_key]['queue']

        # Запускаем задачу чтения из queue
        self.read_task = asyncio.create_task(self.read_from_queue())
        print(f"✅ Подписан на {self.stream_key} (всего: {len(active_streams[self.stream_key]['subscribers'])})")

    async def unsubscribe_stream(self):
        """Отписываемся от потока"""
        if not self.stream_key or self.stream_key not in active_streams:
            return

        async with streams_lock:
            stream = active_streams[self.stream_key]
            stream['subscribers'].discard(self.channel_name)

            # Если подписчиков не осталось — удаляем поток
            if len(stream['subscribers']) == 0:
                stream['task'].cancel()
                try:
                    await stream['task']
                except asyncio.CancelledError:
                    pass
                del active_streams[self.stream_key]
                print(f"🗑️ Удален поток: {self.stream_key}")

        self.stream_key = None
        self.queue = None

    async def read_from_queue(self):
        """Читает свечи из queue и отправляет клиенту"""
        try:
            while True:
                candle = await self.queue.get()
                await self.send(text_data=json.dumps(candle))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"❌ Ошибка чтения queue: {e}")