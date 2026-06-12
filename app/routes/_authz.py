from functools import wraps
from flask import abort
from flask_login import current_user
from app.models.user import Role

def require_roles(*roles: Role):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated: abort(401)
            if not current_user.is_active: abort(403)
            if roles and current_user.role not in roles: abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return deco
