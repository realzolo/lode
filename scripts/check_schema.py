#!/usr/bin/env python3
"""Check a migrated PostgreSQL database against the frozen schema contract."""

from __future__ import annotations

import asyncio
import json

from lode.config import settings
from lode.contracts.schema_check import check_schema


if __name__ == "__main__":
    print(json.dumps(asyncio.run(check_schema(settings.database_url)), sort_keys=True, indent=2))
