from django.utils import timezone


class OnlineUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.user.is_authenticated:
            try:
                profile = request.user.profile
                profile.last_activity = timezone.now()
                profile.save(update_fields=['last_activity'])
            except Exception:
                pass

        return response
