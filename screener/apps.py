from django.apps import AppConfig
from .natr_updater import start_natr_updater

class ScreenerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'screener'

    def ready(self):
        # Запускаем только NATR updater
        start_natr_updater()