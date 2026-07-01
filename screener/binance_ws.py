import asyncio
import websockets
import json


async def binance_candle_stream(symbol, tf, callback):
    """
    WebSocket к Binance Futures (новый endpoint /market/ws/)
    """
    stream_name = f"{symbol.lower()}usdt@kline_{tf}"
    url = f"wss://fstream.binance.com/market/ws/{stream_name}"

    print(f"🔗 Подключение к Binance: {url}", flush=True)

    try:
        async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10
        ) as ws:
            print(f"✅ WS к Binance подключен: {symbol} {tf}", flush=True)

            while True:
                try:
                    message = await ws.recv()
                    data = json.loads(message)

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
                        print(f"🕯️ Свеча: {candle['time']} close={candle['close']}", flush=True)
                        await callback(candle)

                except websockets.exceptions.ConnectionClosed:
                    print(f"⚠️ WS закрыт: {symbol} {tf}", flush=True)
                    break
                except Exception as e:
                    print(f"❌ Ошибка: {e}", flush=True)
                    await asyncio.sleep(2)
                    break

    except Exception as e:
        print(f"❌ Ошибка подключения: {e}", flush=True)
        import traceback
        traceback.print_exc()