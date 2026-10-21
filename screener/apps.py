from django.apps import AppConfig
import os
import atexit
import threading

# Глобальные флаги для graceful shutdown
_stop_event = threading.Event()
_monitor_threads = []

def _stop_monitors():
    print("🛑 Stopping monitors...")
    _stop_event.set()
    os.environ['MONITORS_STOP'] = '1'
    for t in _monitor_threads:
        t.join(timeout=10)
    print("✅ Monitors stopped")

atexit.register(_stop_monitors)


class ScreenerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'screener'

    def ready(self):
        # Мониторы отключены по умолчанию. Включить: ENABLE_MONITORS=1
        if os.getenv('ENABLE_MONITORS', '0') == '1':
            try:
                from .natr_updater import start_natr_updater
                start_natr_updater()
            except Exception as e:
                print(f"NATR updater not started: {e}")

            try:
                from .scalp_monitor import start_scalp_monitor
                start_scalp_monitor()
            except Exception as e:
                print(f"Scalp monitor not started: {e}")

            try:
                from .impulse_monitor import start_impulse_monitor
                impulse_thread = threading.Thread(
                    target=start_impulse_monitor,
                    name='Impulse-Monitor',
                    daemon=True
                )
                _monitor_threads.append(impulse_thread)
                impulse_thread.start()

            except Exception as e:
                print(f"Impulse monitor not started: {e}")
        else:
            print("Monitors disabled (set ENABLE_MONITORS=1 to enable)")