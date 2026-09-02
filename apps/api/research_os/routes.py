"""FastAPI route registration for the AgentOS research compiler."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, Query, Request

from .models import CompileResearchRequest, ResourcePlan, StageCommitRequest


def register_research_os_routes(application: FastAPI) -> None:
    @application.post("/api/v1/research/compile", tags=["research-os"])
    def compile_research(
        body: CompileResearchRequest,
        request: Request,
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> Dict[str, Any]:
        request.app.state.operator_auth.authenticate(authorization)
        return request.app.state.research_os.compile(body)

    @application.post("/api/v1/research/resource-review", tags=["research-os"])
    def review_resources(
        body: ResourcePlan,
        request: Request,
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> Dict[str, Any]:
        request.app.state.operator_auth.authenticate(authorization)
        return request.app.state.research_os.review_resources(body)

    @application.post(
        "/api/v1/research/agents/{agent_id}/stages/commit", tags=["research-os", "memory"]
    )
    def commit_stage(
        agent_id: str,
        body: StageCommitRequest,
        request: Request,
        sync_remote: bool = Query(default=False),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> Dict[str, Any]:
        request.app.state.operator_auth.authenticate(authorization)
        return request.app.state.research_os.commit_stage(
            agent_id, body, sync_remote=sync_remote
        )

    @application.get("/api/v1/research/agents/{agent_id}/focus", tags=["research-os", "memory"])
    def focus(agent_id: str, request: Request) -> Dict[str, Any]:
        return request.app.state.research_os.focus(agent_id)

    @application.get("/api/v1/research/storage", tags=["research-os", "system"])
    def storage(
        request: Request,
        probe_nexa: bool = Query(default=False),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> Dict[str, Any]:
        if probe_nexa:
            request.app.state.operator_auth.authenticate(authorization)
        return request.app.state.research_os.storage_status(probe_nexa=probe_nexa)
