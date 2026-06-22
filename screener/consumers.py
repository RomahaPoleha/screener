import json
import asyncio
import websockets
from channels.generic.websocket import AsyncWebsocketConsumer

# Глобальное хранилище активных потоков
active_streams = {}
streams_lock = asyncio.Lock()


async def binance_ws_task(symbol, tf, market, queue):
    """WebSocket к Binance"""
    stream_name = f"{symbol.lower()}usdt@kline_{tf}"

    if market == 'spot':
        ws_url = f"wss://stream.binance.com:9443/ws/{stream_name}"
    else:
        ws_url = f"wss://fstream.binance.com/ws/{stream_name}"

    print(f"🚀 [WS] Запуск: {symbol} {tf} {market}")
    print(f"🔗 [WS] URL: {ws_url}")

    retry_count = 0
    max_retries = 5
    message_count = 0

    while retry_count < max_retries:
        try:
            print(f"🔄 [WS] Попытка подключения {retry_count + 1}/{max_retries}...")

            async with websockets.connect(
                    ws_url,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5
            ) as ws:
                print(f"✅ [WS] УСПЕШНО подключен: {symbol} ({market})")
                retry_count = 0

                async for message in ws:
                    message_count += 1
                    if message_count <= 3:  # Логируем первые 3 сообщения
                        print(f"📨 [WS] Сообщение #{message_count} от {symbol}: {message[:200]}...")

                    try:
                        data = json.loads(message)
                        k = data.get('k')

                        if not k:
                            print(f"⚠️ [WS] Нет ключа 'k' в сообщении")
                            continue

                        candle = {
                            'time': int(k['t']) // 1000,
                            'open': float(k['o']),
                            'high': float(k['h']),
                            'low': float(k['l']),
                            'close': float(k['c'])
                        }

                        if message_count <= 3:  # Логируем первые 3 свечи
                            print(f"📊 [WS] Свеча #{message_count} для {symbol}: {candle}")

                        await queue.put(candle)

                        if message_count <= 3:
                            print(f"✅ [WS] Свеча #{message_count} отправлена в queue")

                    except Exception as e:
                        print(f"⚠️ [WS] Ошибка парсинга: {e}")
                        print(f"⚠️ [WS] Сообщение: {message[:300]}")

        except asyncio.CancelledError:
            print(f"🛑 [WS] Отменен: {symbol} (получено {message_count} сообщений)")
            break
        except asyncio.TimeoutError:
            print(f"⏰ [WS] ТАЙМАУТ подключения: {symbol}")
            retry_count += 1
            if retry_count < max_retries:
                await asyncio.sleep(3)
        except Exception as e:
            print(f"❌ [WS] Ошибка: {type(e).__name__}: {e}")
            retry_count += 1
            if retry_count < max_retries:
                await asyncio.sleep(3)

    if retry_count >= max_retries:
        print(f"❌ [WS] Превышено число попыток для {symbol}")
        await queue.put({'error': f'WebSocket connection failed after {max_retries} retries'})


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

        if self.read_task and not self.read_task.done():
            self.read_task.cancel()
            try:
                await self.read_task
            except asyncio.CancelledError:
                pass

        if self.stream_key:
            await asyncio.sleep(0.5)
            await self.unsubscribe_stream()

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

                if self.stream_key:
                    await self.unsubscribe_stream()

                self.symbol = new_symbol
                self.tf = new_tf
                self.market_type = new_market

                if self.symbol:
                    await self.subscribe_stream()
        except Exception as e:
            print(f"❌ Ошибка receive: {e}")

    async def subscribe_stream(self):
        self.stream_key = f"{self.symbol}_{self.tf}_{self.market_type}"

        async with streams_lock:
            if self.stream_key not in active_streams:
                queue = asyncio.Queue()
                task = asyncio.create_task(
                    binance_ws_task(
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
                print(f"🆕 Создан поток: {self.stream_key}")

            active_streams[self.stream_key]['subscribers'].add(self.channel_name)
            self.queue = active_streams[self.stream_key]['queue']

        self.read_task = asyncio.create_task(self.read_from_queue())
        print(f"✅ Подписан на {self.stream_key} (подписчиков: {len(active_streams[self.stream_key]['subscribers'])})")

    async def unsubscribe_stream(self):
        if not self.stream_key or self.stream_key not in active_streams:
            return

        async with streams_lock:
            stream = active_streams[self.stream_key]
            stream['subscribers'].discard(self.channel_name)

            subscribers_count = len(stream['subscribers'])
            print(f"📊 Отписка от {self.stream_key}, осталось подписчиков: {subscribers_count}")

            if subscribers_count == 0:
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
        candle_count = 0
        try:
            while True:
                candle = await self.queue.get()
                candle_count += 1
                if candle_count <= 3:  # Логируем первые 3 отправки
                    print(f"📤 [READ] Отправка клиенту #{candle_count}: {candle}")
                await self.send(text_data=json.dumps(candle))
        except asyncio.CancelledError:
            print(f"🛑 [READ] Отменен (отправлено {candle_count} свечей)")
        except Exception as e:
            print(f"❌ Ошибка чтения queue: {e}")