# backend/sitecfg/settings.py

import os
from pathlib import Path
from dotenv import load_dotenv

# === Base paths & env =========================================================
# BASE_DIR = dossier "backend"
BASE_DIR = Path(__file__).resolve().parent.parent
# .env au niveau du projet (parent de backend)
load_dotenv(BASE_DIR.parent / ".env")

# Répertoire de travail applicatif
VAR_DIR = BASE_DIR / "var"

# Sous-dossiers normalisés utilisés par l'app
VAR_SUBDIRS = {
    "incoming": VAR_DIR / "incoming",
    "quarantine": VAR_DIR / "quarantine",
    "receipts_raw": VAR_DIR / "receipts_raw",
    "logs": VAR_DIR / "logs",
    "exports": VAR_DIR / "exports",
    # optionnel : JSON intermédiaire
    "receipts_json": VAR_DIR / "receipts_json",
}

# Compat rétro avec tes constantes existantes
RECEIPTS_STORE_DIR = VAR_DIR
RECEIPTS_SUBDIRS = {
    "raw": "receipts_raw",
    "json": "receipts_json",
    "logs": "logs",
    "exports": "exports",
}

# Création silencieuse des dossiers var/* (idempotent)
for p in VAR_SUBDIRS.values():
    p.mkdir(parents=True, exist_ok=True)

# === Core security/debug ======================================================
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret-key")
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() in {"1", "true", "yes"}
# Autoriser tout en dev, sinon lire depuis l'env (séparateur ",")
if DEBUG:
    ALLOWED_HOSTS = ["*"]
else:
    ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]

# Optionnel : si tu es derrière un proxy/ingress HTTPS en prod
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# === Applications =============================================================
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Apps projet
    "sitecfg.apps.SitecfgConfig",   # <- important pour ready() et checks
    "ocr",
    "ops",

    # Postgres helpers
    "django.contrib.postgres",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "sitecfg.urls"
WSGI_APPLICATION = "sitecfg.wsgi.application"

# === Templates ================================================================
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            # tes templates globaux : backend/templates/...
            BASE_DIR / "templates",
            # pas nécessaire si APP_DIRS=True, mais tolérant :
            BASE_DIR / "ocr" / "templates",
            BASE_DIR / "ops" / "templates",
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.static",
            ],
        },
    },
]

# === Database (PostgreSQL only) ==============================================
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.getenv("PGHOST", "localhost"),
        "PORT": os.getenv("PGPORT", "5432"),
        "NAME": os.getenv("POSTGRES_DB", "app"),
        "USER": os.getenv("POSTGRES_USER", "app"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "app"),
        "CONN_MAX_AGE": int(os.getenv("PG_CONN_MAX_AGE", "60")),  # keepalive
        "OPTIONS": {
            # Schéma prioritaire "ocr" + public (si tu utilises des schémas)
            "options": "-c search_path=ocr,public",
        },
    }
}

# === Auth password validators ================================================
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# === I18N / TZ ================================================================
LANGUAGE_CODE = "en-us"       # tu peux passer à "fr-fr" si tu préfères
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

# === Static & Media ===========================================================
STATIC_URL = "/static/"
STATIC_ROOT = VAR_DIR / "staticfiles"         # collectstatic en prod
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

MEDIA_URL = "/media/"
MEDIA_ROOT = VAR_DIR / "media"

# === Primary key type par défaut =============================================
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# === Logging =================================================================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "[{levelname}] {asctime} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "file_django": {
            "class": "logging.FileHandler",
            "filename": str(VAR_SUBDIRS["logs"] / "django.log"),
            "formatter": "simple",
        },
        "file_ops": {
            "class": "logging.FileHandler",
            "filename": str(VAR_SUBDIRS["logs"] / "ops.log"),
            "formatter": "simple",
        },
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {
        "handlers": ["console", "file_django"],
        "level": "INFO",
    },
    "loggers": {
        # Logger dédié aux jobs ops.* (management commands)
        "ops": {
            "handlers": ["file_ops", "console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
