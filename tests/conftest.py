"""Shared test setup: point the app at a throwaway SQLite database before it is imported."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="vetassist-tests-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp, "test.db").replace("\\", "/")
os.environ["UPLOAD_FOLDER"] = os.path.join(_tmp, "uploads")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.pop("RAILWAY_ENVIRONMENT", None)
os.environ.pop("REDIS_URL", None)
os.environ.pop("RATELIMIT_STORAGE_URL", None)

# The repo root is itself a package, so app.config may already be imported by the
# time this file runs. Point the already-loaded config at the throwaway database too.
import sys as _sys
if "app.config" in _sys.modules:
    _cfg = _sys.modules["app.config"]
    for _cls in (_cfg.BaseConfig, _cfg.DevelopmentConfig, _cfg.ProductionConfig, _cfg.Config):
        _cls.DATABASE_URL = os.environ["DATABASE_URL"]
        _cls.SQLALCHEMY_DATABASE_URI = os.environ["DATABASE_URL"]
        _cls.UPLOAD_FOLDER = _cls.STORAGE_ROOT = os.environ["UPLOAD_FOLDER"]
