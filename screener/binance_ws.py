import asyncio
import websockets
import json


async def binance_candle_stream(symbol, tf, callback):
    """
    WebSocket к Binance Futures с автоматической подпиской
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

            # ❗️ ВАЖНО: Отправляем сообщение подписки!
            subscribe_msg = {
                "method": "SUBSCRIBE",
                "params": [stream_name],
                "id": 1
            }
            await ws.send(json.dumps(subscribe_msg))
            print(f"📤 Отправлена подписка: {stream_name}")

            while True:
                try:
                    message = await ws.recv()
                    print(f"📨 Получено от Binance ({len(message)} bytes)")

                    data = json.loads(message)

                    # Ответ на подписку (игнорируем)
                    if 'result' in data and data.get('id') == 1:
                        print(f"✅ Подписка подтверждена")
                        continue

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