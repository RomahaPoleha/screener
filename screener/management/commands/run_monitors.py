import os
import signal
import threading
import time

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Run background monitors only (for separate worker process)'

    def handle(self, *args, **options):
        self.stdout.write('Starting monitors...')

        try:
            from screener.natr_updater import start_natr_updater
            start_natr_updater()
            self.stdout.write('NATR updater started')
        except Exception as e:
            self.stderr.write(f'NATR updater not started: {e}')

        try:
            from screener.scalp_monitor import start_scalp_monitor
            start_scalp_monitor()
            self.stdout.write('Scalp monitors started')
        except Exception as e:
            self.stderr.write(f'Scalp monitors not started: {e}')

        try:
            if os.getenv('ENABLE_IMPULSE', '1') == '1':
                from screener.impulse_monitor import start_impulse_monitor
                impulse_thread = threading.Thread(
                    target=start_impulse_monitor,
                    name='Impulse-Monitor',
                    daemon=True,
                )
                impulse_thread.start()
                self.stdout.write('Impulse monitor started')
            else:
                self.stdout.write('Impulse monitor disabled (ENABLE_IMPULSE=0)')
        except Exception as e:
            self.stderr.write(f'Impulse monitor not started: {e}')

        self.stdout.write('Monitors are running. Waiting for shutdown signal...')

        stop_event = threading.Event()

        def _stop(signum, frame):
            stop_event.set()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        while not stop_event.is_set():
            stop_event.wait(60)

        self.stdout.write('Shutdown signal received, stopping...')
