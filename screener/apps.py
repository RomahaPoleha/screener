import threading
from django.apps import AppConfig

class ScreenerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'screener'

    def ready(self):
        # Запускаем только если это основной процесс (защита от множественного запуска в Gunicorn)
        import os
        if os.environ.get('RUN_MAIN', None) != 'true' and os.environ.get('DJANGO_RUN_MAIN', None) != 'true':
            # В продакшене с Gunicorn эта логика может требовать дополнительной настройки,
            # но для runserver и простых setup это работает надежно.
            pass

        # 1. Запуск NATR updater (если он синхронный или имеет свой start_)
        try:
            from .natr_updater import start_natr_updater
            threading.Thread(target=start_natr_updater, daemon=True).start()
        except Exception as e:
            print(f"⚠️ NATR updater не запущен: {e}")

        # 2. Запуск асинхронных мониторов в отдельных демо-потоках
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
                module = __import__(f'screener.{module_name}', fromlist=[func_name])
                func = getattr(module, func_name)
                # Запускаем в daemon-потоке, чтобы он не мешал закрытию Django
                threading.Thread(target=func, daemon=True, name=f"Monitor_{name}").start()
                print(f"✅ {name} async monitor запущен в фоне")
            except Exception as e:
                print(f"⚠️ {name} monitor не запущен: {e}")
        try:
            from .candles_warmer import start_candles_warmer
            start_candles_warmer()
        except Exception as e:
            print(f"⚠️ Candles warmer не запущен: {e}")
