from __future__ import annotations

from pathlib import Path

from lode.contracts.forbidden_scan import scan


def test_forbidden_contract_scan_accepts_final_v1_terms(tmp_path: Path) -> None:
    source = tmp_path / "contract.py"
    source.write_text(
        "workspace_id = 1\ntrace_id = 'opaque'\nsource_revision = '0' * 40\n",
        encoding="utf-8",
    )

    assert scan([source]) == []


def test_forbidden_contract_scan_reports_removed_business_fields(tmp_path: Path) -> None:
    source = tmp_path / "legacy.py"
    source.write_text("service_name = 'api'\ngit_commit = 'abc'\n", encoding="utf-8")

    findings = scan([source])

    assert len(findings) == 2
    assert any("removed_alert_field" in finding for finding in findings)
    assert any("removed_alert_revision" in finding for finding in findings)


def test_forbidden_contract_scan_ignores_binary_and_build_directories(tmp_path: Path) -> None:
    binary = tmp_path / "fixture.bin"
    binary.write_bytes(b"service_name")
    generated = tmp_path / "node_modules" / "legacy.ts"
    generated.parent.mkdir()
    generated.write_text("service_name = 'ignored'", encoding="utf-8")

    assert scan([tmp_path]) == []
