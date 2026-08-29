"""Structured bridge errors with stable machine-readable semantics."""

from __future__ import annotations

from typing import Any, Dict, Optional


class BridgeError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


class UpstreamError(BridgeError):
    """A typed AgentTeams, Matrix, or EgoAgentOS HTTP failure."""

    def __init__(
        self,
        upstream: str,
        operation: str,
        http_status: int,
        message: str,
        *,
        body: Any = None,
    ) -> None:
        if http_status == 409:
            code = "%s_conflict" % upstream
            retryable = True
        elif http_status in {401, 403}:
            code = "%s_forbidden" % upstream
            retryable = False
        elif http_status == 404:
            code = "%s_not_found" % upstream
            retryable = False
        elif http_status == 429 or http_status >= 500:
            code = "%s_unavailable" % upstream
            retryable = True
        else:
            code = "%s_request_failed" % upstream
            retryable = False
        super().__init__(
            code,
            "%s %s failed with HTTP %d: %s" % (upstream, operation, http_status, message),
            status_code=502,
            retryable=retryable,
            details={
                "upstream": upstream,
                "operation": operation,
                "http_status": http_status,
                "body": body,
            },
        )


class LiveAgentTeamsUnavailable(BridgeError):
    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(
            "live_agentteams_unavailable",
            message,
            status_code=503,
            retryable=True,
            details=details,
        )
