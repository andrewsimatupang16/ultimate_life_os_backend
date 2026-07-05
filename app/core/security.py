"""
Compatibility layer.
File lama masih mengimpor app.core.security, jadi file ini tetap ada,
tetapi semua logic auth diarahkan ke app.auth agar tidak ada dua sistem JWT/password.
"""

from app.auth import create_access_token, decode_token, get_password_hash, verify_password


def hash_password(password: str) -> str:
    return get_password_hash(password)


def create_token(data: dict) -> str:
    return create_access_token(data)
