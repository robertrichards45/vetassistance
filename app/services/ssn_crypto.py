from __future__ import annotations

import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


def _get_fernet() -> Optional[Fernet]:
    key = os.environ.get("SSN_ENCRYPTION_KEY", "").strip()
    if not key:
        return None
    try:
        return Fernet(key)
    except Exception:
        return None


def encrypt_ssn(raw_ssn: str) -> str:
    f = _get_fernet()
    if not f:
        return ""
    token = f.encrypt(raw_ssn.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_ssn(token: str) -> str:
    f = _get_fernet()
    if not f or not token:
        return ""
    try:
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""


def mask_ssn(last4: str) -> str:
    if not last4:
        return ""
    return f"***-**-{last4}"
