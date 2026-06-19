from django.apps import AppConfig
import os
import signal
import sys


class ScreenerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'screener'

    def ready(self):
        if hasattr(self, '_natr_started'):
            return
        self._natr_started = True

        print("🔥🔥 apps.py ready() ВЫЗВАН 🔥")

        try:
            from .natr_updater import start_natr_updater
            start_natr_updater()
            print("✅ NATR Updater запущен (только Futures)!")

            from .candle_updater import start_candle_updater
            start_candle_updater()
            print("✅ Candle Updater запущен!")

            def handle_shutdown(signum, frame):
                print("🛑 Получен сигнал остановки (SIGTERM/SIGINT)")
                try:
                    from .natr_updater import stop_natr_updater
                    stop_natr_updater()
                except Exception as e:
                    print(f"⚠️ Ошибка при остановке NATR: {e}")
                sys.exit(0)

            signal.signal(signal.SIGTERM, handle_shutdown)
            signal.signal(signal.SIGINT, handle_shutdown)

        except Exception as e:
            print(f"❌ Ошибка запуска: {e}")
            import traceback
            traceback.print_exc()