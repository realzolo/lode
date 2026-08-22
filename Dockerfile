# Lode — backend API image.
#
# The application auto-runs Alembic migrations on startup (see the lifespan
# hook in lode/api/main.py), so a fresh container is always
# schema-current before it serves traffic. Secrets are supplied via environment
# variables at runtime — never baked into the image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build tooling for any wheels that need compilation (asyncpg/aiokafka ship
# wheels for common platforms, but keep this for safety on exotic arches).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Copy project sources, then install. Copying pyproject + src together keeps the
# installable package self-contained.
COPY pyproject.toml ./
COPY src ./src
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

RUN pip install --upgrade pip && pip install .

EXPOSE 8000

CMD ["uvicorn", "lode.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
