"""Base settings shared by every environment. Everything environment specific
comes from environment variables (12-factor); see .env.example."""
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env()
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    environ.Env.read_env(str(_env_file))

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    "apps.core",
    "apps.tenants",
    "apps.accounts",
    "apps.audit",
    "apps.menu",
    "apps.orders",
    "apps.pos",
    "apps.reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "apps.core.middleware.RequestContextMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.AdminSupportAccessMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

# --- Database ---------------------------------------------------------------
# The app MUST connect as a non-superuser that owns its tables: superusers
# bypass PostgreSQL row-level security (see docs/ARCHITECTURE.md).
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"].update(
    {
        # Every request runs in one transaction. Besides atomicity, this is what
        # makes `set_config(..., is_local => true)` (our RLS context) safe with
        # connection pools such as PgBouncer in transaction mode.
        "ATOMIC_REQUESTS": True,
        "CONN_MAX_AGE": env.int("DB_CONN_MAX_AGE", default=60),
        "CONN_HEALTH_CHECKS": True,
    }
)
_replica = env.str("REPLICA_DATABASE_URL", default="")
if _replica:
    DATABASES["replica"] = {**env.db_url_config(_replica), "CONN_MAX_AGE": 60, "TEST": {"MIRROR": "default"}}
REPORTS_DB_ALIAS = "replica" if "replica" in DATABASES else "default"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Cache / Redis ----------------------------------------------------------
REDIS_URL = env.str("REDIS_URL", default="redis://localhost:6379")
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": f"{REDIS_URL}/1",
        "KEY_PREFIX": "kitchenly",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "SOCKET_CONNECT_TIMEOUT": 2,
            "SOCKET_TIMEOUT": 2,
            # A cache outage must never take the API down.
            "IGNORE_EXCEPTIONS": True,
        },
    }
}
DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS = True

# --- Auth -------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
MFA_ENFORCED = env.bool("MFA_ENFORCED", default=True)  # super admins + org managers
MFA_ISSUER = env.str("MFA_ISSUER", default="Kitchenly")

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "SIGNING_KEY": SECRET_KEY,
}

# --- Encryption of POS credentials -----------------------------------------
# Comma separated Fernet keys. The first encrypts; all decrypt (key rotation).
FIELD_ENCRYPTION_KEYS = env.list("FIELD_ENCRYPTION_KEYS")

# --- REST framework ---------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.DefaultPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "600/min",
        "login": "10/min",
        "webhook": env.str("WEBHOOK_RATE_LIMIT", default="120/min"),
    },
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "apps.core.exceptions.exception_handler",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Kitchenly Restaurant Management Platform API",
    "DESCRIPTION": "Multi-tenant restaurant management with a unified POS integration layer.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_HEADERS = [
    "accept", "authorization", "content-type", "origin", "x-organization", "x-request-id",
]
CORS_EXPOSE_HEADERS = ["x-request-id"]

# --- POS integration --------------------------------------------------------
WEBHOOK_TIMESTAMP_TOLERANCE = env.int("WEBHOOK_TIMESTAMP_TOLERANCE", default=300)  # seconds
WEBHOOK_MAX_BODY_BYTES = env.int("WEBHOOK_MAX_BODY_BYTES", default=1_000_000)
POS_ALLOW_PRIVATE_NETWORKS = env.bool("POS_ALLOW_PRIVATE_NETWORKS", default=False)  # SSRF guard
POS_HTTP_TIMEOUT = env.int("POS_HTTP_TIMEOUT", default=15)
SYNC_EVENT_MAX_ATTEMPTS = env.int("SYNC_EVENT_MAX_ATTEMPTS", default=6)
IMPORT_MAX_BYTES = env.int("IMPORT_MAX_BYTES", default=10 * 1024 * 1024)
MENU_CACHE_TTL = env.int("MENU_CACHE_TTL", default=3600)

# --- Celery -----------------------------------------------------------------
CELERY_BROKER_URL = env.str("CELERY_BROKER_URL", default=f"{REDIS_URL}/0")
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3600}
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_DEFAULT_QUEUE = "default"
# Separate queues so a slow CSV import can never delay live webhook orders.
CELERY_TASK_ROUTES = {
    "apps.pos.tasks.process_sync_event": {"queue": "webhook"},
    "apps.pos.tasks.schedule_pulls": {"queue": "pull"},
    "apps.pos.tasks.pull_connection": {"queue": "pull"},
    "apps.pos.tasks.import_file": {"queue": "import"},
    "apps.pos.tasks.push_menu": {"queue": "push"},
    "apps.pos.tasks.reconcile_all": {"queue": "default"},
    "apps.pos.tasks.reconcile_connection": {"queue": "default"},
    "apps.pos.tasks.requeue_stuck_events": {"queue": "default"},
}
CELERY_BEAT_SCHEDULE = {
    "schedule-pulls": {"task": "apps.pos.tasks.schedule_pulls", "schedule": 60.0},
    "requeue-stuck-events": {"task": "apps.pos.tasks.requeue_stuck_events", "schedule": 300.0},
    "nightly-reconciliation": {"task": "apps.pos.tasks.reconcile_all", "schedule": timedelta(hours=1)},
}

# --- Files (CSV imports) ----------------------------------------------------
if env.str("AWS_STORAGE_BUCKET_NAME", default=""):
    AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_ENDPOINT_URL = env.str("AWS_S3_ENDPOINT_URL", default=None)
    AWS_S3_REGION_NAME = env.str("AWS_S3_REGION_NAME", default=None)
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = True
    AWS_S3_FILE_OVERWRITE = False
    _default_storage = {"BACKEND": "storages.backends.s3.S3Storage"}
else:
    MEDIA_ROOT = env.str("MEDIA_ROOT", default=str(BASE_DIR / "media"))
    _default_storage = {"BACKEND": "django.core.files.storage.FileSystemStorage"}
STORAGES = {
    "default": _default_storage,
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --- Email ------------------------------------------------------------------
EMAIL_BACKEND = env.str("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env.str("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=25)
EMAIL_HOST_USER = env.str("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env.str("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL", default="Kitchenly <no-reply@kitchenly.local>")
FRONTEND_URL = env.str("FRONTEND_URL", default="http://localhost:3000")

# --- i18n / static ----------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- Logging (JSON to stdout, collected by the platform) -------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "apps.core.logging.RequestIdFilter"}},
    "formatters": {"json": {"()": "apps.core.logging.JsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json", "filters": ["request_id"]}},
    "root": {"handlers": ["console"], "level": env.str("LOG_LEVEL", default="INFO")},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}

_sentry = env.str("SENTRY_DSN", default="")
if _sentry:
    import sentry_sdk

    sentry_sdk.init(dsn=_sentry, traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0),
                    send_default_pii=False)

# Schema-generation hints for plain APIViews; the OpenAPI output is still correct.
SILENCED_SYSTEM_CHECKS = ["drf_spectacular.W001", "drf_spectacular.W002"]
