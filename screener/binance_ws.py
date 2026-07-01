import asyncio
import websockets
import json


async def binance_candle_stream(symbol, tf, callback):
    """
    WebSocket к Binance Futures с автоматическим Ping/Pong

    Args:
        symbol: Символ монеты (например, 'BTC')
        tf: Таймфрейм (например, '1m')
        callback: Асинхронная функция для обработки свечи
    """
    stream_name = f"{symbol.lower()}usdt@kline_{tf}"
    url = f"wss://fstream.binance.com/ws/{stream_name}"

    # ping_interval=20 — шлём свой Ping каждые 20 сек
    # ping_timeout=20 — ждём Pong 20 сек
    # close_timeout=10 — ждём закрытия 10 сек
    async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10
    ) as ws:
        print(f"✅ WS подключен: {symbol} {tf}")

        while True:
            try:
                message = await ws.recv()
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
                    await callback(candle)

            except websockets.exceptions.ConnectionClosed:
                print(f"⚠️ WS закрыт: {symbol} {tf}, переподключение...")
                break
            except Exception as e:
                print(f"❌ WS ошибка: {e}")
                await asyncio.sleep(2)
                break