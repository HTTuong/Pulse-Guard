# Backend image: installs the package, runs migrations at start, then serves the API.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install .

COPY alembic.ini ./
COPY migrations ./migrations

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000

# urlopen raises on 503, so the container turns unhealthy when the database is unreachable.
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)"

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn pulseguard.main:app --host 0.0.0.0 --port 8000"]
