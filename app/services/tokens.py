import hashlib, hmac, secrets

def new_token() -> str:
    return secrets.token_urlsafe(24)

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def verify_hash(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), token_hash)
