"""Environment-backed settings without logging credentials."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


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
        )
