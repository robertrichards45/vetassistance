from flask_login import LoginManager
try:
    from flask_wtf.csrf import CSRFProtect  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    # Allow the app to boot even if Flask-WTF isn't installed yet.
    # (Useful during first-time setup / minimal deployments.)
    class CSRFProtect:  # type: ignore
        def init_app(self, app):
            return None

        def exempt(self, view):
            return view
try:
    from flask_limiter import Limiter  # type: ignore
    from flask_limiter.util import get_remote_address  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    Limiter = None  # type: ignore

    def get_remote_address():  # type: ignore
        return "0.0.0.0"
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import sessionmaker, scoped_session

login_manager = LoginManager()
csrf = CSRFProtect()
if Limiter is not None:
    limiter = Limiter(key_func=get_remote_address, default_limits=["1000 per day", "120 per hour"])
else:
    limiter = None  # type: ignore
SessionLocal = scoped_session(sessionmaker())

def init_db(app):
    db_url = app.config["SQLALCHEMY_DATABASE_URI"]
    if db_url.startswith("sqlite"):
        engine = create_engine(db_url, future=True, pool_pre_ping=True, poolclass=NullPool)
    else:
        engine = create_engine(db_url, future=True, pool_pre_ping=True, pool_size=20, max_overflow=30, pool_timeout=30)
    SessionLocal.configure(bind=engine)
    return engine
