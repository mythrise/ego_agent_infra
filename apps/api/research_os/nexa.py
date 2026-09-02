"""TDSQL Nexa production data-plane configuration and truthful readiness probe."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .models import TruthClass


class NexaDataPlane:
    def __init__(self, database_url: Optional[str] = None) -> None:
        self.database_url = (database_url or "").strip()

    def status(self, probe: bool = False) -> Dict[str, Any]:
        base: Dict[str, Any] = {
            "provider": "TDSQL Nexa",
            "role": "production agent-native data plane",
            "configured": bool(self.database_url),
            "interfaces": {
                "sql": "adapter_ready",
                "rest": "vendor_capability_not_bound",
                "posix": "vendor_capability_not_bound",
                "mcp": "vendor_capability_not_bound",
            },
            "local_fallback_is_nexa": False,
        }
        if not self.database_url:
            return {
                **base,
                "status": "not_configured",
                "truth_class": TruthClass.NOT_CONFIGURED.value,
                "live_probe": TruthClass.NOT_RUN.value,
            }
        if not probe:
            return {
                **base,
                "status": "configured_unprobed",
                "truth_class": TruthClass.UNVERIFIED.value,
                "live_probe": TruthClass.NOT_RUN.value,
            }
        try:
            import psycopg

            with psycopg.connect(self.database_url, connect_timeout=5) as connection:
                row = connection.execute("SELECT current_database(), version()").fetchone()
            return {
                **base,
                "status": "reachable",
                "truth_class": TruthClass.LIVE.value,
                "live_probe": "PASS",
                "database": row[0],
                "server_version": row[1],
                "nexa_identity": "UNVERIFIED",
            }
        except Exception as error:  # pragma: no cover - exercised only with an external service
            return {
                **base,
                "status": "unreachable",
                "truth_class": TruthClass.UNVERIFIED.value,
                "live_probe": "FAIL",
                "error_type": type(error).__name__,
            }
