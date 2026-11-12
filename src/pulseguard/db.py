"""Database engine. One engine per process; modules get sessions from it."""

from sqlalchemy import Engine, create_engine


def make_engine(database_url: str, connect_timeout: int) -> Engine:
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": connect_timeout},
    )
