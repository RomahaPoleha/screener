from django.apps import AppConfig
import threading
import asyncio


class ScreenerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'screener'

    def ready(self):
        # 1. Запуск NATR (остается как был)
        try:
            from .natr_updater import start_natr_updater
            start_natr_updater()
        except Exception as e:
            print(f"⚠️ NATR updater не запущен: {e}")

        # 2. Запуск НОВОГО асинхронного Scalp Monitor
        try:
            from .scalp_monitor import start_scalp_monitor

            def run_async_scalp_monitor():
                """Запускает asyncio-монитор в отдельном потоке"""
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    start_scalp_monitor()
                    loop.run_forever()
                except Exception as e:
                    print(f"❌ Ошибка в потоке Scalp Monitor: {e}")

            thread = threading.Thread(
                target=run_async_scalp_monitor,
                name='Async-Scalp-Monitor',
                daemon=True
            )
            thread.start()
            print("✅ Async Scalp Monitor запущен в фоновом потоке")

        except Exception as e:
            print(f"⚠️ Scalp monitor не запущен: {e}")