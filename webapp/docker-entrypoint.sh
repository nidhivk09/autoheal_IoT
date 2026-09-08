#!/bin/sh
# Runs at container start (not image build time) because Django settings
# require POSTGRES_URL / DJANGO_SECRET_KEY, which are only present as real
# secrets once the container is actually running with its env injected.
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3
