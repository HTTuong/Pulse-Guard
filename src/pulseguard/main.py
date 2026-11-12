"""Entry point for uvicorn: `uvicorn pulseguard.main:app`."""

from pulseguard.api.app import create_app

app = create_app()
