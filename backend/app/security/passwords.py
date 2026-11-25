from functools import lru_cache

from pwdlib import PasswordHash

password_hasher = PasswordHash.recommended()


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    return password_hasher.hash("dummy-password-for-login-timing")
