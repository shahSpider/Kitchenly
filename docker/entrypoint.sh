#!/bin/sh
# One image, several roles: web | worker | beat | migrate | <any manage.py / shell command>
set -e
case "$1" in
  web)
    exec gunicorn config.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers "${WEB_CONCURRENCY:-4}" --threads "${WEB_THREADS:-2}" --worker-class gthread \
      --timeout 30 --graceful-timeout 30 --max-requests 2000 --max-requests-jitter 200 \
      --access-logfile - --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}"
    ;;
  worker)
    # QUEUES selects which queues this worker pool serves: webhook, pull, import, push, default
    exec celery -A config worker -l "${LOG_LEVEL:-info}" -Q "${QUEUES:-webhook,pull,import,push,default}" \
      --concurrency "${CELERY_CONCURRENCY:-4}" --max-tasks-per-child 1000
    ;;
  beat)
    exec celery -A config beat -l "${LOG_LEVEL:-info}" --schedule /tmp/celerybeat-schedule
    ;;
  migrate)
    exec python manage.py migrate --noinput
    ;;
  *)
    exec "$@"
    ;;
esac
