"""Strict TencentDB Agent Memory v3 HTTP adapter.

This module mirrors the published v3 isolation contract without vendoring the SDK.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List

from .models import FocusMessage, TruthClass


class TencentAgentMemoryAdapter:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        service_id: str,
        *,
        space_id: str,
        timeout: float = 30.0,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.service_id = service_id
        self.space_id = space_id
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.api_key and self.service_id and self.space_id)

    def status(self) -> Dict[str, Any]:
        return {
            "provider": "TencentDB Agent Memory",
            "api_contract": "v3",
            "status": "configured_unprobed" if self.configured else "not_configured",
            "truth_class": (
                TruthClass.UNVERIFIED.value if self.configured else TruthClass.NOT_CONFIGURED.value
            ),
            "isolation_fields": ["team_id", "agent_id", "user_id", "session_id", "task_id"],
            "levels": ["L0 conversation", "L1 atomic", "L2 scenario", "L3 core"],
        }

    def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if not self.configured:
            raise RuntimeError("TencentDB Agent Memory endpoint is not configured")
        request = urllib.request.Request(
            "%s%s" % (self.endpoint, path),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": "Bearer %s" % self.api_key,
                "x-tdai-service-id": self.service_id,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise RuntimeError("TencentDB Agent Memory HTTP %d" % error.code) from error
        if not isinstance(decoded, dict):
            raise RuntimeError("TencentDB Agent Memory returned a non-object response")
        if "code" in decoded and decoded.get("code") not in (0, "0"):
            raise RuntimeError("TencentDB Agent Memory rejected the request")
        data = decoded.get("data", decoded)
        return data if isinstance(data, dict) else {"result": data}

    def commit_and_compact(
        self,
        *,
        team_id: str,
        agent_id: str,
        user_id: str,
        session_id: str,
        task_id: str,
        stage_id: str,
        messages: List[FocusMessage],
    ) -> Dict[str, Any]:
        isolation = {
            "team_id": team_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "session_id": session_id,
            "task_id": task_id,
            "space_id": self.space_id,
        }
        added = self._post(
            "/v3/skill/conversation/add",
            {**isolation, "messages": [message.model_dump(mode="json") for message in messages]},
        )
        archived = self._post(
            "/v3/skill/conversation/force-archive",
            {**isolation, "reason": "EgoAgentOS stage complete: %s" % stage_id},
        )
        return {
            "provider": "TencentDB Agent Memory",
            "truth_class": TruthClass.LIVE.value,
            "conversation_add": added,
            "force_archive": archived,
        }
