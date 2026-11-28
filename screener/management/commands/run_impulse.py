import signal
import threading

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Run impulse monitor only (for separate process/application)'

    def handle(self, *args, **options):
        self.stdout.write('Starting impulse monitor...')

        try:
            from screener.impulse_monitor import start_impulse_monitor

            thread = threading.Thread(
                target=start_impulse_monitor,
                name='Impulse-Monitor',
                daemon=True,
            )
            thread.start()
            self.stdout.write('Impulse monitor started')
        except Exception as e:
            self.stderr.write(f'Impulse monitor not started: {e}')
            return

        stop_event = threading.Event()

        def _stop(signum, frame):
            stop_event.set()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        while not stop_event.is_set():
            stop_event.wait(60)

        self.stdout.write('Shutdown signal received, stopping...')
