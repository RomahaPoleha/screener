from django.apps import AppConfig
import os


class ScreenerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'screener'

    def ready(self):
        # Пропускаем reloader-процесс при runserver (RUN_MAIN != 'true' и DJANGO_AUTORELOAD == 'true')
        # На продакшене (gunicorn) DJANGO_AUTORELOAD не установлен — запускаем
        if os.environ.get('RUN_MAIN') != 'true' and os.environ.get('DJANGO_AUTORELOAD') == 'true':
            return

        try:
            from .natr_updater import start_natr_updater
            start_natr_updater()
        except Exception as e:
            print(f"[WARN] NATR updater not started: {e}")

        try:
            from .scalp_monitor import start_scalp_monitor
            start_scalp_monitor()
        except Exception as e:
            print(f"[WARN] Scalp monitor not started: {e}")

        try:
            import threading
            from .impulse_monitor import start_impulse_monitor
            impulse_thread = threading.Thread(
                target=start_impulse_monitor,
                name='Impulse-Monitor',
                daemon=True
            )
            impulse_thread.start()
            print("[OK] Impulse Monitor thread started")
        except Exception as e:
            print(f"[WARN] Impulse monitor not started: {e}")