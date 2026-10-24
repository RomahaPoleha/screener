from django.utils import timezone
import os

_superuser_created = False


class OnlineUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        global _superuser_created
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
                    _superuser_created = True
                    return response

                if User.objects.filter(username=username).exists():
                    _superuser_created = True
                    return response

                User.objects.create_superuser(username, email or f'{username}@localhost', password)
                print(f"Superuser '{username}' created")
                _superuser_created = True
            except Exception as e:
                print(f"Failed to create superuser: {e}")

        return response
