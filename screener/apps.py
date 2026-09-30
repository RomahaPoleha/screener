from django.apps import AppConfig


class ScreenerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'screener'

    def ready(self):
        try:
            from .natr_updater import start_natr_updater
            start_natr_updater()
        except Exception as e:
            print(f"⚠️ NATR updater не запущен: {e}")

        try:
            from .scalp_monitor import start_scalp_monitor
            start_scalp_monitor()
        except Exception as e:
            print(f"️ Scalp monitor не запущен: {e}")

        try:
            import threading
            from .impulse_monitor import start_impulse_monitor
            impulse_thread = threading.Thread(
                target=start_impulse_monitor,
                name='Impulse-Monitor',
                daemon=True
            )
            impulse_thread.start()
            print("✅ Impulse Monitor поток запущен")
        except Exception as e:
            print(f"⚠️ Impulse monitor не запущен: {e}")