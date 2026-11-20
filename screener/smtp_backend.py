"""SMTP stages without SMTP debug output or raw exception messages."""
import logging
import smtplib
from contextvars import ContextVar
from django.core.mail.backends.smtp import EmailBackend

log = logging.getLogger('screener.mail')
mail_reference = ContextVar('mail_reference', default='untracked')


def stage(name, method):
    def call(self, *args, **kwargs):
        ref = mail_reference.get()
        log.info('mail ref=%s stage=%s status=start', ref, name)
        try:
            result = method(self, *args, **kwargs)
        except Exception as error:
            code = getattr(error, 'smtp_code', None)
            log.error('mail ref=%s stage=%s status=failed type=%s smtp_code=%s',
                      ref, name, type(error).__name__, code if isinstance(code, int) else '-')
            raise
        log.info('mail ref=%s stage=%s status=ok', ref, name)
        return result
    return call


class TraceMixin:
    connect = stage('connect', smtplib.SMTP.connect)
    ehlo = stage('ehlo', smtplib.SMTP.ehlo)
    starttls = stage('starttls', smtplib.SMTP.starttls)
    login = stage('authenticate', smtplib.SMTP.login)
    sendmail = stage('submit', smtplib.SMTP.sendmail)


class TracedSMTP(TraceMixin, smtplib.SMTP):
    pass


class TracedSMTPSSL(TraceMixin, smtplib.SMTP_SSL):
    pass


class DiagnosticEmailBackend(EmailBackend):
    @property
    def connection_class(self):
        return TracedSMTPSSL if self.use_ssl else TracedSMTP
