from django.apps import AppConfig


class ScreenerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'screener'

    def ready(self):
        try:
            from .candle_updater import start_candle_updater
            start_candle_updater()
        except Exception as e:
            print(f"⚠️ Candle updater не запущен: {e}")

        try:
            from .natr_updater import start_natr_updater
            start_natr_updater()
        except Exception as e:
            print(f"⚠️ NATR updater не запущен: {e}")