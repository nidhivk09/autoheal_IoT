"""
Django settings for the autoheal-iot research dashboard.

All secrets and environment-specific values come from environment variables
(loaded from .env in dev via python-dotenv; from real env vars / Key Vault in
deployed environments). Nothing sensitive is hardcoded here - see
.env.example at the repo root for the full list of variables this project
uses.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent

# So `from controller.fault_injector import inject_fault` works from the
# incidents app - controller/ lives at the repo root, alongside webapp/.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise RuntimeError(
        "DJANGO_SECRET_KEY is not set. Generate one and put it in your .env file "
        "(never commit it) - see .env.example."
    )

DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True"

ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "devices",
    "incidents",
    "dashboard",
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

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database ---
# POSTGRES_URL like: postgresql://user:password@host:5432/dbname
_POSTGRES_URL = os.environ.get("POSTGRES_URL")
if not _POSTGRES_URL:
    raise RuntimeError("POSTGRES_URL is not set - see .env.example.")

import urllib.parse as _urlparse

_parsed = _urlparse.urlparse(_POSTGRES_URL)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _parsed.path.lstrip("/"),
        "USER": _urlparse.unquote(_parsed.username or ""),
        "PASSWORD": _urlparse.unquote(_parsed.password or ""),
        "HOST": _parsed.hostname,
        "PORT": _parsed.port or 5432,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
}

# --- Internal API auth (used by the Azure Function to write Incident rows) ---
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "")

# --- Fault injection controller (SSH to the Pi) ---
PI_SSH_HOST = os.environ.get("PI_SSH_HOST", "")
PI_SSH_USER = os.environ.get("PI_SSH_USER", "")
PI_SSH_KEY_PATH = os.environ.get("PI_SSH_KEY_PATH", "")

# --- Background jobs (APScheduler) ---
# Started explicitly from dashboard/apps.py so it only runs once, not once
# per worker-fork - see that file for the guard.
SCHEDULER_AUTOSTART = os.environ.get("DJANGO_SCHEDULER_AUTOSTART", "True") == "True"
