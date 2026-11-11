from django.utils import timezone
from django.core.cache import cache
from django.http import JsonResponse
import os
import time
import re

_superuser_created = False


def _get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


def _parse_rules(rules_str):
    rules = []
    for item in rules_str.split(','):
        item = item.strip()
        if not item:
            continue
        parts = item.split(':')
        if len(parts) != 3:
            continue
        path, limit, window = parts
        try:
            rules.append({
                'path': path.strip(),
                'limit': int(limit.strip()),
                'window': int(window.strip()),
            })
        except ValueError:
            continue
    return rules


class RateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        from django.conf import settings
        self.enable = getattr(settings, 'RATELIMIT_ENABLE', True)
        self.rules = _parse_rules(getattr(settings, 'RATELIMIT_RULES', ''))

    def __call__(self, request):
        if not self.enable:
            return self.get_response(request)

        path = request.path
        matched_rule = None
        for rule in self.rules:
            if path.startswith(rule['path']):
                matched_rule = rule
                break

        if matched_rule:
            ip = _get_client_ip(request)
            cache_key = f"ratelimit:{matched_rule['path']}:{ip}"
            now = int(time.time())
            window = matched_rule['window']
            limit = matched_rule['limit']

            # Считаем запросы в текущем окне
            window_key = f"{cache_key}:{now // window}"
            count = cache.get(window_key, 0) + 1
            cache.set(window_key, count, window)

            if count > limit:
                retry_after = window - (now % window)
                return JsonResponse(
                    {'error': 'rate limit exceeded', 'retry_after': retry_after},
                    status=429,
                )

        return self.get_response(request)


class OnlineUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        global _superuser_created

        # Гарантируем наличие профиля до рендера шаблонов
        if request.user.is_authenticated:
            try:
                from .models import UserProfile
                UserProfile.objects.get_or_create(user=request.user)
            except Exception:
                pass

        response = self.get_response(request)

        if request.user.is_authenticated:
            try:
                from .models import UserProfile
                profile = UserProfile.objects.get(user=request.user)
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
