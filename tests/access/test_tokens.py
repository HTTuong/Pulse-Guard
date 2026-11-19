"""Passwords and staff tokens, without a database (REQ-ACC-01)."""

import base64
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from pulseguard.access import tokens
from pulseguard.access.passwords import hash_password, verify_password

SECRET = "a-test-secret-that-is-long-enough-0123456789"
USER = uuid.UUID("30000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 11, 19, 8, 0, tzinfo=UTC)
EIGHT_HOURS = timedelta(hours=8)


def issued(**claims: object) -> str:
    payload = {
        "sub": str(USER),
        "ver": 0,
        "iss": tokens.ISSUER,
        "aud": tokens.AUDIENCE,
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + EIGHT_HOURS).timestamp()),
    }
    payload.update(claims)
    return jwt.encode(
        {k: v for k, v in payload.items() if v is not None}, SECRET, algorithm="HS256"
    )


def verify(token: str, at: datetime = NOW) -> tokens.Claims | None:
    return tokens.verify(SECRET, token, at, EIGHT_HOURS)


def test_password_round_trip_and_salt() -> None:
    first, second = hash_password("correct horse battery"), hash_password("correct horse battery")
    assert first != second  # a new salt each time
    assert verify_password("correct horse battery", first)
    assert not verify_password("correct horse batterY", first)
    assert "correct horse" not in first


@pytest.mark.parametrize(
    "stored",
    ["", "x", "bcrypt$1$2$3$4$5", "scrypt$a$8$1$AAAA$AAAA", "scrypt$16384$8$1$not base64$AAAA"],
)
def test_a_malformed_stored_hash_never_verifies(stored: str) -> None:
    assert verify_password("anything", stored) is False


def test_issue_and_verify() -> None:
    token, expires = tokens.issue(SECRET, USER, 3, NOW, EIGHT_HOURS)
    assert expires == NOW + EIGHT_HOURS
    assert verify(token) == tokens.Claims(USER, 3)


def test_token_lifetime_edges() -> None:
    """Valid from iat up to, not including, exp."""
    token, _ = tokens.issue(SECRET, USER, 0, NOW, EIGHT_HOURS)
    assert verify(token, NOW) is not None
    assert verify(token, NOW + EIGHT_HOURS - timedelta(seconds=1)) is not None
    assert verify(token, NOW + EIGHT_HOURS) is None
    assert verify(token, NOW - timedelta(seconds=1)) is None  # issued in the future


@pytest.mark.parametrize(
    "token",
    [
        issued(exp=int((NOW + timedelta(hours=9)).timestamp())),  # longer than the backend issues
        issued(aud="someone-else"),
        issued(iss="someone-else"),
        issued(sub="not-a-uuid"),
        issued(ver=True),
        issued(ver="0"),
        issued(sub=None),
        issued(ver=None),
        issued(exp=None),
        jwt.encode(
            {"sub": str(USER)}, "another-secret-that-is-long-enough-012345", algorithm="HS256"
        ),
        "not.a.token",
        "",
    ],
)
def test_tokens_that_must_be_refused(token: str) -> None:
    assert verify(token) is None


def test_a_complete_token_signed_with_another_algorithm_is_refused() -> None:
    """Every claim right, the right secret, but HS512: only HS256 is accepted. A first version of
    this case lacked claims, so it was refused for that and proved nothing about the algorithm
    (a mutant that allowed HS512 survived it, d244)."""
    claims = jwt.decode(issued(), options={"verify_signature": False})
    assert verify(jwt.encode(claims, SECRET, algorithm="HS256")) is not None
    assert verify(jwt.encode(claims, SECRET, algorithm="HS512")) is None


def test_alg_none_is_refused() -> None:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(
        b"="
    )
    body = jwt.decode(issued(), options={"verify_signature": False})
    payload = base64.urlsafe_b64encode(json.dumps(body).encode()).rstrip(b"=")
    assert verify(f"{header.decode()}.{payload.decode()}.") is None


def test_a_changed_payload_breaks_the_signature() -> None:
    head, body, signature = issued().split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["ver"] = 1
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    assert verify(f"{head}.{forged}.{signature}") is None


def test_wrong_password_and_unknown_user_take_about_as_long() -> None:
    """Login runs scrypt for an unknown email too (access/service.py); here the two branches of
    verify_password, a real hash and the dummy one, must cost about the same."""
    from pulseguard.access.passwords import DUMMY_HASH

    real = hash_password("a password of the user")

    def median(stored: str) -> float:
        times = []
        for _ in range(5):
            start = time.perf_counter()
            verify_password("a wrong password", stored)
            times.append(time.perf_counter() - start)
        return sorted(times)[2]

    ratio = median(real) / median(DUMMY_HASH)
    assert 0.5 < ratio < 2, ratio
