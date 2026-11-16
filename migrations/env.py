"""Alembic environment. Migrations are written by hand in SQL; there is no metadata to compare."""

from alembic import context
from sqlalchemy import create_engine, pool

from pulseguard.config import Settings

config = context.config


def database_url() -> str:
    # A URL given by a caller (for example a test, through config.attributes) wins over settings.
    url = config.attributes.get("database_url")
    return str(url) if url else Settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=database_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
