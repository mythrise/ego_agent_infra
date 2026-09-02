"""FastAPI entrypoint for EgoAgentOS ResearchOps."""

import os
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import BackgroundTasks, FastAPI, Header, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .errors import ControlPlaneError
from .expert_runs import ExpertRunRequest, ExpertRunService
from .event_stream import iter_task_events
from .models import (
    AdvanceRequest,
    ApprovalDecisionRequest,
    AutorunRequest,
    CreateTaskRequest,
    DemoResetRequest,
    EvidenceIngestRequest,
    FinalizeTaskRequest,
    RXPVerifyRequest,
)
from .operator_auth import OperatorAuthenticator, OperatorIdentity
from .provenance import canonical_sha256
from .rxp_runtime import demo_ledger, schema_catalog, verify_uploaded_ledger
from .research_os import ResearchOSService, register_research_os_routes
from .service import ResearchOpsService
from .skill_runtime_api import SkillInvokeRequest, create_skill_registry, invoke_skill
from .store_factory import create_store
from .trusted_memory.focus_service import (
    TrustedMemoryFocusService,
    register_trusted_memory_focus_routes,
    validate_focus_service_token,
)
from protocols.rxp import RXPError


APPROVAL_TOKEN_HEADER = "X-Ego-Approval-Token"


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


def _error_payload(
    request: Request, code: str, message: str, details: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": _request_id(request),
        }
    }


def _idempotency_key(header_key: Optional[str], body_key: Optional[str]) -> Optional[str]:
    if header_key and body_key and header_key != body_key:
        raise ControlPlaneError(
            "idempotency_key_mismatch",
            "Header and body idempotency keys must match when both are supplied",
            400,
        )
    key = header_key or body_key
    if key and not 8 <= len(key) <= 128:
        raise ControlPlaneError(
            "invalid_idempotency_key",
            "Idempotency key length must be between 8 and 128 characters",
            400,
        )
    return key


def _run_idempotent(
    service: ResearchOpsService,
    method: str,
    path: str,
    key: Optional[str],
    request_body: Dict[str, Any],
    operation: Callable[[], Dict[str, Any]],
    cache_response: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if not key:
        return operation()
    request_hash = canonical_sha256(request_body)
    committed_error: Optional[ControlPlaneError] = None
    with service.store.transaction():
        cached = service.store.get_idempotent(method, path, key, request_hash)
        if cached:
            _, response = cached
            response["idempotent_replay"] = True
            return response
        try:
            response = operation()
        except ControlPlaneError as error:
            # Evidence-gate failure updates the persisted gate result by design. Commit that
            # state before returning the structured 409, but do not cache an error response.
            if error.code != "evidence_gate_failed":
                raise
            committed_error = error
            response = {}
        if committed_error is None:
            persisted = cache_response(response) if cache_response else response
            service.store.put_idempotent(method, path, key, request_hash, 200, persisted)
    if committed_error is not None:
        raise committed_error
    return response


def _redact_approval_replay(response: Dict[str, Any]) -> Dict[str, Any]:
    redacted = dict(response)
    redacted["approval_token"] = None
    redacted["token_notice"] = (
        "One-time token omitted from idempotent replay; request a new approval if it was lost."
    )
    return redacted


def create_app(
    db_path: Optional[str] = None,
    approval_hmac_secret: Optional[str] = None,
    *,
    database_url: Optional[str] = None,
    skills_path: Optional[str] = None,
    operator_key: Optional[str] = None,
    operator_id: Optional[str] = None,
    allow_unauthenticated_demo: Optional[bool] = None,
    trusted_memory_service_token: Optional[str] = None,
    tenant_id: Optional[str] = None,
    expert_gateway: Optional[Any] = None,
    expert_run_root: Optional[Path] = None,
) -> FastAPI:
    store = create_store(database_url=database_url, sqlite_path=db_path)
    service = ResearchOpsService(store, approval_hmac_secret=approval_hmac_secret)
    skill_registry = create_skill_registry(skills_path)
    operator_auth = OperatorAuthenticator(
        key=operator_key,
        operator_id=operator_id,
        allow_unauthenticated_demo=allow_unauthenticated_demo,
    )
    resolved_tenant_id: str = (
        tenant_id or os.getenv("EGO_TENANT_ID") or "local"
    ).strip()
    if not resolved_tenant_id:
        raise ValueError("EGO_TENANT_ID must contain at least one non-whitespace character")
    token = (
        trusted_memory_service_token
        if trusted_memory_service_token is not None
        else os.getenv("EGO_TRUSTED_MEMORY_SERVICE_TOKEN", "")
    )
    resolved_focus_token = validate_focus_service_token(token)

    application = FastAPI(
        title="EgoAgentOS ResearchOps API",
        description=(
            "Evidence-gated, deterministic control plane for multi-agent embodied-AI research. "
            "The bundled EgoLite run is explicitly synthetic."
        ),
        version="0.2.1",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    application.state.service = service
    application.state.skill_registry = skill_registry
    application.state.operator_auth = operator_auth
    application.state.trusted_memory_focus_service = TrustedMemoryFocusService(
        store,
        tenant_id=resolved_tenant_id,
    )
    application.state.trusted_memory_service_token = resolved_focus_token
    application.state.research_os = ResearchOSService(
        memory_root=(expert_run_root / "agent-memory") if expert_run_root else None
    )
    application.state.expert_runs = (
        ExpertRunService(
            expert_gateway,
            application.state.research_os,
            expert_run_root
            or Path(os.getenv("EGO_ARTIFACT_ROOT", "artifacts/runtime")),
        )
        if expert_gateway is not None
        else ExpertRunService.from_environment(
            application.state.research_os,
            artifact_root=expert_run_root,
        )
    )

    default_origins = ",".join(
        [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ]
    )
    origins = [
        origin.strip()
        for origin in os.getenv("EGO_CORS_ORIGINS", default_origins).split(",")
        if origin.strip()
    ]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID", APPROVAL_TOKEN_HEADER],
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next: Callable[..., Any]) -> Any:
        supplied = request.headers.get("X-Request-ID", "")
        request.state.request_id = supplied[:128] if supplied else "req_%s" % uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @application.exception_handler(ControlPlaneError)
    async def control_plane_error(request: Request, error: ControlPlaneError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None
        return JSONResponse(
            status_code=error.status_code,
            content=_error_payload(request, error.code, error.message, error.details),
            headers=headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_payload(
                request,
                "invalid_request",
                "Request validation failed",
                {"violations": jsonable_encoder(error.errors())},
            ),
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if error.status_code == 404 else "http_error"
        return JSONResponse(
            status_code=error.status_code,
            content=_error_payload(request, code, str(error.detail), {}),
        )

    register_trusted_memory_focus_routes(application)
    register_research_os_routes(application)

    @application.get("/api/v1/health", tags=["system"])
    def health(request: Request) -> Dict[str, Any]:
        payload = request.app.state.service.health()
        payload["operator_auth"] = request.app.state.operator_auth.status()
        return payload

    @application.get("/api/v1/integrations", tags=["system"])
    def integrations(request: Request) -> Dict[str, Any]:
        return request.app.state.service.integrations()

    @application.get("/api/v1/expert-runs/status", tags=["research-os", "system"])
    def expert_run_status(request: Request) -> Dict[str, Any]:
        return request.app.state.expert_runs.status()

    @application.post("/api/v1/expert-runs", tags=["research-os"], status_code=202)
    def create_expert_run(
        body: ExpertRunRequest,
        request: Request,
        background_tasks: BackgroundTasks,
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> Dict[str, Any]:
        request.app.state.operator_auth.authenticate(authorization)
        run = request.app.state.expert_runs.create(body)
        background_tasks.add_task(request.app.state.expert_runs.execute, run["run_id"])
        return run

    @application.get("/api/v1/expert-runs/{run_id}", tags=["research-os"])
    def expert_run(
        run_id: str,
        request: Request,
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> Dict[str, Any]:
        request.app.state.operator_auth.authenticate(authorization)
        return request.app.state.expert_runs.get(run_id)

    @application.get("/api/v1/skills", tags=["skills"])
    def skills(request: Request) -> Dict[str, Any]:
        items = list(request.app.state.skill_registry.catalog())
        return {
            "items": items,
            "total": len(items),
            "executable": sum(bool(item["executable"]) for item in items),
            "truth_boundary": (
                "Discovery is not execution. Only allowlisted deterministic handlers can be "
                "invoked here; SafeRunner remains behind its dedicated approval path."
            ),
        }

    @application.get("/api/v1/skill-invocations/{invocation_id}", tags=["skills"])
    def skill_trace(invocation_id: str, request: Request) -> Dict[str, Any]:
        try:
            return request.app.state.skill_registry.trace(invocation_id)
        except KeyError as error:
            raise ControlPlaneError("skill_trace_not_found", str(error), 404) from error

    @application.post("/api/v1/skills/{name}/invoke", tags=["skills"])
    def skill_invoke(
        name: str,
        body: SkillInvokeRequest,
        request: Request,
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> Dict[str, Any]:
        request.app.state.operator_auth.authenticate(authorization)
        return invoke_skill(request.app.state.skill_registry, name, body)

    @application.get("/api/v1/rxp/schemas", tags=["rxp"])
    def rxp_schemas() -> Dict[str, Any]:
        return schema_catalog()

    @application.get("/api/v1/rxp/demo", tags=["rxp", "demo"])
    def rxp_demo() -> Dict[str, Any]:
        return demo_ledger()

    @application.post("/api/v1/rxp/verify", tags=["rxp"])
    def rxp_verify(body: RXPVerifyRequest) -> Dict[str, Any]:
        try:
            return verify_uploaded_ledger(body.ledger)
        except RXPError as error:
            raise ControlPlaneError(
                "rxp_%s" % error.code,
                error.message,
                422,
                error.details,
            ) from error

    @application.get("/api/v1/dashboard", tags=["research"])
    def dashboard(request: Request) -> Dict[str, Any]:
        return request.app.state.service.dashboard()

    @application.get("/api/v1/tasks", tags=["research"])
    def tasks(request: Request) -> Dict[str, Any]:
        items = request.app.state.service.list_tasks()
        return {"items": items, "total": len(items)}

    @application.post("/api/v1/tasks", tags=["research"], status_code=201)
    def create_task(
        body: CreateTaskRequest,
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> Dict[str, Any]:
        request.app.state.operator_auth.authenticate(authorization)
        key = _idempotency_key(idempotency_key, body.idempotency_key)
        body_json = body.model_dump(mode="json", exclude={"idempotency_key"})
        return _run_idempotent(
            request.app.state.service,
            "POST",
            "/api/v1/tasks",
            key,
            body_json,
            lambda: request.app.state.service.create_live_task(body),
        )

    @application.get("/api/v1/tasks/{task_id}", tags=["research"])
    def task(task_id: str, request: Request) -> Dict[str, Any]:
        return request.app.state.service.get_task(task_id)

    @application.post("/api/v1/demo/reset", tags=["demo"])
    def reset_demo(
        request: Request,
        body: Optional[DemoResetRequest] = None,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> Dict[str, Any]:
        request.app.state.operator_auth.authorize_demo_or_operator(authorization)
        payload = body or DemoResetRequest()
        key = _idempotency_key(idempotency_key, payload.idempotency_key)
        body_json = payload.model_dump(mode="json", exclude={"idempotency_key"})
        return _run_idempotent(
            request.app.state.service,
            "POST",
            "/api/v1/demo/reset",
            key,
            body_json,
            lambda: request.app.state.service.reset_demo(payload.scenario),
        )

    @application.post("/api/v1/tasks/{task_id}/advance", tags=["research"])
    def advance_task(
        task_id: str,
        request: Request,
        body: Optional[AdvanceRequest] = None,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> Dict[str, Any]:
        task_record = request.app.state.service.store.get_task(task_id)
        if task_record.synthetic_demo:
            request.app.state.operator_auth.authorize_demo_or_operator(authorization)
        else:
            request.app.state.operator_auth.authenticate(authorization)
        payload = body or AdvanceRequest()
        key = _idempotency_key(idempotency_key, payload.idempotency_key)
        body_json = payload.model_dump(mode="json", exclude={"idempotency_key"})
        return _run_idempotent(
            request.app.state.service,
            "POST",
            "/api/v1/tasks/%s/advance" % task_id,
            key,
            body_json,
            lambda: request.app.state.service.advance(
                task_id, payload.target, payload.approval_token
            ),
        )

    @application.post("/api/v1/tasks/{task_id}/autorun", tags=["research"])
    def autorun_task(
        task_id: str,
        request: Request,
        body: Optional[AutorunRequest] = None,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> Dict[str, Any]:
        task_record = request.app.state.service.store.get_task(task_id)
        if task_record.synthetic_demo:
            request.app.state.operator_auth.authorize_demo_or_operator(authorization)
        else:
            request.app.state.operator_auth.authenticate(authorization)
        payload = body or AutorunRequest()
        key = _idempotency_key(idempotency_key, payload.idempotency_key)
        body_json = payload.model_dump(mode="json", exclude={"idempotency_key"})
        return _run_idempotent(
            request.app.state.service,
            "POST",
            "/api/v1/tasks/%s/autorun" % task_id,
            key,
            body_json,
            lambda: request.app.state.service.autorun(task_id, payload.approval_token),
        )

    @application.post("/api/v1/tasks/{task_id}/evidence", tags=["research", "evidence"])
    def ingest_evidence(
        task_id: str,
        body: EvidenceIngestRequest,
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> Dict[str, Any]:
        request.app.state.operator_auth.authenticate(authorization)
        key = _idempotency_key(idempotency_key, body.idempotency_key)
        body_json = body.model_dump(mode="json", exclude={"idempotency_key"}, by_alias=True)
        return _run_idempotent(
            request.app.state.service,
            "POST",
            "/api/v1/tasks/%s/evidence" % task_id,
            key,
            body_json,
            lambda: request.app.state.service.ingest_live_evidence(
                task_id, body.evidence, body.expected_task_version
            ),
        )

    @application.post("/api/v1/tasks/{task_id}/finalize", tags=["research", "evidence"])
    def finalize_task(
        task_id: str,
        body: FinalizeTaskRequest,
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> Dict[str, Any]:
        request.app.state.operator_auth.authenticate(authorization)
        key = _idempotency_key(idempotency_key, body.idempotency_key)
        body_json = body.model_dump(mode="json", exclude={"idempotency_key"}, by_alias=True)
        return _run_idempotent(
            request.app.state.service,
            "POST",
            "/api/v1/tasks/%s/finalize" % task_id,
            key,
            body_json,
            lambda: request.app.state.service.finalize_live_task(task_id, body),
        )

    @application.post("/api/v1/approvals/{approval_id}/decision", tags=["approval"])
    def approval_decision(
        approval_id: str,
        body: ApprovalDecisionRequest,
        request: Request,
        response: Response,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> Dict[str, Any]:
        approval = request.app.state.service.store.get_approval(approval_id)
        task_record = request.app.state.service.store.get_task(approval.task_id)
        identity: OperatorIdentity
        if task_record.synthetic_demo:
            identity = request.app.state.operator_auth.authorize_demo_or_operator(
                authorization
            )
        else:
            identity = request.app.state.operator_auth.authenticate(authorization)
        if body.approver is not None and body.approver != identity.id:
            raise ControlPlaneError(
                "approver_identity_mismatch",
                "The asserted approver does not match the authenticated operator identity",
                403,
            )
        key = _idempotency_key(idempotency_key, None)
        body_json = {
            "decision": body.decision.value,
            "approver": identity.id,
            "expected_digest": body.expected_digest,
        }
        result = _run_idempotent(
            request.app.state.service,
            "POST",
            "/api/v1/approvals/%s/decision" % approval_id,
            key,
            body_json,
            lambda: request.app.state.service.decide_approval(
                approval_id, body.decision.value, identity.id, body.expected_digest
            ),
            cache_response=_redact_approval_replay,
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Vary"] = "Authorization"
        if not task_record.synthetic_demo:
            raw_token = result.pop("approval_token", None)
            result["approval_token"] = None
            if raw_token:
                response.headers[APPROVAL_TOKEN_HEADER] = str(raw_token)
                result["token_notice"] = (
                    "One-time live token delivered only in %s; it is omitted from JSON and "
                    "idempotent replays." % APPROVAL_TOKEN_HEADER
                )
        return result

    @application.get("/api/v1/tasks/{task_id}/events", tags=["audit"])
    def task_events(
        task_id: str,
        request: Request,
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> Dict[str, Any]:
        return request.app.state.service.events(task_id, after_sequence, limit)

    @application.get("/api/v1/tasks/{task_id}/event-stream", tags=["audit"])
    def task_event_stream(
        task_id: str,
        request: Request,
        after_sequence: int = Query(default=0, ge=0),
        follow: bool = Query(default=True),
        heartbeat_seconds: float = Query(default=15.0, ge=0.05, le=60.0),
        last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        # Resolve the task before constructing a streaming response so a missing task
        # remains a normal structured 404 instead of a late iterator failure.
        request.app.state.service.store.get_task(task_id)
        stream = iter_task_events(
            request.app.state.service,
            task_id,
            cursor=last_event_id,
            after_sequence=after_sequence,
            follow=follow,
            heartbeat_seconds=heartbeat_seconds,
        )
        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "X-Ego-Event-Mode": (
                    "postgres-listen-notify-durable-replay"
                    if request.app.state.service.store.engine == "postgresql"
                    else "sqlite-cursor-fallback"
                ),
            },
        )

    return application


app = create_app()
