from django.utils import timezone
import os

_superuser_created = False
_superuser_error = None


class OnlineUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        global _superuser_created, _superuser_error
        response = self.get_response(request)

        if request.user.is_authenticated:
            try:
                profile = request.user.profile
                profile.last_activity = timezone.now()
                profile.save(update_fields=['last_activity'])
            except Exception:
                pass

        # Создать суперпользователя из ENV при первом запросе (fallback для Amvera)
        if not _superuser_created:
            try:
                from django.contrib.auth.models import User
                username = os.getenv('ADMIN_USERNAME')
                password = os.getenv('ADMIN_PASSWORD')
                email = os.getenv('ADMIN_EMAIL')

                if not username or not password:
                    _superuser_error = 'ADMIN_USERNAME or ADMIN_PASSWORD not set in env'
                    _superuser_created = True
                    return response

                if User.objects.filter(username=username).exists():
                    _superuser_error = f"User '{username}' already exists"
                    _superuser_created = True
                    return response

                User.objects.create_superuser(username, email or f'{username}@localhost', password)
                print(f"Superuser '{username}' created successfully")
                _superuser_created = True
            except Exception as e:
                _superuser_error = str(e)
                # Don't set _superuser_created=True so it retries next request
                print(f"Failed to create superuser: {e}")

        return response


def get_superuser_status():
    """Return diagnostic info about superuser creation"""
    from django.contrib.auth.models import User
    username = os.getenv('ADMIN_USERNAME')
    return {
        'env_username': username or '(not set)',
        'env_password_set': bool(os.getenv('ADMIN_PASSWORD')),
        'env_email': os.getenv('ADMIN_EMAIL') or '(not set)',
        'superuser_exists': User.objects.filter(username=username).exists() if username else False,
        'total_users': User.objects.count(),
        'superusers': list(User.objects.filter(is_superuser=True).values_list('username', flat=True)),
        'last_error': _superuser_error,
    }
