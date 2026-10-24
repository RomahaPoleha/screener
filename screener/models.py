from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    email_verified = models.BooleanField(default=False)
    verification_token = models.CharField(max_length=200, blank=True)
    token_created = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_activity = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Profile({self.user.username})"

    def generate_verification_token(self):
        from django.core import signing
        token = signing.dumps(self.user.pk, salt='email-verify')
        self.verification_token = token
        self.token_created = timezone.now()
        self.save(update_fields=['verification_token', 'token_created'])
        return token

    @staticmethod
    def verify_token(token, max_age=86400):
        """Проверка токена. max_age в секундах (по умолчанию 24 часа)"""
        from django.core import signing
        try:
            uid = signing.loads(token, salt='email-verify', max_age=max_age)
            user = User.objects.get(pk=uid)
            # Проверяем, что токен совпадает с сохранённым (не использован ранее)
            profile = user.profile
            if profile.verification_token != token:
                return None  # Токен уже использован или заменён
            return user
        except (signing.BadSignature, signing.SignatureExpired, User.DoesNotExist):
            return None