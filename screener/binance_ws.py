import asyncio
import websockets
import json


async def binance_candle_stream(symbol, tf, callback):
    """
    WebSocket к Binance Futures с логированием
    """
    stream_name = f"{symbol.lower()}usdt@kline_{tf}"
    url = f"wss://fstream.binance.com/ws/{stream_name}"

    print(f"🔗 Подключение к Binance: {url}")

    try:
        async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10
        ) as ws:
            print(f"✅ WS к Binance подключен: {symbol} {tf}")

            while True:
                try:
                    message = await ws.recv()
                    print(f"📨 Получено от Binance ({len(message)} bytes)")

                    data = json.loads(message)

                    # Binance может слать текстовый Ping (редко)
                    if isinstance(data, str) and data == 'PING':
                        await ws.send('PONG')
                        continue

                    # Обрабатываем свечу
                    if 'k' in data:
                        k = data['k']
                        candle = {
                            'time': int(k['t'] / 1000),
                            'open': float(k['o']),
                            'high': float(k['h']),
                            'low': float(k['l']),
                            'close': float(k['c'])
                        }
                        print(f"🕯️ Отправка свечи: {candle['time']}")
                        await callback(candle)

                except websockets.exceptions.ConnectionClosed:
                    print(f"⚠️ WS закрыт Binance: {symbol} {tf}")
                    break
                except Exception as e:
                    print(f"❌ Ошибка обработки: {e}")
                    await asyncio.sleep(2)
                    break

    except Exception as e:
        print(f"❌ Ошибка подключения к Binance: {e}")
        import traceback
        traceback.print_exc()