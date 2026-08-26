#!/usr/bin/env python3
"""CLI entry point for removed V1 contract detection."""

from __future__ import annotations

import argparse

from lode.contracts.checks import ROOT
from lode.contracts.forbidden_scan import DEFAULT_PATHS, scan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", default=list(DEFAULT_PATHS))
    args = parser.parse_args()
    findings = scan([(ROOT / value).resolve() for value in args.paths])
    if findings:
        print("\n".join(findings))
        print(f"forbidden contract findings: {len(findings)}")
        return 1
    print("forbidden contract findings: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
