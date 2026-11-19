"""Device credentials: 256 random bits, so a plain SHA-256 is enough; no password stretching."""

import hashlib


def hash_credential(credential: str) -> str:
    return hashlib.sha256(credential.encode()).hexdigest()
