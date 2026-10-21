import os
import requests
from django.conf import settings
from django.template.loader import render_to_string


class UniOneEmailService:
    def __init__(self):
        self.api_key = os.getenv('UNIONE_API_KEY')
        self.sender_email = os.getenv('UNIONE_SENDER_EMAIL', 'noreply@yourdomain.com')
        self.sender_name = os.getenv('UNIONE_SENDER_NAME', 'Recon&Scalp')
        self.base_url = 'https://api.unione.io/ru/transactional/api/v1/email/send'

    def is_configured(self):
        return bool(self.api_key)

    def send_email(self, to_email, subject, html_content, text_content=None):
        if not self.is_configured():
            print("⚠️ UniOne не настроен: отсутствует UNIONE_API_KEY")
            return False, "UniOne not configured"

        payload = {
            "from": f"{self.sender_name} <{self.sender_email}>",
            "to": to_email,
            "subject": subject,
            "html": html_content,
        }
        if text_content:
            payload["text"] = text_content

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(self.base_url, json=payload, headers=headers, timeout=10)
            if response.status_code == 200:
                return True, response.json()
            else:
                print(f"❌ UniOne ошибка: {response.status_code} - {response.text}")
                return False, f"UniOne error: {response.status_code}"
        except requests.RequestException as e:
            print(f"❌ UniOne ошибка соединения: {e}")
            return False, str(e)

    def send_verification_email(self, user, verify_url, request=None):
        subject = "Подтвердите ваш email — Recon&Scalp"
        
        if request:
            logo_url = request.build_absolute_uri('/api/logo/')
        else:
            logo_url = getattr(settings, 'SITE_LOGO_URL', 'https://your-domain.com/api/logo/')
        
        html_content = render_to_string('screener/email/verify_email.html', {
            'username': user.username,
            'verify_url': verify_url,
            'logo_url': logo_url,
        })
        text_content = (
            f"Привет, {user.username}!\n\n"
            f"Чтобы завершить регистрацию в Recon&Scalp, перейдите по ссылке:\n{verify_url}\n\n"
            f"Ссылка действительна 24 часа.\n"
            f"Если вы не регистрировались — просто проигнорируйте это письмо."
        )
        return self.send_email(user.email, subject, html_content, text_content)


unione_service = UniOneEmailService()