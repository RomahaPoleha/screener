import json
import asyncio
import ccxt.async_support as ccxt_async
from channels.generic.websocket import AsyncWebsocketConsumer

active_streams = {}
streams_lock = asyncio.Lock()

# Один общий exchange для Futures
shared_exchange = None


async def get_shared_exchange():
    global shared_exchange
    if shared_exchange is None:
        shared_exchange = ccxt_async.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
    return shared_exchange


async def binance_polling_task(symbol, tf, queue):
    """Polling к Binance Futures"""
    print(f"🚀 [POLL] Запуск: {symbol} {tf}")

    exchange = await get_shared_exchange()
    pair = f"{symbol}/USDT:USDT"

    try:
        print(f"✅ [POLL] Подключен: {symbol}")
        last_candle = None

        while True:
            try:
                ohlcv = await exchange.fetch_ohlcv(pair, timeframe=tf, limit=1)
                if ohlcv:
                    ts, o, h, l, c, v = ohlcv[0]
                    candle = {
                        'time': int(ts / 1000),
                        'open': float(o),
                        'high': float(h),
                        'low': float(l),
                        'close': float(c)
                    }

                    if last_candle != candle:
                        await queue.put(candle)
                        last_candle = candle

                await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                print(f"🛑 [POLL] Отменен: {symbol}")
                break
            except Exception as e:
                print(f"⚠️ [POLL] Ошибка: {e}")
                await asyncio.sleep(2)
    except Exception as e:
        print(f"❌ [POLL] Критическая ошибка: {e}")


class CandleConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.symbol = None
        self.tf = None
        self.stream_key = None
        self.queue = None
        self.read_task = None

        await self.accept()
        print(f"✅ Клиент подключен: {self.channel_name}")

    async def disconnect(self, close_code):
        print(f"🔌 Клиент отключен: {self.channel_name}")

        if self.read_task and not self.read_task.done():
            self.read_task.cancel()

        if self.stream_key:
            await self.unsubscribe_stream()

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            new_symbol = data.get('symbol')
            new_tf = data.get('tf', '1m')

            print(f"📩 Запрос: {new_symbol} {new_tf}")

            if (new_symbol != self.symbol or new_tf != self.tf):
                if self.stream_key:
                    await self.unsubscribe_stream()

                self.symbol = new_symbol
                self.tf = new_tf

                if self.symbol:
                    await self.subscribe_stream()
        except Exception as e:
            print(f"❌ Ошибка receive: {e}")

    async def subscribe_stream(self):
        self.stream_key = f"{self.symbol}_{self.tf}_future"

        async with streams_lock:
            if self.stream_key not in active_streams:
                queue = asyncio.Queue()
                task = asyncio.create_task(
                    binance_polling_task(self.symbol, self.tf, queue)
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
            if self.stream_key not in active_streams:
                return

            stream = active_streams[self.stream_key]
            stream['subscribers'].discard(self.channel_name)

            subscribers_count = len(stream['subscribers'])
            print(f"📊 Отписка от {self.stream_key}, осталось: {subscribers_count}")

            if subscribers_count == 0:
                stream['task'].cancel()
                del active_streams[self.stream_key]
                print(f"🗑️ Удален поток: {self.stream_key}")

        self.stream_key = None
        self.queue = None

    async def read_from_queue(self):
        try:
            while True:
                candle = await self.queue.get()
                await self.send(text_data=json.dumps(candle))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"❌ Ошибка чтения queue: {e}")