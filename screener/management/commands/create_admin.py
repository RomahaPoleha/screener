from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
import os


class Command(BaseCommand):
    help = 'Create superuser from env vars'

    def handle(self, *args, **options):
        username = os.getenv('ADMIN_USERNAME')
        password = os.getenv('ADMIN_PASSWORD')
        email = os.getenv('ADMIN_EMAIL', f'{username}@localhost')

        if not username or not password:
            self.stdout.write('ADMIN_USERNAME/ADMIN_PASSWORD not set, skipping')
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(f'User {username} already exists')
            return

        User.objects.create_superuser(username, email, password)
        self.stdout.write(f'Superuser {username} created')