#!/usr/bin/env python
import ccxt
import time

print("=== Тест лимита свечей Binance Futures для 1m ===")

# Создаем exchange объект
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'},
    'timeout': 10000
})

print("1. Тестируем лимит 4320...")
try:
    ohlcv = exchange.fetch_ohlcv('BTC/USDT:USDT', '1m', limit=4320)
    print(f"   Получено свечей: {len(ohlcv)}")
    if len(ohlcv) > 0:
        # Вычислим период
        first_ts = ohlcv[0][0] / 1000  # секунды
        last_ts = ohlcv[-1][0] / 1000
        days = (last_ts - first_ts) / 86400
        print(f"   Период: {days:.1f} дней")
        print(f"   Диапазон дат:")
        import datetime
        first_dt = datetime.datetime.fromtimestamp(first_ts)
        last_dt = datetime.datetime.fromtimestamp(last_ts)
        print(f"     От: {first_dt}")
        print(f"     До: {last_dt}")
except Exception as e:
    print(f"   Ошибка: {e}")

print("\n2. Тестируем лимит 1440 (старый)...")
try:
    ohlcv = exchange.fetch_ohlcv('BTC/USDT:USDT', '1m', limit=1440)
    print(f"   Получено свечей: {len(ohlcv)}")
    if len(ohlcv) > 0:
        first_ts = ohlcv[0][0] / 1000
        last_ts = ohlcv[-1][0] / 1000
        days = (last_ts - first_ts) / 86400
        print(f"   Период: {days:.1f} дней")
except Exception as e:
    print(f"   Ошибка: {e}")

print("\n3. Тестируем лимит 1000...")
try:
    ohlcv = exchange.fetch_ohlcv('BTC/USDT:USDT', '1m', limit=1000)
    print(f"   Получено свечей: {len(ohlcv)}")
except Exception as e:
    print(f"   Ошибка: {e}")

print("\n=== Тест завершен ===")