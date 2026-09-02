"""Application service composing compiler, guardian, and memory providers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from .agent_memory import AgentMemoryRegistry
from .ladder import compile_research_input
from .models import CompileResearchRequest, ResourcePlan, StageCommitRequest
from .nexa import NexaDataPlane
from .resource_reviewer import IndependentResourceReviewer
from .tencent_memory import TencentAgentMemoryAdapter


class ResearchOSService:
    def __init__(self, memory_root: Optional[Path] = None) -> None:
        root = memory_root or Path(os.getenv("EGO_AGENT_MEMORY_ROOT", "data/agent-memory"))
        self.memories = AgentMemoryRegistry(root)
        self.reviewer = IndependentResourceReviewer()
        self.nexa = NexaDataPlane(os.getenv("EGO_NEXA_DATABASE_URL"))
        self.tencent_memory = TencentAgentMemoryAdapter(
            os.getenv("TENCENT_AGENT_MEMORY_ENDPOINT", ""),
            os.getenv("TENCENT_AGENT_MEMORY_API_KEY", ""),
            os.getenv("TENCENT_AGENT_MEMORY_SERVICE_ID", ""),
            space_id=os.getenv("TENCENT_AGENT_MEMORY_SPACE_ID", ""),
        )

    def compile(self, body: CompileResearchRequest) -> Dict[str, Any]:
        result = compile_research_input(body.input)
        if body.resource_plan is not None:
            review = self.reviewer.review(body.resource_plan)
        else:
            review = {
                "decision": "NOT_RUN",
                "gate": "BLOCK_EXECUTION",
                "reason": "A resource plan is required before approval and execution.",
            }
        return {**result, "resource_review": review}

    def review_resources(self, body: ResourcePlan) -> Dict[str, Any]:
        return self.reviewer.review(body)

    def commit_stage(
        self, agent_id: str, body: StageCommitRequest, *, sync_remote: Optional[bool] = None
    ) -> Dict[str, Any]:
        local = self.memories.for_agent(agent_id).commit(body)
        remote: Dict[str, Any] = self.tencent_memory.status()
        should_sync_remote = self.tencent_memory.configured if sync_remote is None else sync_remote
        if should_sync_remote:
            remote = self.tencent_memory.commit_and_compact(
                team_id=body.team_id,
                agent_id=agent_id,
                user_id=body.user_id,
                session_id=body.session_id,
                task_id=body.task_id,
                stage_id=body.stage_id,
                messages=body.messages,
            )
        return {
            "local": local,
            "remote": remote,
            "remote_requested": should_sync_remote,
            "policy": "auto-sync when TencentDB Agent Memory is configured",
        }

    def focus(self, agent_id: str) -> Dict[str, Any]:
        return self.memories.for_agent(agent_id).read()

    def storage_status(self, *, probe_nexa: bool = False) -> Dict[str, Any]:
        return {
            "authority_target": self.nexa.status(probe=probe_nexa),
            "context_memory_target": self.tencent_memory.status(),
            "deterministic_fallback": self.memories.status(),
            "claim_boundary": (
                "The local fallback is not TDSQL Nexa. LIVE Nexa and TencentDB Agent Memory "
                "claims require configured endpoints and successful provider receipts."
            ),
        }
