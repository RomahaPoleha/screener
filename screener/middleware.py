from django.utils import timezone

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
            _superuser_created = True
            try:
                import os
                from django.contrib.auth.models import User
                username = os.getenv('ADMIN_USERNAME')
                password = os.getenv('ADMIN_PASSWORD')
                email = os.getenv('ADMIN_EMAIL')
                if username and password and not User.objects.filter(username=username).exists():
                    User.objects.create_superuser(username, email or f'{username}@localhost', password)
                    print(f"Superuser '{username}' created (ADMIN_USERNAME/ADMIN_PASSWORD)")
            except Exception as e:
                print(f"Failed to create superuser: {e}")

        return response
