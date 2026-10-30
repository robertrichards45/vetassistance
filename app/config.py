from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Project root = folder that contains 'app' package
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# override=False (the default): a real value already set in the environment
# — e.g. Railway's dashboard-configured OPENAI_API_KEY — must win over
# whatever's in a local .env file. override=True previously let an empty
# placeholder line in .env silently clobber that real value at runtime.
load_dotenv(PROJECT_ROOT / ".env")

def _default_sqlite_uri() -> str:
    db_path = PROJECT_ROOT / "instance" / "app.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # sqlite:///C:/path/to/db
    return "sqlite:///" + db_path.as_posix()

def _normalize_database_url(raw_url: str | None) -> str:
    url = (raw_url or "").strip()
    if not url:
        return _default_sqlite_uri()
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and not url.startswith("postgresql+"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url

def _database_url() -> str:
    return _normalize_database_url(
        os.environ.get("DATABASE_URL")
        or os.environ.get("DATABASE_PUBLIC_URL")
        or os.environ.get("POSTGRES_URL")
    )

class BaseConfig:
    # Flask
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    ENV = os.environ.get("FLASK_ENV", "development")
    DEBUG = os.environ.get("FLASK_DEBUG", "1") == "1"
    WTF_CSRF_TIME_LIMIT = None
    # Token is still required on every form; skip the extra HTTPS Referer match so
    # privacy extensions that strip Referer don't break forms behind Railway's proxy.
    WTF_CSRF_SSL_STRICT = False

    # Database
    DATABASE_URL = _database_url()

    # Support both patterns used across the app
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Uploads / storage
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", str(PROJECT_ROOT / "uploads"))
    _max_gb = float(os.environ.get("MAX_UPLOAD_GB", "5"))
    MAX_CONTENT_LENGTH = None if _max_gb <= 0 else int(_max_gb * 1024 * 1024 * 1024)
    STORAGE_ROOT = os.environ.get("STORAGE_ROOT", UPLOAD_FOLDER)
    VA_FORMS_DIR = os.environ.get("VA_FORMS_DIR", str(PROJECT_ROOT / "va_forms"))
    VA_PREFILL_DIR = os.environ.get("VA_PREFILL_DIR", str(PROJECT_ROOT / "uploads" / "va_forms_prefilled"))

    # SMTP (email invites)
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASS = os.environ.get("SMTP_PASS", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "")

    # Branding
    SITE_NAME = os.environ.get("SITE_NAME", "Veteran Benefits Assistance")
    SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "veteranclaimsassistance@gmail.com")
    SUPPORT_PHONE = os.environ.get("SUPPORT_PHONE", "229-848-1633")
    DEFAULT_TENANT = os.environ.get("DEFAULT_TENANT", "Veteran Benefits Assistance")

    # AI
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.5")

    # Rate limiting storage (avoid in-memory warning)
    # Shared across gunicorn workers when Redis is available; falls back to memory.
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URL") or os.environ.get("REDIS_URL") or "memory://"
    RATELIMIT_STORAGE_URL = RATELIMIT_STORAGE_URI
    RATELIMIT_HEADERS_ENABLED = True
    # If Redis is briefly unreachable, keep serving pages instead of erroring.
    RATELIMIT_SWALLOW_ERRORS = True
    RATELIMIT_IN_MEMORY_FALLBACK_ENABLED = True

    # Session cookies
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

class DevelopmentConfig(BaseConfig):
    ENV = "development"
    DEBUG = True

class ProductionConfig(BaseConfig):
    ENV = "production"
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"

def _select_config():
    env = (os.environ.get("FLASK_ENV") or os.environ.get("ENV") or "").strip().lower()
    if env == "production" or os.environ.get("RAILWAY_ENVIRONMENT"):
        return ProductionConfig
    return DevelopmentConfig

# Backwards-compatible alias used across the codebase
Config = _select_config()


