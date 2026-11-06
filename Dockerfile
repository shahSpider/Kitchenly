# syntax=docker/dockerfile:1
FROM python:3.13-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements/base.txt requirements/base.txt
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -r requirements/base.txt

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.prod
RUN useradd --system --uid 10001 --create-home app
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app . .
# Static files need settings to import; these throwaway values are not used at runtime.
RUN DJANGO_SECRET_KEY=build-only-build-only-build-only-build-only-0000 \
    FIELD_ENCRYPTION_KEYS=mO9m4o5dJbS2k1bUuN4m0v8p5r3Zq7c9eXy1A2b3C4E= \
    DATABASE_URL=postgres://x:x@localhost/x \
    python manage.py collectstatic --noinput && chown -R app:app /app/staticfiles
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status==200 else 1)" || exit 1
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["web"]
