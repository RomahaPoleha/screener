import logging
import uuid
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from .smtp_backend import mail_reference

log = logging.getLogger('screener.mail')


class DeliveryError(Exception):
    def __init__(self, reference):
        self.reference = reference
        super().__init__('Verification email submission failed')


def send_verification(user, verify_url):
    reference = uuid.uuid4().hex[:12]
    context = mail_reference.set(reference)
    step = 'render'
    try:
        log.info('mail ref=%s stage=prepare host=%s port=%s tls=%s ssl=%s timeout=%s',
                 reference, settings.EMAIL_HOST, settings.EMAIL_PORT,
                 settings.EMAIL_USE_TLS, settings.EMAIL_USE_SSL, settings.EMAIL_TIMEOUT)
        html = render_to_string('screener/email/verify_email.html', {
            'username': user.username, 'verify_url': verify_url,
        })
        step = 'send'
        count = send_mail(
            'Подтверждение email — ReconAve',
            f'Привет, {user.username}!\n\nПодтверди email: {verify_url}\n\nСсылка действительна 24 часа.',
            settings.DEFAULT_FROM_EMAIL, [user.email], html_message=html,
            fail_silently=False,
        )
        if count != 1:
            raise RuntimeError('No message accepted')
        log.info('mail ref=%s stage=submission status=accepted', reference)
    except Exception as error:
        # Never log str(error), exc_info, recipient, password, or verification URL.
        log.error('mail ref=%s stage=%s status=failed type=%s',
                  reference, step, type(error).__name__)
        raise DeliveryError(reference) from None
    finally:
        mail_reference.reset(context)
