"""Programmatic entry point to run Alembic migrations.

The database schema is applied automatically on server startup (see
``lode.api.main``). Alembic runs in a short-lived subprocess so it
owns its own event loop and uses the async engine defined in ``env.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from lode.config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_migrations() -> None:
    """Apply all pending migrations (``alembic upgrade head``)."""
    env = {**os.environ, "LODE_DATABASE_URL": settings.database_url}
    # Use the same interpreter that imported this module so the alembic
    # package is guaranteed to be importable (the bare `alembic` CLI may not
    # be on PATH inside a subprocess).
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(PROJECT_ROOT),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Database migration failed:\n" + (result.stderr or result.stdout)
        )
