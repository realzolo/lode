"""Domain-level failures with stable, provider-neutral codes."""

from __future__ import annotations


class DomainValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
