import asyncio
import websockets
import json


async def binance_candle_stream(symbol, tf, callback):
    """
    WebSocket к Binance Futures (новый формат URL)
    """
    stream_name = f"{symbol.lower()}usdt@kline_{tf}"
    # ❗️ НОВЫЙ формат URL с /stream?streams=
    url = f"wss://fstream.binance.com/stream?streams={stream_name}"

    print(f"🔗 Подключение к Binance: {url}", flush=True)

    try:
        async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10
        ) as ws:
            print(f"✅ WS к Binance подключен: {symbol} {tf}", flush=True)

            # ❗️ Отправляем SUBSCRIBE (обязательно для нового URL)
            subscribe_msg = {
                "method": "SUBSCRIBE",
                "params": [stream_name],
                "id": 1
            }
            await ws.send(json.dumps(subscribe_msg))
            print(f"📤 Отправлена подписка: {stream_name}", flush=True)

            while True:
                try:
                    message = await ws.recv()
                    print(f"📨 Получено от Binance ({len(message)} bytes)", flush=True)

                    data = json.loads(message)

                    # Ответ на подписку
                    if 'result' in data and data.get('id') == 1:
                        print(f"✅ Подписка подтверждена", flush=True)
                        continue

                    # Новый формат: данные приходят в data['data']
                    if 'stream' in data and 'data' in data:
                        inner_data = data['data']

                        if 'e' in inner_data and inner_data['e'] == 'kline' and 'k' in inner_data:
                            k = inner_data['k']
                            candle = {
                                'time': int(k['t'] / 1000),
                                'open': float(k['o']),
                                'high': float(k['h']),
                                'low': float(k['l']),
                                'close': float(k['c'])
                            }
                            print(f"🕯️ Отправка свечи: {candle['time']} close={candle['close']}", flush=True)
                            await callback(candle)
                    else:
                        print(f"ℹ️ Другое сообщение: {data}", flush=True)

                except websockets.exceptions.ConnectionClosed:
                    print(f"⚠️ WS закрыт Binance: {symbol} {tf}", flush=True)
                    break
                except Exception as e:
                    print(f"❌ Ошибка обработки: {e}", flush=True)
                    import traceback
                    traceback.print_exc()
                    await asyncio.sleep(2)
                    break

    except Exception as e:
        print(f"❌ Ошибка подключения к Binance: {e}", flush=True)
        import traceback
        traceback.print_exc()