from django.core.cache import cache
from django.http import JsonResponse
import time
import re


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


class OnlineCacheMiddleware:
    """Отмечает онлайн-пользователей в кэше (без обращения к БД).
    Хранит словарь {user_id: timestamp} в одном ключе online_users.
    Работает с любым бэкендом кэша (Redis, file, db).
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            now = time.time()
            online = cache.get('online_users', {})
            online[request.user.id] = now
            # Удаляем устаревшие записи (старше 5 минут)
            online = {uid: ts for uid, ts in online.items() if now - ts < 300}
            cache.set('online_users', online, 300)
        response = self.get_response(request)
        return response
