from .base import *  # noqa: F401,F403

DEBUG = True
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}  # noqa: F405
CORS_ALLOW_ALL_ORIGINS = True
MFA_ENFORCED = env.bool("MFA_ENFORCED", default=False)  # noqa: F405
POS_ALLOW_PRIVATE_NETWORKS = True
