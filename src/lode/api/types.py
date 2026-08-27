"""Shared public API scalar contracts."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

MAX_ENTITY_ID = 2**52 - 1
EntityId = Annotated[int, Field(gt=0, le=MAX_ENTITY_ID)]
