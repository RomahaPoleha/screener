import asyncio
import websockets
import json


async def binance_candle_stream(symbol, tf, callback):
    """
    WebSocket к Binance Futures
    ВАЖНО: При подключении к /ws/<streamName> подписка автоматическая!
    Отправлять SUBSCRIBE НЕ НУЖНО!
    """
    stream_name = f"{symbol.lower()}usdt@kline_{tf}"
    url = f"wss://fstream.binance.com/ws/{stream_name}"

    print(f"🔗 Подключение к Binance: {url}", flush=True)

    try:
        async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10
        ) as ws:
            print(f"✅ WS к Binance подключен: {symbol} {tf}", flush=True)

            # ❌ НЕ отправляем SUBSCRIBE — стрим уже подписан через URL!

            while True:
                try:
                    message = await ws.recv()
                    print(f"📨 Получено от Binance ({len(message)} bytes)", flush=True)

                    data = json.loads(message)

                    # Обрабатываем свечу
                    if 'e' in data and data['e'] == 'kline' and 'k' in data:
                        k = data['k']
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
                    await asyncio.sleep(2)
                    break

    except Exception as e:
        print(f"❌ Ошибка подключения к Binance: {e}", flush=True)
        import traceback
        traceback.print_exc()