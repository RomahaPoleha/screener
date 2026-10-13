#!/bin/sh
set -e
cd "$(dirname "$0")"
python manage.py migrate --noinput
python manage.py collectstatic --noinput
exec daphne -b 0.0.0.0 -p 8000 config.asgi:application
