"""
Scalp Monitor — главный файл запуска мониторов
"""
import threading
from logging.handlers import RotatingFileHandler
import os

# Настройка логов
LOG_DIR = '/app/data'
LOG_FILE = os.path.join(LOG_DIR, 'scalp_monitor.log')

os.makedirs(LOG_DIR, exist_ok=True)

_scalp_logger = __import__('logging').getLogger('scalp_monitor')
_scalp_logger.setLevel(__import__('logging').INFO)

_rotating_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding='utf-8'
)
_rotating_handler.setFormatter(__import__('logging').Formatter('%(asctime)s - %(message)s'))
_scalp_logger.addHandler(_rotating_handler)

_console_handler = __import__('logging').StreamHandler()
_console_handler.setFormatter(__import__('logging').Formatter('%(message)s'))
_scalp_logger.addHandler(_console_handler)


def log(msg):
    _scalp_logger.info(msg)


def start_scalp_monitor():
    """Запуск всех мониторов"""
    log("🔧 Вызов start_scalp_monitor()...")

    try:
        from .binance_monitor import start_binance_monitor
        # from .bybit_monitor import start_bybit_monitor, start_bybit_spot_monitor
        # from .okx_monitor import start_okx_monitor, start_okx_spot_monitor
        from .gate_monitor import start_gate_monitor
        # Запуск Binance Monitor
        binance_thread = threading.Thread(
            target=lambda: start_binance_monitor(log),
            name='Binance-Monitor',
            daemon=True
        )
        binance_thread.start()
        log("✅ Binance Monitor поток запущен")

        # Gate Futures
        threading.Thread(
            target=lambda: start_gate_monitor(log),
            name='Gate-Futures-Monitor',
            daemon=True
        ).start()
        log("✅ Gate Futures Monitor поток запущен")

    except Exception as e:
        log(f"❌ Ошибка запуска мониторов: {e}")

        # # Запуск OKX Futures Monitor (временно вместо Bybit)
        # okx_thread = threading.Thread(
        #     target=lambda: start_okx_monitor(log),
        #     name='OKX-Futures-Monitor',
        #     daemon=True
        # )
        # okx_thread.start()
        # log("✅ OKX Futures Monitor поток запущен")

        # # Запуск OKX Spot Monitor
        # okx_spot_thread = threading.Thread(
        #     target=lambda: start_okx_spot_monitor(log),
        #     name='OKX-Spot-Monitor',
        #     daemon=True
        # )
        # okx_spot_thread.start()
        # log("✅ OKX Spot Monitor поток запущен")

        # # Запуск Bybit Futures Monitor (ВРЕМЕННО ОТКЛЮЧЁН)
        # bybit_thread = threading.Thread(
        #     target=lambda: start_bybit_monitor(log),
        #     name='Bybit-Futures-Monitor',
        #     daemon=True
        # )
        # bybit_thread.start()
        # log("✅ Bybit Futures Monitor поток запущен")

        # # Запуск Bybit Spot Monitor (ВРЕМЕННО ОТКЛЮЧЁН)
        # bybit_spot_thread = threading.Thread(
        #     target=lambda: start_bybit_spot_monitor(log),
        #     name='Bybit-Spot-Monitor',
        #     daemon=True
        # )
        # bybit_spot_thread.start()
        # log("✅ Bybit Spot Monitor поток запущен")

    except Exception as e:
        log(f"❌ Ошибка запуска мониторов: {e}")
        import traceback
        log(traceback.format_exc())