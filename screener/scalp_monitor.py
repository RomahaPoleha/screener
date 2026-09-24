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
        from .binance_monitor_async import start_binance_async_monitor
        from .gate_monitor_async import start_gate_async_monitor
        from .bitget_monitor_async import start_bitget_async_monitor
        from .bybit_monitor_async import start_bybit_async_monitor
        from .okx_monitor_async import start_okx_async_monitor
        # from .mexc_monitor_async import start_mexc_async_monitor


        bybit_async_thread = threading.Thread(
            target=lambda: start_bybit_async_monitor(log),
            name='Bybit-Async-Monitor',
            daemon=True
        )
        bybit_async_thread.start()
        log("✅ Bybit Async Monitor поток запущен")

        bitget_async_thread = threading.Thread(
            target=lambda: start_bitget_async_monitor(log),
            name='Bitget-Async-Monitor',
            daemon=True
        )
        bitget_async_thread.start()
        log("✅ Bitget Async Monitor поток запущен")


        okx_async_thread = threading.Thread(
            target=lambda: start_okx_async_monitor(log),
            name='OKX-Async-Monitor',
            daemon=True
        )
        okx_async_thread.start()
        log("✅ OKX Async Monitor поток запущен")

        gate_async_thread = threading.Thread(
            target=lambda: start_gate_async_monitor(log),
            name='Gate-Async-Monitor',
            daemon=True
        )
        gate_async_thread.start()
        log("✅ Gate Async Monitor поток запущен")

        # mexc_async_thread = threading.Thread(
        #     target=lambda: start_mexc_async_monitor(log),
        #     name='MEXC-Async-Monitor',
        #     daemon=True
        # )
        # mexc_async_thread.start()
        # log("✅ MEXC Async Monitor поток запущен")

        binance_async_thread = threading.Thread(
            target=lambda: start_binance_async_monitor(log),
            name='Binance-Async-Monitor',
            daemon=True
        )
        binance_async_thread.start()
        log("✅ Binance Async Monitor поток запущен")

    except Exception as e:
        log(f"❌ Ошибка запуска мониторов: {e}")
        import traceback
        log(traceback.format_exc())


