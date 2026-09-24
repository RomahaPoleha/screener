"""
Scalp Monitor — главный файл запуска мониторов
ОПТИМИЗИРОВАНО: защита от повтора, auto-restart.
"""
import threading
from logging.handlers import RotatingFileHandler
import os
import time

# Настройка логов
LOG_DIR = '/app/data'
LOG_FILE = os.path.join(LOG_DIR, 'scalp_monitor.log')
os.makedirs(LOG_DIR, exist_ok=True)

_scalp_logger = __import__('logging').getLogger('scalp_monitor')
_scalp_logger.setLevel(__import__('logging').INFO)

_rotating_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8'
)
_rotating_handler.setFormatter(__import__('logging').Formatter('%(asctime)s - %(message)s'))
_scalp_logger.addHandler(_rotating_handler)

_console_handler = __import__('logging').StreamHandler()
_console_handler.setFormatter(__import__('logging').Formatter('%(message)s'))
_scalp_logger.addHandler(_console_handler)


def log(msg):
    _scalp_logger.info(msg)


# ✅ ИСПРАВЛЕНО: защита от повторного запуска
_started = False
_start_lock = threading.Lock()


def _run_with_restart(name, start_fn, log_fn):
    """Обёртка с auto-restart при падении"""
    while True:
        try:
            log_fn(f"🚀 Запуск {name}...")
            start_fn(log_fn)
        except Exception as e:
            log_fn(f"❌ {name} упал: {e}")
            import traceback
            log_fn(traceback.format_exc())

        log_fn(f"🔄 {name}: перезапуск через 10 секунд...")
        time.sleep(10)


def start_scalp_monitor():
    """Запуск всех мониторов"""
    global _started

    # ✅ Защита от повторного запуска
    with _start_lock:
        if _started:
            log("⚠️ start_scalp_monitor() уже вызван, пропускаем")
            return
        _started = True

    log("🔧 Вызов start_scalp_monitor()...")

    try:
        from .binance_monitor_async import start_binance_async_monitor
        from .gate_monitor_async import start_gate_async_monitor
        from .bitget_monitor_async import start_bitget_async_monitor
        from .bybit_monitor_async import start_bybit_async_monitor
        from .okx_monitor_async import start_okx_async_monitor

        monitors = [
            ('Bybit-Async-Monitor', start_bybit_async_monitor),
            ('Bitget-Async-Monitor', start_bitget_async_monitor),
            ('OKX-Async-Monitor', start_okx_async_monitor),
            ('Gate-Async-Monitor', start_gate_async_monitor),
            ('Binance-Async-Monitor', start_binance_async_monitor),
        ]

        for name, start_fn in monitors:
            thread = threading.Thread(
                target=lambda n=name, fn=start_fn: _run_with_restart(n, fn, log),
                name=name,
                daemon=True
            )
            thread.start()
            log(f"✅ {name} поток запущен (с auto-restart)")

    except Exception as e:
        log(f"❌ Ошибка запуска мониторов: {e}")
        import traceback
        log(traceback.format_exc())
        # ✅ Сбрасываем флаг чтобы можно было попробовать снова
        with _start_lock:
            _started = False
