"""Login, token use and revocation through the API (REQ-ACC-01, REQ-ACC-02)."""

from datetime import timedelta

import pytest
from httpx import Response
from sqlalchemy import text

from pulseguard.access.passwords import hash_password
from pulseguard.api.app import create_app
from pulseguard.config import Settings
from tests.ingest.bed import equipped_bed
from tests.ward import ADMIN, NURSE_W1, W1, W2, Ward

pytestmark = pytest.mark.db
PASSWORD = "a long enough fake password"


@pytest.fixture
def with_passwords(ward: Ward) -> Ward:
    with ward.engine.begin() as connection:
        connection.execute(
            text("UPDATE app_user SET password_hash = :h"), {"h": hash_password(PASSWORD)}
        )
    return ward


def login(ward: Ward, email: str, password: str = PASSWORD) -> Response:
    response: Response = ward.client(None).post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    return response


def test_login_gives_a_token_for_8_hours(with_passwords: Ward) -> None:
    response = login(with_passwords, "nurse1@example.com")
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "NURSE"
    assert body["expires_at"] == "2026-11-13T02:52:03.000Z"
    me = with_passwords.as_token(body["access_token"]).get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json() == {"user_id": str(NURSE_W1.user_id), "role": "NURSE", "unit_ids": [str(W1)]}


def test_email_is_not_case_sensitive(with_passwords: Ward) -> None:
    assert login(with_passwords, "  Nurse1@Example.COM ").status_code == 200


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("nurse1@example.com", "a wrong password"),
        ("nobody@example.com", PASSWORD),
        ("nurse1@example.com", PASSWORD.upper()),
    ],
)
def test_wrong_login_is_401_with_the_same_answer(
    with_passwords: Ward, email: str, password: str
) -> None:
    response = login(with_passwords, email, password)
    assert response.status_code == 401
    assert response.json() == {
        "type": "/problems/unauthenticated",
        "title": "Wrong email or password",
        "status": 401,
        "code": "UNAUTHENTICATED",
    }


def test_a_deactivated_user_cannot_log_in(with_passwords: Ward) -> None:
    with with_passwords.engine.begin() as connection:
        connection.execute(
            text("UPDATE app_user SET active = false WHERE email = 'nurse1@example.com'")
        )
    assert login(with_passwords, "nurse1@example.com").status_code == 401


def test_token_expires_after_8_hours(with_passwords: Ward) -> None:
    token = login(with_passwords, "nurse1@example.com").json()["access_token"]
    with_passwords.clock.current += timedelta(hours=8) - timedelta(seconds=1)
    assert with_passwords.as_token(token).get("/api/v1/auth/me").status_code == 200
    with_passwords.clock.current += timedelta(seconds=1)
    assert with_passwords.as_token(token).get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        "Bearer",
        "Bearer ",
        "bearer {t}",
        "Token {t}",
        "Bearer {t} extra",
        "Bearer  {t}",
        "{t}",
        "Bearer {t}x",
    ],
)
def test_only_an_exact_bearer_header_is_accepted(ward: Ward, header: str) -> None:
    token = ward.token(NURSE_W1)
    response = ward.client(None).get(
        "/api/v1/auth/me", headers={"Authorization": header.format(t=token)}
    )
    assert response.status_code == 401


def test_deactivating_a_user_stops_their_token_at_once(ward: Ward) -> None:
    token = ward.token(NURSE_W1)
    assert ward.as_token(token).get("/api/v1/auth/me").status_code == 200
    admin = ward.as_token(ward.token(ADMIN))
    assert (
        admin.patch(f"/api/v1/users/{NURSE_W1.user_id}", json={"active": False}).status_code == 200
    )
    assert ward.as_token(token).get("/api/v1/auth/me").status_code == 401
    # Activating again does not bring the old token back: the version only goes up.
    assert (
        admin.patch(f"/api/v1/users/{NURSE_W1.user_id}", json={"active": True}).status_code == 200
    )
    assert ward.as_token(token).get("/api/v1/auth/me").status_code == 401


def test_a_role_change_stops_the_old_token(ward: Ward) -> None:
    token = ward.token(NURSE_W1)
    admin = ward.as_token(ward.token(ADMIN))
    response = admin.patch(f"/api/v1/users/{NURSE_W1.user_id}", json={"role": "PHYSICIAN"})
    assert response.status_code == 200
    assert response.json()["role"] == "PHYSICIAN"
    assert ward.as_token(token).get("/api/v1/auth/me").status_code == 401
    assert (
        ward.as_token(ward.token(NURSE_W1, version=1)).get("/api/v1/auth/me").json()["role"]
        == "PHYSICIAN"
    )


def test_a_unit_change_applies_to_the_same_token(ward: Ward) -> None:
    """Units are read on every request: a nurse moved to Ward 2 loses Ward 1 at once."""
    session = ward.open_session(1, 1)
    token = ward.token(NURSE_W1)
    assert ward.as_token(token).get(f"/api/v1/sessions/{session}").status_code == 200
    admin = ward.as_token(ward.token(ADMIN))
    response = admin.patch(f"/api/v1/users/{NURSE_W1.user_id}", json={"unit_codes": ["W2"]})
    assert response.json()["unit_codes"] == ["W2"]
    assert ward.as_token(token).get(f"/api/v1/sessions/{session}").status_code == 403
    assert ward.as_token(token).get("/api/v1/auth/me").json()["unit_ids"] == [str(W2)]


def test_an_admin_cannot_lock_themselves_out(ward: Ward) -> None:
    admin = ward.as_token(ward.token(ADMIN))
    for change in ({"active": False}, {"role": "NURSE"}):
        response = admin.patch(f"/api/v1/users/{ADMIN.user_id}", json=change)
        assert response.status_code == 409
        assert response.json()["code"] == "SELF_LOCKOUT"


def test_admin_creates_a_user_who_can_log_in(ward: Ward) -> None:
    admin = ward.as_token(ward.token(ADMIN))
    body = {
        "email": "Nurse3@Example.com",
        "display_name": "Nurse Three",
        "role": "NURSE",
        "password": PASSWORD,
        "unit_codes": ["W1", "W2"],
    }
    response = admin.post("/api/v1/users", json=body)
    assert response.status_code == 201
    assert response.json()["email"] == "nurse3@example.com"
    assert response.json()["unit_codes"] == ["W1", "W2"]
    assert login(ward, "nurse3@example.com").status_code == 200
    again = admin.post("/api/v1/users", json=body)
    assert again.status_code == 409
    assert again.json()["code"] == "EMAIL_TAKEN"


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"password": "short pass"}, "password"),
        ({"unit_codes": ["W9"]}, "unit_codes"),
        ({"email": "not an email"}, "email"),
        ({"role": "JANITOR"}, "role"),
    ],
)
def test_invalid_new_user_is_400(ward: Ward, change: dict[str, object], field: str) -> None:
    body = {
        "email": "n3@example.com",
        "display_name": "N",
        "role": "NURSE",
        "password": PASSWORD,
        "unit_codes": [],
    } | change
    response = ward.as_token(ward.token(ADMIN)).post("/api/v1/users", json=body)
    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == field
    assert ward.scalar("SELECT count(*) FROM app_user WHERE email = 'n3@example.com'") == 0


def test_a_staff_token_on_ingest_is_403(ward: Ward) -> None:
    equipped_bed(ward, 1, sensors=False)
    response = ward.as_token(ward.token(NURSE_W1)).post(
        "/api/v1/ingest/batches",
        json={"monitor": "SN-PM-0001", "sent_at": "2026-11-12T18:52:03.000Z", "readings": []},
    )
    assert response.status_code == 403


def test_a_token_signed_with_another_secret_is_401(ward: Ward) -> None:
    from pulseguard.access import tokens

    forged, _ = tokens.issue(
        "another-secret-that-is-long-enough-0123456789",
        NURSE_W1.user_id,
        0,
        ward.clock.now(),
        timedelta(hours=8),
    )
    assert ward.as_token(forged).get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize("secret", [None, "", "x" * 31])
def test_the_backend_refuses_to_start_without_a_strong_secret(secret: str | None) -> None:
    with pytest.raises(RuntimeError, match="PULSEGUARD_JWT_SECRET"):
        create_app(settings=Settings(jwt_secret=secret))


def test_unknown_email_and_wrong_password_take_about_as_long(with_passwords: Ward) -> None:
    """The answer is the same (above); the time must not tell either. Without the dummy hash an
    unknown email answers at once while a wrong password runs scrypt."""
    import time

    def median(email: str) -> float:
        times = []
        for _ in range(5):
            start = time.perf_counter()
            assert login(with_passwords, email, "a wrong password").status_code == 401
            times.append(time.perf_counter() - start)
        return sorted(times)[2]

    ratio = median("nobody@example.com") / median("nurse1@example.com")
    assert 0.5 < ratio < 2, ratio


def test_a_user_deactivated_straight_in_the_database_loses_access(ward: Ward) -> None:
    """An emergency deactivation by SQL does not raise token_version; the active flag alone must
    still stop the token. Found in d244 by a mutant: the version check had hidden this case."""
    token = ward.token(NURSE_W1)
    with ward.engine.begin() as connection:
        connection.execute(
            text("UPDATE app_user SET active = false WHERE id = :id"), {"id": NURSE_W1.user_id}
        )
    assert ward.as_token(token).get("/api/v1/auth/me").status_code == 401
