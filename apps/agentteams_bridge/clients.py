"""Clients for the official AgentTeams, Matrix, and EgoAgentOS APIs."""

from __future__ import annotations

import json
import hashlib
import urllib.parse
import uuid
from typing import Any, Dict, List, Mapping, Optional

from .errors import BridgeError, LiveAgentTeamsUnavailable, UpstreamError
from .models import (
    ProjectSpawns,
    SpawnMessages,
    TeamResponse,
    WorkerResponse,
    WorkflowResponse,
    canonical_sha256,
)
from .transport import HTTPResponse, HTTPTransport, TransportFailure, UrllibTransport


class JSONClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        timeout: float = 15.0,
        transport: Optional[HTTPTransport] = None,
        upstream_name: str,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.transport = transport or UrllibTransport()
        self.upstream_name = upstream_name

    def _headers(self, extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        headers = dict(extra or {})
        if self.token:
            headers["Authorization"] = "Bearer %s" % self.token
        return headers

    def request_raw(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        expected: tuple[int, ...] = (200,),
        operation: Optional[str] = None,
    ) -> HTTPResponse:
        try:
            response = self.transport.request(
                method,
                "%s%s" % (self.base_url, path),
                headers=self._headers(headers),
                json_body=body,
                timeout=self.timeout,
            )
        except TransportFailure as error:
            raise UpstreamError(
                self.upstream_name,
                operation or "%s %s" % (method, path),
                503,
                "transport unavailable",
                body={"error_type": type(error).__name__},
            ) from error
        if response.status not in expected:
            try:
                response_body = response.json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                response_body = response.text()[:1000]
            message = "unexpected upstream response"
            if isinstance(response_body, dict):
                message = str(
                    response_body.get("error")
                    or response_body.get("message")
                    or response_body
                )
            elif response_body:
                message = str(response_body)
            raise UpstreamError(
                self.upstream_name,
                operation or "%s %s" % (method, path),
                response.status,
                message,
                body=response_body,
            )
        return response

    def request_json(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        expected: tuple[int, ...] = (200,),
        operation: Optional[str] = None,
    ) -> Any:
        response = self.request_raw(
            method,
            path,
            body=body,
            headers=headers,
            expected=expected,
            operation=operation,
        )
        try:
            return response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise BridgeError(
                "%s_malformed_response" % self.upstream_name,
                "%s returned a non-JSON success response" % self.upstream_name,
                status_code=502,
                retryable=False,
                details={
                    "operation": operation or "%s %s" % (method, path),
                    "content_type": response.headers.get("content-type"),
                },
            ) from error

    def request_json_with_receipt(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        expected: tuple[int, ...] = (200,),
        operation: Optional[str] = None,
        response_identifier: Optional[str] = None,
    ) -> tuple[Any, Dict[str, Any]]:
        """Return parsed JSON plus a secret-free, byte-digest-bound HTTP receipt."""

        operation_name = operation or "%s %s" % (method, path)
        response = self.request_raw(
            method,
            path,
            body=body,
            headers=headers,
            expected=expected,
            operation=operation_name,
        )
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise BridgeError(
                "%s_malformed_response" % self.upstream_name,
                "%s returned a non-JSON success response" % self.upstream_name,
                status_code=502,
                details={"operation": operation_name},
            ) from error
        identifier = response_identifier
        if identifier is None and isinstance(payload, dict):
            for key in ("event_id", "project_id", "id", "request_id"):
                if payload.get(key):
                    identifier = str(payload[key])
                    break
        receipt = {
            "schema": "egoagentos.upstream-http-receipt/v1",
            "source": self.upstream_name,
            "operation": operation_name,
            "method": method,
            "endpoint": path,
            "http_status": response.status,
            "request_body": body,
            "request_sha256": canonical_sha256(body),
            "response_body": payload,
            "response_sha256": hashlib.sha256(response.body).hexdigest(),
            "response_identifier": identifier,
            "response_headers": {
                key.lower(): value
                for key, value in response.headers.items()
                if key.lower() in {"content-type", "etag", "x-request-id"}
            },
        }
        return payload, receipt


class AgentTeamsClient(JSONClient):
    """Official Controller REST client pinned by integrations/agentteams lock."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        timeout: float = 15.0,
        transport: Optional[HTTPTransport] = None,
    ) -> None:
        super().__init__(
            base_url,
            token=token,
            timeout=timeout,
            transport=transport,
            upstream_name="agentteams",
        )

    @staticmethod
    def _quote(value: str) -> str:
        return urllib.parse.quote(value, safe="")

    @staticmethod
    def _team_query(team: str, *, include_tasks: bool = False) -> str:
        params: Dict[str, str] = {"team": team}
        if include_tasks:
            params["includeTasks"] = "true"
        return urllib.parse.urlencode(params)

    def health(self) -> bool:
        response = self.request_raw("GET", "/healthz", expected=(200,), operation="health")
        return response.text().strip() == "ok"

    def version(self) -> Dict[str, Any]:
        payload = self.request_json("GET", "/api/v1/version", operation="version")
        if not isinstance(payload, dict):
            raise LiveAgentTeamsUnavailable("AgentTeams version response is not an object")
        return payload

    def probe_project_api(self) -> Dict[str, Any]:
        payload = self.request_json("GET", "/api/v1/projects", operation="project-api-probe")
        if not isinstance(payload, dict) or "projects" not in payload:
            raise LiveAgentTeamsUnavailable(
                "Controller does not expose the pinned project workflow API",
                details={"required": "GET /api/v1/projects"},
            )
        return payload

    def get_team(self, team: str) -> TeamResponse:
        return TeamResponse.model_validate(
            self.request_json(
                "GET", "/api/v1/teams/%s" % self._quote(team), operation="get-team"
            )
        )

    def get_worker(self, worker: str) -> WorkerResponse:
        return WorkerResponse.model_validate(
            self.request_json(
                "GET", "/api/v1/workers/%s" % self._quote(worker), operation="get-worker"
            )
        )

    def ensure_worker_ready(self, worker: str) -> WorkerResponse:
        payload = self.request_json(
            "POST",
            "/api/v1/workers/%s/ensure-ready" % self._quote(worker),
            expected=(200, 202),
            operation="ensure-worker-ready",
        )
        # The lifecycle response can be minimal, so fetch the full record after wakeup.
        if not isinstance(payload, dict):
            raise LiveAgentTeamsUnavailable("Worker readiness response is malformed")
        return self.get_worker(worker)

    def create_project(
        self,
        *,
        project_id: str,
        title: str,
        team: str,
        requester: str,
        source_room_id: str,
    ) -> Dict[str, Any]:
        payload, _ = self.create_project_with_receipt(
            project_id=project_id,
            title=title,
            team=team,
            requester=requester,
            source_room_id=source_room_id,
        )
        return payload

    def create_project_with_receipt(
        self,
        *,
        project_id: str,
        title: str,
        team: str,
        requester: str,
        source_room_id: str,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        payload, receipt = self.request_json_with_receipt(
            "POST",
            "/api/v1/projects",
            body={
                "title": title,
                "source": "egoagentos-bridge",
                "requester": requester,
                "team_id": team,
                "project_id": project_id,
                "source_room_id": source_room_id,
            },
            expected=(201,),
            operation="create-project",
            response_identifier=project_id,
        )
        if not isinstance(payload, dict):
            raise BridgeError(
                "agentteams_malformed_response",
                "AgentTeams create-project response is not an object",
                status_code=502,
            )
        return payload, receipt

    def workflow(self, project_id: str, team: str) -> WorkflowResponse:
        workflow, _ = self.workflow_with_receipt(project_id, team)
        return workflow

    def workflow_with_receipt(
        self, project_id: str, team: str
    ) -> tuple[WorkflowResponse, Dict[str, Any]]:
        path = "/api/v1/projects/%s/workflow?%s" % (
            self._quote(project_id),
            self._team_query(team, include_tasks=True),
        )
        payload, receipt = self.request_json_with_receipt(
            "GET",
            path,
            operation="get-workflow",
            response_identifier=project_id,
        )
        return WorkflowResponse.model_validate(payload), receipt

    def pause(self, project_id: str, team: str, reason: str) -> WorkflowResponse:
        path = "/api/v1/projects/%s/pause?%s" % (
            self._quote(project_id),
            self._team_query(team),
        )
        return WorkflowResponse.model_validate(
            self.request_json("POST", path, body={"reason": reason}, operation="pause-project")
        )

    def resume(self, project_id: str, team: str) -> WorkflowResponse:
        path = "/api/v1/projects/%s/resume?%s" % (
            self._quote(project_id),
            self._team_query(team),
        )
        return WorkflowResponse.model_validate(
            self.request_json("POST", path, operation="resume-project")
        )

    def replan(
        self, project_id: str, team: str, tasks: List[Dict[str, Any]]
    ) -> WorkflowResponse:
        path = "/api/v1/projects/%s/replan?%s" % (
            self._quote(project_id),
            self._team_query(team),
        )
        return WorkflowResponse.model_validate(
            self.request_json(
                "POST", path, body={"tasks": tasks}, operation="replan-project"
            )
        )

    def cancel_task(
        self,
        project_id: str,
        team: str,
        task_id: str,
        reason: str,
        replacement_task_id: str,
    ) -> WorkflowResponse:
        path = "/api/v1/projects/%s/tasks/%s/cancel?%s" % (
            self._quote(project_id),
            self._quote(task_id),
            self._team_query(team),
        )
        return WorkflowResponse.model_validate(
            self.request_json(
                "POST",
                path,
                body={"reason": reason, "replacementTaskId": replacement_task_id},
                operation="cancel-task",
            )
        )

    def complete(self, project_id: str, team: str) -> WorkflowResponse:
        workflow, _ = self.complete_with_receipt(project_id, team)
        return workflow

    def complete_with_receipt(
        self, project_id: str, team: str
    ) -> tuple[WorkflowResponse, Dict[str, Any]]:
        path = "/api/v1/projects/%s/complete?%s" % (
            self._quote(project_id),
            self._team_query(team),
        )
        payload, receipt = self.request_json_with_receipt(
            "POST",
            path,
            operation="complete-project",
            response_identifier=project_id,
        )
        return WorkflowResponse.model_validate(payload), receipt

    def task_artifact(
        self, project_id: str, team: str, task_id: str, path: str
    ) -> bytes:
        query = urllib.parse.urlencode({"team": team, "path": path})
        response = self.request_raw(
            "GET",
            "/api/v1/projects/%s/tasks/%s/artifact?%s"
            % (self._quote(project_id), self._quote(task_id), query),
            operation="get-task-artifact",
        )
        return response.body

    def task_artifact_with_receipt(
        self, project_id: str, team: str, task_id: str, path: str
    ) -> tuple[bytes, Dict[str, Any]]:
        query = urllib.parse.urlencode({"team": team, "path": path})
        endpoint = "/api/v1/projects/%s/tasks/%s/artifact?%s" % (
            self._quote(project_id),
            self._quote(task_id),
            query,
        )
        response = self.request_raw(
            "GET", endpoint, operation="get-task-artifact"
        )
        try:
            response_body = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            response_body = None
        receipt = {
            "schema": "egoagentos.upstream-http-receipt/v1",
            "source": "agentteams",
            "operation": "get-task-artifact",
            "method": "GET",
            "endpoint": endpoint,
            "http_status": response.status,
            "request_body": None,
            "request_sha256": canonical_sha256(None),
            "response_body": response_body,
            "response_sha256": hashlib.sha256(response.body).hexdigest(),
            "response_identifier": "%s:%s:%s" % (project_id, task_id, path),
            "response_headers": {
                key.lower(): value
                for key, value in response.headers.items()
                if key.lower() in {"content-type", "etag", "x-request-id"}
            },
        }
        return response.body, receipt

    def spawns(self, project_id: str, team: str) -> ProjectSpawns:
        path = "/api/v1/projects/%s/spawns?%s" % (
            self._quote(project_id),
            self._team_query(team),
        )
        return ProjectSpawns.model_validate(
            self.request_json("GET", path, operation="get-project-spawns")
        )

    def spawn_messages(
        self, project_id: str, team: str, session_id: str, limit: int = 50
    ) -> SpawnMessages:
        query = urllib.parse.urlencode({"team": team, "limit": str(limit)})
        path = "/api/v1/projects/%s/spawns/%s/messages?%s" % (
            self._quote(project_id),
            self._quote(session_id),
            query,
        )
        return SpawnMessages.model_validate(
            self.request_json("GET", path, operation="get-spawn-messages")
        )


class MatrixClient(JSONClient):
    def __init__(
        self,
        base_url: str,
        *,
        access_token: str,
        timeout: float = 15.0,
        transport: Optional[HTTPTransport] = None,
    ) -> None:
        super().__init__(
            base_url,
            token=access_token,
            timeout=timeout,
            transport=transport,
            upstream_name="matrix",
        )

    def whoami(self) -> Dict[str, Any]:
        payload = self.request_json(
            "GET", "/_matrix/client/v3/account/whoami", operation="matrix-whoami"
        )
        if not isinstance(payload, dict) or not payload.get("user_id"):
            raise LiveAgentTeamsUnavailable(
                "Matrix token did not resolve to a user_id",
                details={"required": "GET /_matrix/client/v3/account/whoami"},
            )
        return payload

    def send_envelope(
        self,
        *,
        room_id: str,
        leader_matrix_id: str,
        envelope: Dict[str, Any],
        transaction_id: Optional[str] = None,
    ) -> str:
        event_id, _ = self.send_envelope_with_receipt(
            room_id=room_id,
            leader_matrix_id=leader_matrix_id,
            envelope=envelope,
            transaction_id=transaction_id,
        )
        return event_id

    def send_envelope_with_receipt(
        self,
        *,
        room_id: str,
        leader_matrix_id: str,
        envelope: Dict[str, Any],
        transaction_id: Optional[str] = None,
    ) -> tuple[str, Dict[str, Any]]:
        txn_id = transaction_id or "ego-%s" % uuid.uuid4().hex
        quoted_room = urllib.parse.quote(room_id, safe="")
        quoted_txn = urllib.parse.quote(txn_id, safe="")
        body = (
            "%s PROJECT_REQUESTED: %s\n"
            "Use TeamHarness projectflow/taskflow for real delegation, ACK, submission, "
            "and acceptance. Structured envelope is attached as com.egoagentos.envelope."
            % (leader_matrix_id, envelope["project_id"])
        )
        request_body = {
            "msgtype": "m.text",
            "body": body,
            "m.mentions": {"user_ids": [leader_matrix_id]},
            "com.egoagentos.envelope": envelope,
        }
        payload, receipt = self.request_json_with_receipt(
            "PUT",
            "/_matrix/client/v3/rooms/%s/send/m.room.message/%s"
            % (quoted_room, quoted_txn),
            body=request_body,
            operation="send-envelope",
        )
        if not isinstance(payload, dict) or not payload.get("event_id"):
            raise BridgeError(
                "matrix_event_missing",
                "Matrix accepted the request without returning an event_id",
                status_code=502,
                retryable=True,
            )
        receipt["matrix_room_id"] = room_id
        receipt["matrix_transaction_id"] = txn_id
        receipt["matrix_event_id"] = str(payload["event_id"])
        return str(payload["event_id"]), receipt


class EgoClient(JSONClient):
    def __init__(
        self,
        base_url: str,
        *,
        operator_key: str = "",
        timeout: float = 15.0,
        transport: Optional[HTTPTransport] = None,
    ) -> None:
        super().__init__(
            base_url,
            token=operator_key,
            timeout=timeout,
            transport=transport,
            upstream_name="egoagentos",
        )

    def health(self) -> Dict[str, Any]:
        payload = self.request_json("GET", "/api/v1/health", operation="health")
        if not isinstance(payload, dict):
            raise LiveAgentTeamsUnavailable("EgoAgentOS health response is malformed")
        return payload

    def get_task(self, task_id: str) -> Dict[str, Any]:
        payload = self.request_json(
            "GET",
            "/api/v1/tasks/%s" % urllib.parse.quote(task_id, safe=""),
            operation="get-task",
        )
        if not isinstance(payload, dict):
            raise BridgeError("ego_task_malformed", "EgoAgentOS task response is malformed")
        return payload

    def consume_r2_grant(
        self,
        task_id: str,
        approval_token: str,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        payload = self.request_json(
            "POST",
            "/api/v1/tasks/%s/advance" % urllib.parse.quote(task_id, safe=""),
            body={"target": "EXECUTE", "approval_token": approval_token},
            headers={"Idempotency-Key": idempotency_key},
            operation="consume-r2-grant",
        )
        if not isinstance(payload, dict):
            raise BridgeError("ego_grant_malformed", "EgoAgentOS grant response is malformed")
        return payload

    def advance_stage(
        self, task_id: str, target: str, idempotency_key: str
    ) -> Dict[str, Any]:
        payload, _ = self.advance_stage_with_receipt(task_id, target, idempotency_key)
        return payload

    def advance_stage_with_receipt(
        self, task_id: str, target: str, idempotency_key: str
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        payload, receipt = self.request_json_with_receipt(
            "POST",
            "/api/v1/tasks/%s/advance" % urllib.parse.quote(task_id, safe=""),
            body={"target": target},
            headers={"Idempotency-Key": idempotency_key},
            operation="advance-live-stage",
            response_identifier="%s:%s" % (task_id, target),
        )
        if not isinstance(payload, dict):
            raise BridgeError("ego_advance_malformed", "EgoAgentOS advance response is malformed")
        return payload, receipt

    def finalize_live(
        self, task_id: str, body: Dict[str, Any], idempotency_key: str
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        path = "/api/v1/tasks/%s/finalize" % urllib.parse.quote(task_id, safe="")
        payload, receipt = self.request_json_with_receipt(
            "POST",
            path,
            body=body,
            headers={"Idempotency-Key": idempotency_key},
            operation="finalize-live-task",
            response_identifier="%s:%s" % (task_id, body.get("generation", "unknown")),
        )
        if not isinstance(payload, dict):
            raise BridgeError(
                "ego_finalization_malformed", "EgoAgentOS finalization response is malformed"
            )
        return payload, receipt
