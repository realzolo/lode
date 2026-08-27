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
    removed_service = "service" + "_name"
    removed_commit = "git" + "_commit"
    source.write_text(f"{removed_service} = 'api'\n{removed_commit} = 'abc'\n", encoding="utf-8")

    findings = scan([source])

    assert len(findings) == 2
    assert any("removed_alert_field" in finding for finding in findings)
    assert any("removed_alert_revision" in finding for finding in findings)


def test_forbidden_contract_scan_distinguishes_service_model_from_display_text(
    tmp_path: Path,
) -> None:
    source = tmp_path / "models.py"
    removed_service = "Ser" + "vice"
    source.write_text(
        f"class {removed_service}:\n    pass\nlabel = 'Service endpoint'\n",
        encoding="utf-8",
    )

    findings = scan([source])

    assert len(findings) == 1
    assert "removed_service_model" in findings[0]


def test_forbidden_contract_scan_ignores_binary_and_build_directories(tmp_path: Path) -> None:
    binary = tmp_path / "fixture.bin"
    removed_service = "service" + "_name"
    binary.write_bytes(removed_service.encode())
    generated = tmp_path / "node_modules" / "legacy.ts"
    generated.parent.mkdir()
    generated.write_text(f"{removed_service} = 'ignored'", encoding="utf-8")

    assert scan([tmp_path]) == []


def test_forbidden_contract_scan_rejects_versioned_implementation_filenames(
    tmp_path: Path,
) -> None:
    implementation = tmp_path / "release_v1_evaluator.py"
    implementation.write_text("CURRENT = True\n", encoding="utf-8")

    assert scan([implementation]) == [
        f"{implementation}:versioned_implementation_filename"
    ]
