import hashlib
import secrets


def generate_guest_token() -> str:
    return secrets.token_urlsafe(32)


def hash_guest_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
