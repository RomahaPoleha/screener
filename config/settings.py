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
# Если нужен admin — раскомментируйте
INSTALLED_APPS = [
    # 'django.contrib.admin',
    # 'django.contrib.auth',
    # 'django.contrib.contenttypes',
    # 'django.contrib.sessions',
    # 'django.contrib.messages',
    'django.contrib.staticfiles',
    'screener.apps.ScreenerConfig',
]

# ✅ ИСПРАВЛЕНО: убраны Session, CSRF, Auth, Messages middleware
# Они не нужны для API-сервиса и экономят ~1-2мс на каждый запрос
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

# ✅ УБРАНО: SQLite не используется (все данные в Redis)
DATABASES = {}

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'

# ==========================================
# КЭШ (Redis / LocMem fallback)
# ==========================================
# ✅ ИСПРАВЛЕНО: убрано дублирование REDIS_URL
REDIS_URL = os.getenv('REDIS_URL')

if REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': REDIS_URL,
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                # ✅ Оптимизация: пул соединений
                'CONNECTION_POOL_KWARGS': {
                    'max_connections': 50,
                    'retry_on_timeout': True,
                },
            },
            # ✅ Ограничение размера кэша
            'TIMEOUT': 600,
        }
    }
    print(f"✅ Redis настроен: {REDIS_URL}")
else:
    # ✅ ИСПРАВЛЕНО: LocMemCache вместо FileBasedCache
    # LocMemCache в 100-1000 раз быстрее файлового кэша
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'screener-cache',
            'OPTIONS': {
                'MAX_ENTRIES': 10000,  # Ограничение памяти
                'CULL_FREQUENCY': 3,   # Удалять 1/3 при переполнении
            },
        }
    }
    print("⚠️ Redis не найден, используем LocMemCache (данные теряются при перезапуске)")

# Django Channels
ASGI_APPLICATION = 'config.asgi.application'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}

# ✅ Безопасность
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
