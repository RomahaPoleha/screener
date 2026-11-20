from unittest.mock import patch
import smtplib
from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from django.core.cache import cache
from .models import UserProfile
from .verification_mail import send_verification, DeliveryError
from .smtp_backend import TracedSMTP


@override_settings(RATELIMIT_ENABLE=False)
class VerificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('mailtest', 'test@example.invalid', 'Test-Pass-90743', is_active=False)
        self.profile = UserProfile.objects.create(user=self.user)
        self.token = self.profile.generate_verification_token()

    @patch('screener.verification_mail.send_mail', return_value=1)
    def test_accepted(self, send):
        with self.assertLogs('screener.mail', level='INFO') as logs:
            send_verification(self.user, 'https://example.invalid/private-token')
        self.assertIn('status=accepted', '\n'.join(logs.output))
        self.assertNotIn('private-token', '\n'.join(logs.output))

    @patch('screener.verification_mail.send_mail', side_effect=smtplib.SMTPAuthenticationError(535, b'private-password'))
    def test_failure_is_redacted(self, send):
        with self.assertLogs('screener.mail', level='INFO') as logs:
            with self.assertRaises(DeliveryError):
                send_verification(self.user, 'private-token')
        self.assertNotIn('private-password', '\n'.join(logs.output))
        self.assertNotIn('private-token', '\n'.join(logs.output))
        self.assertNotIn(self.user.email, '\n'.join(logs.output))

    @patch('screener.verification_mail.send_mail', return_value=0)
    def test_zero_is_failure(self, send):
        with self.assertRaises(DeliveryError):
            send_verification(self.user, 'https://example.invalid/token')

    @patch('screener.views.send_verification', side_effect=DeliveryError('sample-reference'))
    def test_resend_failure_and_token_preserved(self, send):
        response = self.client.post('/accounts/resend-verification/', {'email': self.user.email})
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, 'sample-reference', status_code=503)
        self.assertNotContains(response, 'Мы отправили', status_code=503)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_token, self.token)
        self.assertFalse(User.objects.get(pk=self.user.pk).is_active)

    @patch('screener.views.send_verification')
    def test_cooldown_and_unknown_address(self, send):
        for address in [self.user.email, self.user.email, 'unknown@example.invalid']:
            response = self.client.post('/accounts/resend-verification/', {'email': address})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Запрос принят')
        self.assertEqual(send.call_count, 1)

    @patch('screener.views.send_verification', side_effect=DeliveryError('registration-failure'))
    def test_registration_failure(self, send):
        response = self.client.post('/accounts/register/', {
            'username': 'new-test-user', 'email': 'new@example.invalid',
            'password1': 'Random-Test-98237', 'password2': 'Random-Test-98237',
        })
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, 'registration-failure', status_code=503)
        self.assertFalse(User.objects.get(username='new-test-user').is_active)

    def test_resend_requires_post(self):
        self.assertEqual(self.client.get('/accounts/resend-verification/').status_code, 405)

    @patch('smtplib.SMTP.login', side_effect=smtplib.SMTPAuthenticationError(535, b'secret'))
    def test_stage_failure_redacted(self, login):
        from .smtp_backend import stage
        with self.assertLogs('screener.mail', level='INFO') as logs:
            with self.assertRaises(smtplib.SMTPAuthenticationError):
                stage('authenticate', login)(object(), 'user', 'password')
        text = '\n'.join(logs.output)
        self.assertIn('smtp_code=535', text)
        self.assertNotIn('secret', text)
