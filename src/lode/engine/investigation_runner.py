"""Public entry point for V1 investigations."""

from __future__ import annotations


async def run_investigation(investigation_id: int, session) -> None:
    from lode.engine.investigation_engine import run_sequential_investigation

    await run_sequential_investigation(investigation_id, session)
