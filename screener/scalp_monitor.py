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
        # from .bitget_monitor_async import start_bitget_async_monitor
        from .bybit_monitor_async import start_bybit_async_monitor
        from .okx_monitor_async import start_okx_async_monitor
        # from .bybit_monitor import start_bybit_monitor, start_bybit_spot_monitor
        # from .okx_monitor import start_okx_monitor, start_okx_spot_monitor
        # from .gate_monitor import start_gate_monitor, start_gate_spot_monitor
        # from .mexc_monitor import start_mexc_monitor, start_mexc_spot_monitor
        # from .bitget_monitor import start_bitget_monitor, start_bitget_spot_monitor

        # Запуск Binance Monitor
        binance_thread = threading.Thread(
            target=lambda: start_binance_monitor(log),
            name='Binance-Monitor',
            daemon=True
        )
        binance_thread.start()
        log("✅ Binance Monitor поток запущен")

        # # Запуск MEXC Futures Monitor
        # threading.Thread(
        #     target=lambda: start_mexc_monitor(log),
        #     name='MEXC-Futures-Monitor',
        #     daemon=True
        # ).start()
        # log("✅ MEXC Futures Monitor поток запущен")
        #
        # # Запуск MEXC Spot Monitor
        # threading.Thread(
        #     target=lambda: start_mexc_spot_monitor(log),
        #     name='MEXC-Spot-Monitor',
        #     daemon=True
        # ).start()
        # log("✅ MEXC Spot Monitor поток запущен")
        #
        # # Запуск Gate Futures Monitor
        # gate_futures_thread = threading.Thread(
        #     target=lambda: start_gate_monitor(log),
        #     name='Gate-Futures-Monitor',
        #     daemon=True
        # )
        # gate_futures_thread.start()
        # log("✅ Gate Futures Monitor поток запущен")
        #
        # # Запуск Gate Spot Monitor
        # gate_spot_thread = threading.Thread(
        #     target=lambda: start_gate_spot_monitor(log),
        #     name='Gate-Spot-Monitor',
        #     daemon=True
        # )
        # gate_spot_thread.start()
        # log("✅ Gate Spot Monitor поток запущен")
        #
        # # Запуск OKX Futures Monitor
        # okx_thread = threading.Thread(
        #     target=lambda: start_okx_monitor(log),
        #     name='OKX-Futures-Monitor',
        #     daemon=True
        # )
        # okx_thread.start()
        # log("✅ OKX Futures Monitor поток запущен")
        #
        # # Запуск OKX Spot Monitor
        # okx_spot_thread = threading.Thread(
        #     target=lambda: start_okx_spot_monitor(log),
        #     name='OKX-Spot-Monitor',
        #     daemon=True
        # )
        # okx_spot_thread.start()
        # log("✅ OKX Spot Monitor поток запущен")
        #
        # # Запуск Bybit Futures Monitor
        # bybit_thread = threading.Thread(
        #     target=lambda: start_bybit_monitor(log),
        #     name='Bybit-Futures-Monitor',
        #     daemon=True
        # )
        # bybit_thread.start()
        # log("✅ Bybit Futures Monitor поток запущен")
        #
        # # Запуск Bybit Spot Monitor
        # bybit_spot_thread = threading.Thread(
        #     target=lambda: start_bybit_spot_monitor(log),
        #     name='Bybit-Spot-Monitor',
        #     daemon=True
        # )
        # bybit_spot_thread.start()
        # log("✅ Bybit Spot Monitor поток запущен")

        # # Запуск Bitget Futures Monitor
        # bitget_thread = threading.Thread(
        #     target=lambda: start_bitget_monitor(log),
        #     name='Bitget-Futures-Monitor',
        #     daemon=True
        # )
        # bitget_thread.start()
        # log("✅ Bitget Futures Monitor поток запущен")
        #
        # # Запуск Bitget Spot Monitor
        # bitget_spot_thread = threading.Thread(
        #     target=lambda: start_bitget_spot_monitor(log),
        #     name='Bitget-Spot-Monitor',
        #     daemon=True
        # )
        # bitget_spot_thread.start()
        # log("✅ Bitget Spot Monitor поток запущен")

        # bybit_async_thread = threading.Thread(
        #     target=lambda: start_bybit_async_monitor(log),
        #     name='Bybit-Async-Monitor',
        #     daemon=True
        # )
        # bybit_async_thread.start()
        # log("✅ Bybit Async Monitor поток запущен")

        # bitget_async_thread = threading.Thread(
        #     target=lambda: start_bitget_async_monitor(log),
        #     name='Bitget-Async-Monitor',
        #     daemon=True
        # )
        # bitget_async_thread.start()
        # log("✅ Bitget Async Monitor поток запущен")


        okx_async_thread = threading.Thread(
            target=lambda: start_okx_async_monitor(log),
            name='OKX-Async-Monitor',
            daemon=True
        )
        okx_async_thread.start()
        log("✅ OKX Async Monitor поток запущен")

    except Exception as e:
        log(f"❌ Ошибка запуска мониторов: {e}")
        import traceback
        log(traceback.format_exc())


