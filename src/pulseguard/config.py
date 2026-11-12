"""Settings read from environment variables prefixed with PULSEGUARD_."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PULSEGUARD_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://pulseguard:pulseguard@localhost:5434/pulseguard"
    # Seconds the health check waits for the database before it reports it unreachable.
    db_check_timeout: int = 2
