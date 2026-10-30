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
