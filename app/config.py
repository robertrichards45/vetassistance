from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Project root = folder that contains 'app' package
PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=True)

def _default_sqlite_uri() -> str:
    db_path = PROJECT_ROOT / "instance" / "app.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # sqlite:///C:/path/to/db
    return "sqlite:///" + db_path.as_posix()

class BaseConfig:
    # Flask
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    ENV = os.environ.get("FLASK_ENV", "development")
    DEBUG = os.environ.get("FLASK_DEBUG", "1") == "1"
    WTF_CSRF_TIME_LIMIT = None

    # Database
    DATABASE_URL = os.environ.get("DATABASE_URL", _default_sqlite_uri())

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
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

    # Rate limiting storage (avoid in-memory warning)
    RATELIMIT_STORAGE_URL = os.environ.get("RATELIMIT_STORAGE_URL", "memory://")

class DevelopmentConfig(BaseConfig):
    ENV = "development"
    DEBUG = True

class ProductionConfig(BaseConfig):
    ENV = "production"
    DEBUG = False

# Backwards-compatible alias used across the codebase
Config = DevelopmentConfig


