from django.core.management.base import BaseCommand
from screener.scheduler import process_natr_chunk

class Command(BaseCommand):
    help = 'Обновляет NATR для Spot и Futures'

    def handle(self, *args, **kwargs):
        self.stdout.write('🔄 Запуск обновления NATR...')
        process_natr_chunk('spot')
        process_natr_chunk('future')
        self.stdout.write('✅ NATR обновлён')