"""Seed contract tests."""

from __future__ import annotations

import ast
from pathlib import Path


def test_seed_imports_only_final_models() -> None:
    seed_path = Path(__file__).resolve().parents[2] / "scripts" / "seed.py"
    source = seed_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "lode.db.models" in imported_modules
    assert all(not module.startswith("lode.db.models.") for module in imported_modules)
    assert "create_investigation" not in source
    assert "ApplicationServiceBinding" not in source
