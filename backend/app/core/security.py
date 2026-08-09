import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import bcrypt

from app.core.config import settings


def hash_password(password: str) -> str:
    """Hash a password with bcrypt before storing it in the database."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify bcrypt hashes and temporarily support existing scrypt hashes."""
    try:
        if hashed_password.startswith(("$2a$", "$2b$", "$2y$")):
            return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))

        # Existing users created before the bcrypt upgrade can still log in once.
        algorithm, salt_hex, digest_hex = hashed_password.split("$", 2)
        if algorithm != "scrypt":
            return hmac.compare_digest(plain_password, hashed_password)
        candidate = hashlib.scrypt(
            plain_password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1
        )
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (TypeError, ValueError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return not password_hash.startswith(("$2a$", "$2b$", "$2y$"))


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_access_token(subject: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
    }
    header = {"alg": settings.algorithm, "typ": "JWT"}
    signing_input = ".".join(
        (
            _base64url_encode(json.dumps(header, separators=(",", ":")).encode()),
            _base64url_encode(json.dumps(payload, separators=(",", ":")).encode()),
        )
    )
    signature = hmac.new(settings.secret_key.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_base64url_encode(signature)}"


def decode_access_token(token: str) -> dict[str, object] | None:
    try:
        header_part, payload_part, signature_part = token.split(".")
        signing_input = f"{header_part}.{payload_part}"
        expected_signature = hmac.new(
            settings.secret_key.encode(), signing_input.encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected_signature, _base64url_decode(signature_part)):
            return None
        payload = json.loads(_base64url_decode(payload_part))
        if int(payload["exp"]) <= int(datetime.now(timezone.utc).timestamp()):
            return None
        return payload
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
