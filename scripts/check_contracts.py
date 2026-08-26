#!/usr/bin/env python3
"""CLI entry point for the frozen contract fixture checks."""

from lode.contracts.checks import render_result


if __name__ == "__main__":
    print(render_result())
