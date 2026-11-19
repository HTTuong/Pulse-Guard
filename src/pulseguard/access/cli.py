"""Set up a fresh database: units with beds, and the first users.

    python -m pulseguard.access.cli create-unit W1 "Ward 1" --beds 12
    PULSEGUARD_NEW_PASSWORD=... python -m pulseguard.access.cli create-user \\
        admin@example.com "Admin" ADMIN --units W1

The password is read from PULSEGUARD_NEW_PASSWORD, or asked for, never given on the command line,
where it would stay in the shell history. Only fake data belongs here (REQ-NFR-04).
"""

import argparse
import getpass
import os
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulseguard.access.actor import Role
from pulseguard.access.passwords import hash_password
from pulseguard.access.schemas import MIN_PASSWORD_LENGTH
from pulseguard.config import Settings
from pulseguard.db import make_engine
from pulseguard.models import AppUser, Bed, Unit, UserUnit


def create_unit(db: Session, code: str, name: str, beds: int) -> str:
    unit = Unit(code=code, name=name, time_zone="Europe/Helsinki")
    db.add(unit)
    db.flush()
    for number in range(1, beds + 1):
        db.add(Bed(unit_id=unit.id, label=f"B{number}"))
    db.commit()
    return f"unit {code} with {beds} beds"


def create_user(
    db: Session, email: str, name: str, role: str, units: list[str], password: str
) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"password must have at least {MIN_PASSWORD_LENGTH} characters")
    found = {u.code: u.id for u in db.scalars(select(Unit).where(Unit.code.in_(units)))}
    missing = sorted(set(units) - set(found))
    if missing:
        raise SystemExit(f"unknown units: {', '.join(missing)}")
    user = AppUser(
        email=email.strip().lower(),
        display_name=name,
        role=Role(role).value,
        password_hash=hash_password(password),
        active=True,
        token_version=0,
    )
    db.add(user)
    db.flush()
    for code in units:
        db.add(UserUnit(user_id=user.id, unit_id=found[code]))
    db.commit()
    return f"user {user.email} ({role})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pulseguard.access.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    unit = commands.add_parser("create-unit")
    unit.add_argument("code")
    unit.add_argument("name")
    unit.add_argument("--beds", type=int, default=12)
    user = commands.add_parser("create-user")
    user.add_argument("email")
    user.add_argument("name")
    user.add_argument("role", choices=[r.value for r in Role])
    user.add_argument("--units", default="", help="unit codes, comma separated")
    args = parser.parse_args(argv)

    settings = Settings()
    with Session(make_engine(settings.database_url, settings.db_check_timeout)) as db:
        if args.command == "create-unit":
            done = create_unit(db, args.code, args.name, args.beds)
        else:
            password = os.environ.get("PULSEGUARD_NEW_PASSWORD") or getpass.getpass("Password: ")
            units = [c for c in args.units.split(",") if c]
            done = create_user(db, args.email, args.name, args.role, units, password)
    print(f"created {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
