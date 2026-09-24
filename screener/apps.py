import os
import threading
from django.apps import AppConfig


class ScreenerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'screener'

    def ready(self):
        # 1. ЗАЩИТА ОТ ДВОЙНОГО ЗАПУСКА в `python manage.py runserver`
        # Если это процесс авто-релоадера, мы просто выходим.
        # (В продакшене с Gunicorn переменной RUN_MAIN не будет, и код выполнится нормально)
        if os.environ.get('RUN_MAIN') != 'true':
            return

        print("🚀 Инициализация фоновых задач Screener...")

        # 2. Запуск NATR updater
        try:
            from .natr_updater import start_natr_updater
            threading.Thread(target=start_natr_updater, daemon=True, name='NATR-Updater').start()
            print("✅ NATR updater запущен в фоне")
        except Exception as e:
            print(f"⚠️ NATR updater не запущен: {e}")

        # 3. Запуск асинхронных мониторов
        monitors_to_start = [
            ('Binance', 'binance_monitor_async', 'start_binance_async_monitor'),
            ('Bybit', 'bybit_monitor_async', 'start_bybit_async_monitor'),
            ('OKX', 'okx_monitor_async', 'start_okx_async_monitor'),
            ('Gate', 'gate_monitor_async', 'start_gate_async_monitor'),
            ('MEXC', 'mexc_monitor_async', 'start_mexc_async_monitor'),
            ('Bitget', 'bitget_monitor_async', 'start_bitget_async_monitor'),
        ]

        for name, module_name, func_name in monitors_to_start:
            try:
                # Динамический импорт модуля и функции
                module = __import__(f'screener.{module_name}', fromlist=[func_name])
                func = getattr(module, func_name)

                # Запускаем в daemon-потоке
                threading.Thread(target=func, daemon=True, name=f"Monitor_{name}").start()
                print(f"✅ {name} async monitor запущен в фоне")
            except Exception as e:
                print(f"⚠️ {name} monitor не запущен: {e}")

        # 4. Запуск Candles Warmer (🔧 ИСПРАВЛЕНО: теперь тоже в отдельном потоке!)
        try:
            from .candles_warmer import start_candles_warmer
            threading.Thread(target=start_candles_warmer, daemon=True, name='Candles-Warmer').start()
            print("✅ Candles warmer запущен в фоне")
        except Exception as e:
            print(f"⚠️ Candles warmer не запущен: {e}")

        print("🎉 Все фоновые задачи успешно инициализированы!")