from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)


class ScreenerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'screener'

    def ready(self):
        try:
            from .natr_updater import start_natr_updater
            start_natr_updater()
            logger.info("✅ NATR updater запущен")
        except Exception as e:
            logger.error(f"⚠️ NATR updater не запущен: {e}")
            import traceback
            logger.error(traceback.format_exc())

        try:
            from .scalp_monitor import start_scalp_monitor
            logger.info("🔧 Вызываю start_scalp_monitor()...")
            start_scalp_monitor()
            logger.info("✅ Scalp monitor запущен")
        except Exception as e:
            logger.error(f"⚠️ Scalp monitor не запущен: {e}")
            import traceback
            logger.error(traceback.format_exc())