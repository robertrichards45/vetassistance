from __future__ import annotations

from werkzeug.security import generate_password_hash, check_password_hash

def hash_password(password: str) -> str:
    """Return a salted password hash suitable for storage."""
    if password is None:
        raise ValueError("password is required")
    password = str(password)
    if password.strip() == "":
        raise ValueError("password cannot be empty")
    return generate_password_hash(password)

def verify_password(password_hash: str, password: str) -> bool:
    """Verify a candidate password against a stored hash."""
    if not password_hash:
        return False
    if password is None:
        return False
    return check_password_hash(password_hash, str(password))
