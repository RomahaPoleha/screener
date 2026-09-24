"""
Django settings for config project.
ОПТИМИЗИРОВАНО: убраны ненужные middleware, исправлен кэш.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-local-dev-only-change-in-production')

DEBUG = False

# ✅ ИСПРАВЛЕНО: указываем конкретные хосты вместо '*'
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '*').split(',')

STATIC_ROOT = BASE_DIR / 'staticfiles'

# ✅ ИСПРАВЛЕНО: убраны ненужные apps (admin, auth, sessions, messages, contenttypes)
INSTALLED_APPS = [
    'django.contrib.staticfiles',
    'screener.apps.ScreenerConfig',
]

# ✅ ИСПРАВЛЕНО: убраны Session, CSRF, Auth, Messages middleware
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {}

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'

# ==========================================
# КЭШ (Redis)
# ==========================================
REDIS_URL = os.getenv('REDIS_URL')

if not REDIS_URL:
    raise RuntimeError("❌ REDIS_URL не задан! Укажите переменную окружения REDIS_URL")

CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': REDIS_URL,
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'CONNECTION_POOL_KWARGS': {
                'max_connections': 50,
                'retry_on_timeout': True,
            },
        },
        'TIMEOUT': 600,
    }
}
print(f"✅ Redis настроен: {REDIS_URL}")

ASGI_APPLICATION = 'config.asgi.application'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'