from __future__ import annotations

import base64
import hashlib
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def derive_fernet_key(secret: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())


def load_or_create_secret(path: Path) -> str:
    """Read the install secret, creating a 32-byte random one on first boot."""
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32)
    path.write_text(value + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


class SecretBox:
    """Encrypts provider API keys at rest (Fernet / AES-CBC + HMAC)."""

    def __init__(self, secret: str):
        self._fernet = Fernet(derive_fernet_key(secret)) if secret else None

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def encrypt(self, value: str | None) -> str:
        if not value:
            return ""
        if not self._fernet:
            raise RuntimeError("SECRET_KEY is not configured; refusing to store secrets in plaintext")
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str | None) -> str:
        if not token:
            return ""
        if not self._fernet:
            return ""
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return ""

    @staticmethod
    def mask(value: str, keep: int = 4) -> str:
        if not value:
            return "(none)"
        if len(value) <= keep * 2:
            return "***"
        return f"{value[:keep]}…{value[-keep:]}"
