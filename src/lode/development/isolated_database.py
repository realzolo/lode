"""Guards for verification code that writes PostgreSQL state."""

from __future__ import annotations

import os
import re

from sqlalchemy.engine import make_url

_TEST_DATABASE = re.compile(r"^lode_test_[a-z0-9_]+$")


def require_isolated_database(purpose: str) -> None:
    raw_url = os.environ.get("LODE_DATABASE_URL", "")
    marker = os.environ.get("LODE_TOOLING_ISOLATED_DATABASE")
    database = make_url(raw_url).database if raw_url else None
    if marker != "1" or database is None or not _TEST_DATABASE.fullmatch(database):
        raise RuntimeError(
            f"{purpose} writes verification fixtures and requires an isolated "
            "lode_test_* PostgreSQL database; run the corresponding make target"
        )
