from django.apps import AppConfig
import os
import atexit
import threading

# Глобальные флаги для graceful shutdown
_stop_event = threading.Event()
_monitor_threads = []


def _create_superuser_from_env():
    """Создаёт суперпользователя из переменных окружения при старте (для Amvera без shell)"""
    from django.contrib.auth.models import User

    username = os.getenv('ADMIN_USERNAME')
    email = os.getenv('ADMIN_EMAIL')
    password = os.getenv('ADMIN_PASSWORD')

    if not username or not password:
        return

    if User.objects.filter(username=username).exists():
        return

    try:
        User.objects.create_superuser(username, email or f'{username}@localhost', password)
        print(f"✅ Суперпользователь '{username}' создан (ADMIN_USERNAME/ADMIN_PASSWORD)")
    except Exception as e:
        print(f"❌ Не удалось создать суперпользователя '{username}': {e}")


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
        # Создать суперпользователя из ENV (для деплоя без shell)
        _create_superuser_from_env()

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