import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key-test-secret-key-test-secret-key")
os.environ.setdefault("FIELD_ENCRYPTION_KEYS", "mO9m4o5dJbS2k1bUuN4m0v8p5r3Zq7c9eXy1A2b3C4E=")
os.environ.setdefault("DATABASE_URL", "postgres://kitchenly:kitchenly@127.0.0.1:55432/kitchenly")

from .base import *  # noqa: E402,F401,F403

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CELERY_TASK_ALWAYS_EAGER = False  # tests call tasks directly; no broker needed
CELERY_BROKER_URL = "memory://"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}  # noqa: F405
MEDIA_ROOT = "/tmp/kitchenly-test-media"
MFA_ENFORCED = False
POS_ALLOW_PRIVATE_NETWORKS = True
REST_FRAMEWORK = {**REST_FRAMEWORK, "DEFAULT_THROTTLE_CLASSES": []}  # noqa: F405
LOGGING = {"version": 1, "disable_existing_loggers": False}
