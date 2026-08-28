#!/usr/bin/env python3
"""Run rollback-only smoke checks for the PostgreSQL triggers."""

from __future__ import annotations

import asyncio
import json

from lode.config import settings
from lode.contracts.schema_behavior import check_schema_behavior
from lode.development.isolated_database import require_isolated_database

if __name__ == "__main__":
    require_isolated_database("database behavior check")
    result = asyncio.run(check_schema_behavior(settings.database_url))
    print(json.dumps(result, sort_keys=True, indent=2))
