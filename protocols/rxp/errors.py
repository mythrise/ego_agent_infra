"""Stable machine-readable failures for RXP implementations and adapters."""

from __future__ import annotations

from typing import Any, Mapping

from .canonical import canonical_json


class RXPError(Exception):
    def __init__(
        self, code: str, message: str, details: Mapping[str, Any] | None = None
    ) -> None:
        self.code = code
        self.message = message
        self.details = dict(details or {})
        super().__init__(message)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
        }

    def __str__(self) -> str:
        return canonical_json(self.as_dict())
