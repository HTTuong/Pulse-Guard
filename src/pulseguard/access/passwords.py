"""Password hashing with scrypt from the standard library (memory hard, listed by OWASP).
Stored as scrypt$n$r$p$salt$hash, so the cost can be raised later and old hashes still verify."""

import base64
import hashlib
import hmac
import secrets

N, R, P = 2**14, 8, 1
SALT_BYTES, HASH_BYTES = 16, 32


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=HASH_BYTES)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(base64.b64decode(expected)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, base64.b64decode(expected))


# Verified against when the email is unknown, so a wrong email takes as long as a wrong password
# and the response time does not tell which emails exist.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
