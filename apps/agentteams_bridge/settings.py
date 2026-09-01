"""Environment-backed settings without logging credentials."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


_FOCUS_MEMORY_MODES = {"disabled", "best_effort", "required"}


@dataclass(frozen=True)
class BridgeSettings:
    agentteams_base_url: str = "http://127.0.0.1:18080"
    agentteams_auth_token: str = field(default="", repr=False)
    matrix_base_url: str = "http://127.0.0.1:18080"
    matrix_access_token: str = field(default="", repr=False)
    ego_base_url: str = "http://127.0.0.1:8000"
    ego_operator_key: str = field(default="", repr=False)
    bridge_operator_key: str = field(default="", repr=False)
    database_url: str = field(default="", repr=False)
    migration_database_url: str = field(default="", repr=False)
    database_path: str = "/tmp/egoagentos-agentteams-bridge.sqlite3"
    request_timeout_seconds: float = 15.0
    focus_memory_mode: str = "disabled"
    focus_memory_service_token: str = field(default="", repr=False)
    focus_memory_tenant_id: str = "local"
    focus_memory_token_budget: int = 4000
    focus_memory_max_items: int = 12
    focus_memory_source_max_items: int = 64
    focus_memory_scan_limit: int = 512

    def __post_init__(self) -> None:
        if self.focus_memory_mode not in _FOCUS_MEMORY_MODES:
            raise ValueError(
                "EGO_FOCUS_MEMORY_MODE must be disabled, best_effort, or required"
            )
        token_bytes = self.focus_memory_service_token.encode("utf-8")
        if token_bytes and len(token_bytes) < 32:
            raise ValueError(
                "EGO_TRUSTED_MEMORY_SERVICE_TOKEN must contain at least 32 bytes"
            )
        if self.focus_memory_mode != "disabled" and not token_bytes:
            raise ValueError(
                "enabled focus-memory mode requires EGO_TRUSTED_MEMORY_SERVICE_TOKEN"
            )
        if not self.focus_memory_tenant_id or len(self.focus_memory_tenant_id) > 200:
            raise ValueError("EGO_TENANT_ID must contain between 1 and 200 characters")
        positive = {
            "EGO_FOCUS_MEMORY_TOKEN_BUDGET": self.focus_memory_token_budget,
            "EGO_FOCUS_MEMORY_MAX_ITEMS": self.focus_memory_max_items,
            "EGO_FOCUS_MEMORY_SOURCE_MAX_ITEMS": self.focus_memory_source_max_items,
            "EGO_FOCUS_MEMORY_SCAN_LIMIT": self.focus_memory_scan_limit,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError("%s must be a positive integer" % name)
        if self.focus_memory_max_items > self.focus_memory_source_max_items:
            raise ValueError(
                "EGO_FOCUS_MEMORY_MAX_ITEMS cannot exceed "
                "EGO_FOCUS_MEMORY_SOURCE_MAX_ITEMS"
            )

    @classmethod
    def from_env(cls) -> "BridgeSettings":
        return cls(
            agentteams_base_url=os.getenv(
                "AGENTTEAMS_CONTROLLER_URL", "http://127.0.0.1:18080"
            ).rstrip("/"),
            agentteams_auth_token=os.getenv("AGENTTEAMS_AUTH_TOKEN", ""),
            matrix_base_url=os.getenv(
                "AGENTTEAMS_MATRIX_URL", "http://127.0.0.1:18080"
            ).rstrip("/"),
            matrix_access_token=os.getenv("AGENTTEAMS_MATRIX_ACCESS_TOKEN", ""),
            ego_base_url=os.getenv("EGO_API_URL", "http://127.0.0.1:8000").rstrip("/"),
            ego_operator_key=os.getenv("EGO_OPERATOR_KEY", ""),
            bridge_operator_key=os.getenv(
                "EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY", ""
            ),
            database_url=os.getenv("EGO_AGENTTEAMS_DATABASE_URL", "").strip(),
            migration_database_url=os.getenv(
                "EGO_AGENTTEAMS_MIGRATION_DATABASE_URL", ""
            ).strip(),
            database_path=os.getenv(
                "EGO_AGENTTEAMS_BRIDGE_DB", "/tmp/egoagentos-agentteams-bridge.sqlite3"
            ),
            request_timeout_seconds=float(os.getenv("EGO_AGENTTEAMS_HTTP_TIMEOUT", "15")),
            focus_memory_mode=os.getenv("EGO_FOCUS_MEMORY_MODE", "disabled").strip(),
            focus_memory_service_token=os.getenv(
                "EGO_TRUSTED_MEMORY_SERVICE_TOKEN", ""
            ),
            focus_memory_tenant_id=os.getenv("EGO_TENANT_ID", "local").strip(),
            focus_memory_token_budget=int(
                os.getenv("EGO_FOCUS_MEMORY_TOKEN_BUDGET", "4000")
            ),
            focus_memory_max_items=int(os.getenv("EGO_FOCUS_MEMORY_MAX_ITEMS", "12")),
            focus_memory_source_max_items=int(
                os.getenv("EGO_FOCUS_MEMORY_SOURCE_MAX_ITEMS", "64")
            ),
            focus_memory_scan_limit=int(
                os.getenv("EGO_FOCUS_MEMORY_SCAN_LIMIT", "512")
            ),
        )
