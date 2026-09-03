#!/usr/bin/env python3
"""Deploy and verify the local official AgentTeams + EgoAgentOS live stack.

Secret values are accepted through a masked prompt or an existing process
environment, persisted only below the gitignored ``.runtime`` directory with
mode 0600, and never printed by this wrapper.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
PRIVATE_ENV = RUNTIME / "live-stack.env"
OFFICIAL_ENV = RUNTIME / "agentteams-manager.env"
OFFICIAL_ROOT = RUNTIME / "AgentTeams"
OFFICIAL_REPOSITORY = "https://github.com/agentscope-ai/AgentTeams.git"
OFFICIAL_TAG = "v1.2.3"
OFFICIAL_COMMIT = "223ddc2b8073e4c8b93bcbb15e1d717f196c04d9"
BASE_RUNTIME_IMAGE = "python:3.11-slim"
INSTALL_LOG = RUNTIME / "agentteams-bootstrap.log"
APPLY_LOG = RUNTIME / "agentteams-apply.log"
PUBLIC_MANIFEST = RUNTIME / "live-stack-public.json"
RENDERED_RESOURCES = RUNTIME / "agentteams-resources.yaml"
RESOURCE_TEMPLATE = ROOT / "integrations/agentteams/local-ready-team.yaml.tmpl"
WORKFLOW_TEMPLATE = ROOT / "integrations/agentteams/readiness-workflow.json"

CONTROLLER_URL = "http://127.0.0.1:18090"
CONTROLLER_DOCKER_URL = "http://agentteams-controller:8090"
MATRIX_URL = "http://127.0.0.1:18080"
MATRIX_DOCKER_URL = "http://agentteams-controller:6167"
EGO_API_URL = "http://127.0.0.1:8000"
BRIDGE_URL = "http://127.0.0.1:8020"
MODEL_BASE_URL = os.getenv("EGO_AGENT_MODEL_BASE_URL", "https://api.deepseek.com").strip()
MODEL_NAME = os.getenv("EGO_AGENT_MODEL", "deepseek-v4-flash").strip()
TEAM_NAME = "ego-researchops"
HUMAN_NAME = "ego-judge"
PROJECT_ID = "egoagentos-gpu-gated-v1"
GPU_PAUSE_REASON = "GPU Worker intentionally not attached yet"
WORKERS = (
    "ego-research-lead",
    "ego-architect",
    "ego-reviewer",
    "ego-memory-curator",
)
DIRECT_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class DeploymentError(RuntimeError):
    """An actionable, secret-free deployment failure."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def _validate_env_value(name: str, value: str) -> None:
    if not value:
        raise DeploymentError("%s must not be empty" % name)
    if any(character in value for character in ("\n", "\r", "\0")):
        raise DeploymentError("%s contains an unsafe control character" % name)


def _read_env(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value
    return values


def _write_private_env(path: Path, values: Mapping[str, str]) -> None:
    _private_directory(path.parent)
    for name, value in values.items():
        _validate_env_value(name, value)
    payload = "# Generated locally; never commit or paste this file.\n"
    payload += "\n".join("%s=%s" % item for item in sorted(values.items())) + "\n"
    temporary = path.with_name(".%s.%s.tmp" % (path.name, secrets.token_hex(8)))
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            encoded = payload.encode("utf-8")
            written = 0
            while written < len(encoded):
                chunk_size = os.write(descriptor, encoded[written:])
                if chunk_size <= 0:
                    raise OSError("private env write made no progress")
                written += chunk_size
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _secret() -> str:
    return secrets.token_hex(32)


def _ensure_private_env() -> Dict[str, str]:
    values = _read_env(PRIVATE_ENV)
    model_key = values.get("AGENTTEAMS_LLM_API_KEY") or os.getenv("AGENTTEAMS_LLM_API_KEY", "")
    if not model_key:
        model_key = getpass.getpass("DeepSeek/OpenAI-compatible API key (hidden): ").strip()
    _validate_env_value("AGENTTEAMS_LLM_API_KEY", model_key)

    generated = {
        "AGENTTEAMS_ADMIN_PASSWORD": _secret(),
        "EGO_POSTGRES_PASSWORD": _secret(),
        "EGO_RUNTIME_PASSWORD": _secret(),
        "EGO_AGENTTEAMS_RUNTIME_PASSWORD": _secret(),
        "EGO_OPERATOR_KEY": _secret(),
        "EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY": _secret(),
        "EGO_MCP_APPROVAL_HMAC_SECRET": _secret(),
        "EGO_TRUSTED_MEMORY_SERVICE_TOKEN": _secret(),
    }
    for name, value in generated.items():
        values.setdefault(name, value)

    values.update(
        {
            "AGENTTEAMS_LLM_API_KEY": model_key,
            "AGENTTEAMS_CONTROLLER_URL": CONTROLLER_DOCKER_URL,
            "AGENTTEAMS_CONTROLLER_PORT": "18090",
            "AGENTTEAMS_MATRIX_URL": MATRIX_DOCKER_URL,
            "AGENTTEAMS_MODEL": MODEL_NAME,
            "EGO_AGENT_MODEL_BASE_URL": MODEL_BASE_URL,
            "EGO_AGENT_MODEL_API_KEY": model_key,
            "EGO_AGENT_MODEL": MODEL_NAME,
            "EGO_POSTGRES_DB": "egoagentos",
            "EGO_POSTGRES_USER": "egoagentos_owner",
            "EGO_POSTGRES_PORT": "5432",
            "EGO_RUNTIME_USER": "egoagentos_api_runtime",
            "EGO_AGENTTEAMS_RUNTIME_USER": "egoagentos_bridge_runtime_login",
            "EGO_API_PORT": "8000",
            "EGO_WEB_PORT": "4173",
            "EGO_AGENTTEAMS_BRIDGE_PORT": "8020",
            "EGO_BASE_IMAGE_PULL_POLICY": "never",
            "EGO_TENANT_ID": "local-live",
            "EGO_OPERATOR_ID": "local.operator",
            "EGO_ALLOW_UNAUTHENTICATED_DEMO": "false",
            "EGO_FOCUS_MEMORY_MODE": "required",
            "EGO_CORS_ORIGINS": (
                "http://localhost:4173,http://127.0.0.1:4173,https://mythrise.github.io"
            ),
            "NO_PROXY": "127.0.0.1,localhost,agentteams-controller",
            "no_proxy": "127.0.0.1,localhost,agentteams-controller",
        }
    )

    independent = [values[name] for name in generated]
    if len(set(independent)) != len(independent):
        raise DeploymentError("generated operator and database secrets are not independent")
    if values["EGO_OPERATOR_KEY"] == values["EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY"]:
        raise DeploymentError("API and Bridge operator keys must be different")
    _write_private_env(PRIVATE_ENV, values)
    return values


def _run(
    command: Sequence[str],
    *,
    env: Optional[Mapping[str, str]] = None,
    input_bytes: Optional[bytes] = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        list(command),
        cwd=ROOT,
        env=dict(env) if env is not None else None,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise DeploymentError("command failed (%s): %s" % (command[0], error[-1200:]))
    return result


def _run_to_private_log(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    log_path: Path,
) -> None:
    _private_directory(log_path.parent)
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(descriptor, "ab", closefd=True) as stream:
            result = subprocess.run(
                list(command),
                cwd=ROOT,
                env=dict(env),
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=False,
            )
    finally:
        log_path.chmod(0o600)
    if result.returncode != 0:
        raise DeploymentError(
            "%s failed; inspect the protected log at %s" % (Path(command[0]).name, log_path)
        )


def _docker_names(*, running_only: bool = False) -> set[str]:
    command = ["docker", "ps"]
    if not running_only:
        command.append("-a")
    command.extend(["--format", "{{.Names}}"])
    output = _run(command).stdout.decode("utf-8")
    return {line.strip() for line in output.splitlines() if line.strip()}


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def _ensure_base_runtime_image() -> None:
    inspected = _run(
        ["docker", "image", "inspect", BASE_RUNTIME_IMAGE],
        check=False,
    )
    if inspected.returncode == 0:
        return
    print("[prepare] pulling the shared Python runtime image", flush=True)
    _run(["docker", "pull", BASE_RUNTIME_IMAGE])


def _ensure_official_checkout() -> None:
    if not OFFICIAL_ROOT.exists():
        print("[prepare] cloning official AgentTeams %s" % OFFICIAL_TAG, flush=True)
        _private_directory(RUNTIME)
        _run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                OFFICIAL_TAG,
                OFFICIAL_REPOSITORY,
                str(OFFICIAL_ROOT),
            ]
        )
    actual = (
        _run(["git", "-C", str(OFFICIAL_ROOT), "rev-parse", "HEAD"]).stdout.decode("utf-8").strip()
    )
    if actual != OFFICIAL_COMMIT:
        raise DeploymentError(
            "official checkout drift: expected %s, got %s" % (OFFICIAL_COMMIT, actual)
        )
    dirty = (
        _run(["git", "-C", str(OFFICIAL_ROOT), "status", "--porcelain"])
        .stdout.decode("utf-8")
        .strip()
    )
    if dirty:
        raise DeploymentError("official checkout has local modifications")


def _render_resources() -> None:
    template = RESOURCE_TEMPLATE.read_text(encoding="utf-8")
    rendered = template.replace("${AGENTTEAMS_MODEL}", MODEL_NAME)
    unresolved = re.findall(r"\$\{[A-Z][A-Z0-9_]*\}", rendered)
    if unresolved:
        raise DeploymentError("unresolved resource placeholders: %s" % unresolved)
    RENDERED_RESOURCES.write_text(rendered, encoding="utf-8")
    RENDERED_RESOURCES.chmod(0o644)


def prepare() -> None:
    if shutil.which("docker") is None:
        raise DeploymentError("Docker is required")
    _run(["docker", "info"])
    _private_directory(RUNTIME)
    _ensure_base_runtime_image()
    _ensure_official_checkout()
    _render_resources()
    for port in (5432, 8000, 8020, 18080, 18001, 18088, 18090, 18888):
        if not _port_available(port):
            name = "an existing official AgentTeams service" if port >= 18000 else "a process"
            print("[prepare] port %d is already used by %s" % (port, name), flush=True)
    print("[prepare] official contract pin and local resources are ready", flush=True)


def _official_install_env(private: Mapping[str, str]) -> Dict[str, str]:
    workspace = RUNTIME / "agentteams-manager"
    _private_directory(workspace)
    environment = dict(os.environ)
    environment.update(
        {
            # Docker Desktop stores its selected context below the real HOME.
            # Changing HOME here makes a healthy daemon look unavailable.
            "HOME": os.environ.get("HOME", str(Path.home())),
            "AGENTTEAMS_NON_INTERACTIVE": "1",
            "AGENTTEAMS_LANGUAGE": "en",
            "AGENTTEAMS_VERSION": OFFICIAL_TAG,
            "AGENTTEAMS_LLM_PROVIDER": "openai-compat",
            "AGENTTEAMS_OPENAI_BASE_URL": MODEL_BASE_URL,
            "AGENTTEAMS_LLM_API_KEY": private["AGENTTEAMS_LLM_API_KEY"],
            "AGENTTEAMS_DEFAULT_MODEL": MODEL_NAME,
            "AGENTTEAMS_EMBEDDING_MODEL": "",
            "AGENTTEAMS_ADMIN_USER": "ego-admin",
            "AGENTTEAMS_ADMIN_PASSWORD": private["AGENTTEAMS_ADMIN_PASSWORD"],
            "AGENTTEAMS_LOCAL_ONLY": "1",
            "AGENTTEAMS_PORT_GATEWAY": "18080",
            "AGENTTEAMS_PORT_CONSOLE": "18001",
            "AGENTTEAMS_PORT_ELEMENT_WEB": "18088",
            "AGENTTEAMS_PORT_MANAGER_CONSOLE": "18888",
            "AGENTTEAMS_MATRIX_DOMAIN": "matrix-local.agentteams.io:18080",
            "AGENTTEAMS_MATRIX_CLIENT_DOMAIN": "matrix-client-local.agentteams.io",
            "AGENTTEAMS_AI_GATEWAY_DOMAIN": "aigw-local.agentteams.io",
            "AGENTTEAMS_FS_DOMAIN": "fs-local.agentteams.io",
            "AGENTTEAMS_CONSOLE_DOMAIN": "console-local.agentteams.io",
            "AGENTTEAMS_MANAGER_RUNTIME": "qwenpaw",
            "AGENTTEAMS_DEFAULT_WORKER_RUNTIME": "qwenpaw",
            "AGENTTEAMS_MATRIX_E2EE": "0",
            "AGENTTEAMS_MOUNT_SOCKET": "1",
            "AGENTTEAMS_DOCKER_PROXY": "1",
            "AGENTTEAMS_DATA_DIR": "egoagentos-agentteams-data",
            "AGENTTEAMS_WORKSPACE_DIR": str(workspace),
            "AGENTTEAMS_HOST_SHARE_DIR": str(ROOT),
            "AGENTTEAMS_WORKER_IDLE_TIMEOUT": "720",
            "AGENTTEAMS_DASHBOARD": "0",
            "AGENTTEAMS_UPGRADE_KEEP_ALL": "1",
            "AGENTTEAMS_ENV_FILE": str(OFFICIAL_ENV),
            "AGENTTEAMS_TIMEZONE": "Asia/Shanghai",
        }
    )
    return environment


def _installer_for_current_docker_context() -> Tuple[Path, bool]:
    """Return the pristine installer, or a runtime-only Colima compatibility copy."""

    official = OFFICIAL_ROOT / "install/agentteams-install.sh"
    endpoint = (
        _run(
            [
                "docker",
                "context",
                "inspect",
                "--format",
                "{{.Endpoints.docker.Host}}",
                _run(["docker", "context", "show"]).stdout.decode("utf-8").strip(),
            ]
        )
        .stdout.decode("utf-8")
        .strip()
    )
    if ".colima/" not in endpoint:
        return official, False

    source = official.read_text(encoding="utf-8")
    marker = "detect_socket() {\n    local socket_path\n"
    if source.count(marker) != 1:
        raise DeploymentError("official installer socket probe changed; refusing blind patch")
    replacement = (
        marker
        + """
    # EgoAgentOS local compatibility: Docker commands use the host Colima
    # context, while bind-mount paths are resolved inside the Colima VM.
    if [ -n "${AGENTTEAMS_CONTAINER_SOCKET_SOURCE:-}" ]; then
        echo "${AGENTTEAMS_CONTAINER_SOCKET_SOURCE}"
        return 0
    fi
"""
    )
    patched = source.replace(marker, replacement, 1)
    target = RUNTIME / "agentteams-install-colima.sh"
    target.write_text(patched, encoding="utf-8")
    target.chmod(0o700)
    return target, True


def install_agentteams() -> None:
    private = _ensure_private_env()
    names = _docker_names()
    official = {name for name in names if name.startswith("agentteams-")}
    running = _docker_names(running_only=True)
    if official:
        if {"agentteams-controller", "agentteams-manager"}.issubset(running):
            print("[install] official Controller and Manager already running", flush=True)
            return
        failed_bootstrap = official == {"agentteams-controller"}
        if failed_bootstrap:
            state = (
                _run(
                    ["docker", "inspect", "--format", "{{.State.Status}}", "agentteams-controller"]
                )
                .stdout.decode("utf-8")
                .strip()
            )
            failed_bootstrap = state == "created"
        if not failed_bootstrap:
            raise DeploymentError(
                "partial/stopped AgentTeams containers found; refusing automatic replacement: %s"
                % ", ".join(sorted(official))
            )
        print(
            "[install] replacing the failed Created-only Controller; data volume is preserved",
            flush=True,
        )
    conflicts = [port for port in (18080, 18001, 18088, 18888) if not _port_available(port)]
    if conflicts:
        raise DeploymentError("AgentTeams ports are occupied: %s" % conflicts)
    print("[install] starting the official v1.2.3 local Docker installer", flush=True)
    installer, colima_compat = _installer_for_current_docker_context()
    official_log = Path(os.environ.get("HOME", str(Path.home()))) / "agentteams-install.log"
    descriptor = os.open(official_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.close(descriptor)
    official_log.chmod(0o600)
    try:
        install_environment = _official_install_env(private)
        if colima_compat:
            install_environment["AGENTTEAMS_CONTAINER_SOCKET_SOURCE"] = "/var/run/docker.sock"
        _run_to_private_log(
            ["bash", str(installer), "manager"],
            env=install_environment,
            log_path=INSTALL_LOG,
        )
    finally:
        if official_log.exists():
            official_log.chmod(0o600)
    running = _docker_names(running_only=True)
    if not {"agentteams-controller", "agentteams-manager"}.issubset(running):
        raise DeploymentError("official installer exited without a running Controller and Manager")
    print("[install] official Controller and Manager are running", flush=True)


def _capture_controller_token(timeout: int = 120) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _run(
            [
                "docker",
                "exec",
                "agentteams-controller",
                "sh",
                "-c",
                (
                    "cat /var/run/agentteams/cli-token 2>/dev/null || "
                    "cat /var/run/hiclaw/cli-token 2>/dev/null"
                ),
            ],
            check=False,
        )
        token = result.stdout.decode("utf-8", errors="replace").strip()
        if result.returncode == 0 and token:
            return token
        time.sleep(2)
    raise DeploymentError("Controller service-account token was not minted within 120 seconds")


def _ensure_controller_proxy() -> None:
    print("[configure] exposing the official Controller on localhost:18090", flush=True)
    _run_to_private_log(
        [
            "docker",
            "compose",
            "--env-file",
            str(PRIVATE_ENV),
            "--profile",
            "agentteams",
            "up",
            "-d",
            "agentteams-controller-proxy",
        ],
        env=os.environ,
        log_path=RUNTIME / "controller-proxy.log",
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with DIRECT_HTTP.open(CONTROLLER_URL + "/healthz", timeout=5) as response:
                if response.status == 200 and response.read().strip() == b"ok":
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(2)
    raise DeploymentError("Controller proxy did not expose /healthz within 90 seconds")


def _json_request(
    base_url: str,
    path: str,
    *,
    token: str = "",
    method: str = "GET",
    body: Optional[Any] = None,
    expected: Iterable[int] = (200,),
) -> Tuple[int, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer %s" % token
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers=headers,
        method=method,
    )
    accepted = set(expected)
    try:
        with DIRECT_HTTP.open(request, timeout=20) as response:
            status = response.status
            payload_bytes = response.read()
    except urllib.error.HTTPError as error:
        status = error.code
        payload_bytes = error.read()
    if status not in accepted:
        message = payload_bytes.decode("utf-8", errors="replace")[-1000:]
        raise DeploymentError("HTTP %s %s returned %d: %s" % (method, path, status, message))
    if not payload_bytes:
        return status, {}
    try:
        return status, json.loads(payload_bytes)
    except json.JSONDecodeError as error:
        raise DeploymentError("HTTP %s returned malformed JSON" % path) from error


def _wait_json(
    path: str,
    token: str,
    predicate: Any,
    *,
    description: str,
    timeout: int = 720,
) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    announced = 0.0
    while time.monotonic() < deadline:
        try:
            _, last = _json_request(CONTROLLER_URL, path, token=token)
            if predicate(last):
                return last
        except DeploymentError as error:
            last = {"error": str(error)}
        if time.monotonic() - announced >= 15:
            print("[wait] %s" % description, flush=True)
            announced = time.monotonic()
        time.sleep(3)
    raise DeploymentError("timed out waiting for %s; last=%s" % (description, last))


def _apply_resources(controller_token: str) -> None:
    apply_script = OFFICIAL_ROOT / "install/agentteams-apply.sh"
    resource_file = RENDERED_RESOURCES
    human_status, _ = _json_request(
        CONTROLLER_URL,
        "/api/v1/humans/%s" % HUMAN_NAME,
        token=controller_token,
        expected=(200, 404),
    )
    if human_status == 200:
        documents = re.split(r"(?m)^---\s*$", RENDERED_RESOURCES.read_text(encoding="utf-8"))
        documents = [document.strip() for document in documents if "kind: Human" not in document]
        resource_file = RUNTIME / "agentteams-resources-existing-human.yaml"
        resource_file.write_text("\n---\n".join(documents) + "\n", encoding="utf-8")
        resource_file.chmod(0o644)
    environment = dict(os.environ)
    environment["AGENTTEAMS_ENV_FILE"] = str(OFFICIAL_ENV)
    _run_to_private_log(
        ["bash", str(apply_script), "-f", str(resource_file)],
        env=environment,
        log_path=APPLY_LOG,
    )


def _matrix_login(username: str, password: str) -> str:
    login = {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": username},
        "password": password,
    }
    result = _run(
        [
            "docker",
            "exec",
            "-i",
            "agentteams-controller",
            "curl",
            "-sf",
            "-X",
            "POST",
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            "@-",
            "http://127.0.0.1:6167/_matrix/client/v3/login",
        ],
        input_bytes=json.dumps(login).encode("utf-8"),
    )
    try:
        token = json.loads(result.stdout).get("access_token", "")
    except json.JSONDecodeError as error:
        raise DeploymentError("Matrix login returned malformed JSON") from error
    if not token:
        raise DeploymentError("Matrix login did not return an access token")
    return str(token)


def _matrix_join(room_id: str, token: str) -> None:
    quoted_room = urllib.parse.quote(room_id, safe="")
    _json_request(
        MATRIX_URL,
        "/_matrix/client/v3/join/%s" % quoted_room,
        token=token,
        method="POST",
        body={},
    )


def _workflow_tasks(controller_token: str) -> list[Dict[str, Any]]:
    matrix_ids: Dict[str, str] = {}
    for worker in WORKERS:
        _, payload = _json_request(
            CONTROLLER_URL, "/api/v1/workers/%s" % worker, token=controller_token
        )
        matrix_user_id = str(payload.get("matrixUserID", ""))
        if not matrix_user_id:
            raise DeploymentError("Worker %s has no Matrix identity" % worker)
        matrix_ids[worker] = matrix_user_id

    source = json.loads(WORKFLOW_TEMPLATE.read_text(encoding="utf-8"))
    tasks: list[Dict[str, Any]] = []
    for item in source:
        role = item["assignedRole"]
        if role not in matrix_ids:
            raise DeploymentError("workflow references unknown role %s" % role)
        tasks.append(
            {
                "taskId": item["taskId"],
                "title": item["title"],
                "assignedTo": matrix_ids[role],
                "dependsOn": item["dependsOn"],
                "status": "planned",
            }
        )
    return tasks


def _configure_project(controller_token: str, team: Mapping[str, Any]) -> Any:
    room_id = str(team.get("teamRoomID", ""))
    if not room_id:
        raise DeploymentError("Team has no Matrix room")
    path = "/api/v1/projects/%s/workflow?team=%s&includeTasks=true" % (
        PROJECT_ID,
        urllib.parse.quote(TEAM_NAME),
    )
    status, workflow = _json_request(
        CONTROLLER_URL, path, token=controller_token, expected=(200, 404)
    )
    if status == 404:
        _, workflow = _json_request(
            CONTROLLER_URL,
            "/api/v1/projects",
            token=controller_token,
            method="POST",
            body={
                "project_id": PROJECT_ID,
                "title": "EgoAgentOS GPU-gated research acceptance",
                "team_id": TEAM_NAME,
                "source": "egoagentos-bootstrap",
                "requester": "@%s:matrix-local.agentteams.io:18080" % HUMAN_NAME,
                "source_room_id": room_id,
            },
            expected=(201,),
        )
    if workflow.get("status") == "paused":
        _, workflow = _json_request(
            CONTROLLER_URL,
            "/api/v1/projects/%s/resume?team=%s" % (PROJECT_ID, TEAM_NAME),
            token=controller_token,
            method="POST",
        )
    _, workflow = _json_request(
        CONTROLLER_URL,
        "/api/v1/projects/%s/replan?team=%s" % (PROJECT_ID, TEAM_NAME),
        token=controller_token,
        method="POST",
        body={"tasks": _workflow_tasks(controller_token)},
    )
    _, workflow = _json_request(
        CONTROLLER_URL,
        "/api/v1/projects/%s/pause?team=%s" % (PROJECT_ID, TEAM_NAME),
        token=controller_token,
        method="POST",
        body={"reason": GPU_PAUSE_REASON},
    )
    return workflow


def _sha256_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _optional_acceptance_summary() -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    matrix_path = RUNTIME / "matrix-live-smoke-result.json"
    if matrix_path.is_file():
        try:
            matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
            summary["matrix_multi_agent"] = {
                "status": "PASS" if matrix.get("passed") is True else "PARTIAL",
                "distinct_agent_senders": matrix.get("distinct_worker_senders", []),
                "event_count": len(matrix.get("replies", [])),
                "request_event_id": matrix.get("request_event_id"),
                "gpu": matrix.get("gpu"),
            }
        except (OSError, json.JSONDecodeError, TypeError):
            summary["matrix_multi_agent"] = {"status": "UNREADABLE"}
    expert_path = RUNTIME / "ego-live-expert-run.json"
    if expert_path.is_file():
        try:
            expert = json.loads(expert_path.read_text(encoding="utf-8"))
            summary["custom_input_experts"] = {
                "status": str(expert.get("status", "UNKNOWN")).upper(),
                "run_id": expert.get("run_id"),
                "model": expert.get("provider", {}).get("model"),
                "event_chain_valid": expert.get("event_chain_valid"),
                "event_chain_sha256": expert.get("event_chain_sha256"),
                "roles": [
                    {"role": item.get("role"), "status": item.get("status")}
                    for item in expert.get("roles", [])
                ],
                "decision": expert.get("decision"),
            }
        except (OSError, json.JSONDecodeError, TypeError):
            summary["custom_input_experts"] = {"status": "UNREADABLE"}
    return summary


def _write_public_manifest(
    private: Mapping[str, str],
    *,
    controller: Mapping[str, Any],
    manager: Mapping[str, Any],
    team: Mapping[str, Any],
    human: Mapping[str, Any],
    workers: Sequence[Mapping[str, Any]],
    workflow: Mapping[str, Any],
    compose: Optional[Mapping[str, Any]] = None,
) -> None:
    controller_token = private["AGENTTEAMS_AUTH_TOKEN"]
    matrix_token = private["AGENTTEAMS_MATRIX_ACCESS_TOKEN"]
    payload = {
        "schema": "egoagentos.local-live-stack/v1",
        "generated_at": _utc_now(),
        "truth": "LIVE_LOCAL",
        "official_agentteams": {
            "repository": OFFICIAL_REPOSITORY,
            "tag": OFFICIAL_TAG,
            "commit": OFFICIAL_COMMIT,
            "installer_compatibility": {
                "status": "APPLIED_RUNTIME_ONLY",
                "reason": "Colima bind mounts resolve inside the Linux VM",
                "socket_source": "/var/run/docker.sock",
                "official_checkout_modified": False,
            },
            "controller_url": CONTROLLER_URL,
            "controller": controller,
            "manager": manager,
            "team": {
                "name": team.get("name"),
                "phase": team.get("phase"),
                "leader_ready": team.get("leaderReady"),
                "ready_workers": team.get("readyWorkers"),
                "total_workers": team.get("totalWorkers"),
                "ready_worker_resources": sum(
                    worker.get("phase") == "Running" for worker in workers
                ),
                "room_id": team.get("teamRoomID"),
            },
            "workers": [
                {
                    "name": worker.get("name"),
                    "phase": worker.get("phase"),
                    "runtime": worker.get("runtime"),
                    "matrix_user_id": worker.get("matrixUserID"),
                }
                for worker in workers
            ],
        },
        "matrix": {
            "url": MATRIX_URL,
            "element_url": "http://127.0.0.1:18088/#/login",
            "user": human.get("matrixUserID"),
            "permission_level": human.get("permissionLevel"),
            "team_room_id": team.get("teamRoomID"),
        },
        "project": {
            "id": PROJECT_ID,
            "team": TEAM_NAME,
            "status": workflow.get("status"),
            "pause_reason": workflow.get("pause_reason"),
            "workflow_url": (
                "%s/api/v1/projects/%s/workflow?team=%s&includeTasks=true"
                % (CONTROLLER_URL, PROJECT_ID, TEAM_NAME)
            ),
            "workflow": workflow,
            "tracked_template": str(WORKFLOW_TEMPLATE.relative_to(ROOT)),
        },
        "egoagentos": {
            "api_url": EGO_API_URL,
            "api_docs": EGO_API_URL + "/docs",
            "bridge_url": BRIDGE_URL,
            "bridge_docs": BRIDGE_URL + "/docs",
            "postgres_url": "postgresql://127.0.0.1:5432/egoagentos",
            "compose": compose or {"status": "NOT_VERIFIED"},
        },
        "credentials": {
            "stored_at": str(PRIVATE_ENV),
            "mode": "0600",
            "values_in_manifest": False,
            "controller_token_sha256": _sha256_secret(controller_token),
            "matrix_token_sha256": _sha256_secret(matrix_token),
            "independent_operator_keys": (
                private["EGO_OPERATOR_KEY"] != private["EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY"]
            ),
        },
        "gpu": {
            "status": "NOT_ATTACHED",
            "reason": "GPU machine intentionally deferred by operator",
        },
        "live_acceptance": _optional_acceptance_summary(),
    }
    PUBLIC_MANIFEST.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    PUBLIC_MANIFEST.chmod(0o644)


def configure_agentteams() -> None:
    private = _ensure_private_env()
    controller_token = _capture_controller_token()
    _ensure_controller_proxy()
    print("[configure] applying four Workers, Team, and L2 Matrix user", flush=True)
    _apply_resources(controller_token)

    manager = _wait_json(
        "/api/v1/managers/default",
        controller_token,
        lambda item: item.get("phase") == "Running",
        description="official Manager/default phase=Running",
    )
    team = _wait_json(
        "/api/v1/teams/%s" % TEAM_NAME,
        controller_token,
        lambda item: (
            item.get("phase") == "Active"
            and item.get("leaderReady") is True
            and item.get("readyWorkers") == len(WORKERS) - 1
            and item.get("totalWorkers") == len(WORKERS) - 1
        ),
        description="Team active with 4/4 ready Workers",
    )
    workers = [
        _wait_json(
            "/api/v1/workers/%s" % worker,
            controller_token,
            lambda item: item.get("phase") == "Running" and bool(item.get("matrixUserID")),
            description="Worker/%s phase=Running" % worker,
        )
        for worker in WORKERS
    ]
    human = _wait_json(
        "/api/v1/humans/%s" % HUMAN_NAME,
        controller_token,
        lambda item: (
            item.get("phase") == "Active"
            and bool(item.get("matrixUserID"))
            and bool(item.get("initialPassword"))
        ),
        description="L2 Human/ego-judge phase=Active",
    )
    room_id = str(team["teamRoomID"])
    if room_id not in human.get("rooms", []):
        raise DeploymentError("ego-judge was not joined to the Team room")

    matrix_token = _matrix_login(HUMAN_NAME, str(human["initialPassword"]))
    _matrix_join(room_id, matrix_token)
    _, matrix_identity = _json_request(
        MATRIX_URL,
        "/_matrix/client/v3/account/whoami",
        token=matrix_token,
    )
    if matrix_identity.get("user_id") != human.get("matrixUserID"):
        raise DeploymentError("Matrix token identity does not match Human/ego-judge")

    private.update(
        {
            "AGENTTEAMS_AUTH_TOKEN": controller_token,
            "AGENTTEAMS_MATRIX_ACCESS_TOKEN": matrix_token,
            "AGENTTEAMS_MATRIX_USER_ID": str(human["matrixUserID"]),
            "AGENTTEAMS_MATRIX_USER_PASSWORD": str(human["initialPassword"]),
            "AGENTTEAMS_TEAM": TEAM_NAME,
            "AGENTTEAMS_TEAM_ROOM_ID": room_id,
            "AGENTTEAMS_PROJECT_ID": PROJECT_ID,
        }
    )
    _write_private_env(PRIVATE_ENV, private)

    _, controller = _json_request(CONTROLLER_URL, "/api/v1/version", token=controller_token)
    workflow = _configure_project(controller_token, team)
    _write_public_manifest(
        private,
        controller=controller,
        manager=manager,
        team=team,
        human=human,
        workers=workers,
        workflow=workflow,
    )
    print(
        "[configure] 4/4 Worker resources ready; Matrix room and paused workflow created",
        flush=True,
    )


def deploy_ego() -> None:
    private = _ensure_private_env()
    required = {
        "AGENTTEAMS_AUTH_TOKEN",
        "AGENTTEAMS_MATRIX_ACCESS_TOKEN",
        "EGO_OPERATOR_KEY",
        "EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY",
    }
    missing = sorted(required - set(private))
    if missing:
        raise DeploymentError("configure AgentTeams before Compose; missing %s" % missing)
    if shutil.which("npm") is None:
        raise DeploymentError("npm is required to build the local web application")
    web_root = ROOT / "apps/web"
    if not (web_root / "node_modules").is_dir():
        print("[ego] installing locked web dependencies", flush=True)
        _run_to_private_log(
            ["npm", "--prefix", str(web_root), "ci"],
            env=os.environ,
            log_path=RUNTIME / "web-build.log",
        )
    print("[ego] building current web assets", flush=True)
    _run_to_private_log(
        ["npm", "--prefix", str(web_root), "run", "build"],
        env=os.environ,
        log_path=RUNTIME / "web-build.log",
    )
    if not (web_root / "dist/index.html").is_file():
        raise DeploymentError("web build completed without dist/index.html")
    print("[ego] building API, PostgreSQL, web, and AgentTeams Bridge", flush=True)
    _run_to_private_log(
        [
            "docker",
            "compose",
            "--env-file",
            str(PRIVATE_ENV),
            "--profile",
            "agentteams",
            "build",
            "backend",
            "agentteams-bridge",
        ],
        env=os.environ,
        log_path=RUNTIME / "ego-compose.log",
    )
    _run_to_private_log(
        [
            "docker",
            "compose",
            "--env-file",
            str(PRIVATE_ENV),
            "--profile",
            "agentteams",
            "up",
            "-d",
        ],
        env=os.environ,
        log_path=RUNTIME / "ego-compose.log",
    )
    print("[ego] Compose services started", flush=True)


def _wait_http_json(url: str, timeout: int = 180) -> Any:
    deadline = time.monotonic() + timeout
    last = "not requested"
    while time.monotonic() < deadline:
        try:
            with DIRECT_HTTP.open(url, timeout=15) as response:
                payload = json.loads(response.read())
                if response.status == 200:
                    return payload
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            last = type(error).__name__
        time.sleep(3)
    raise DeploymentError("timed out waiting for %s (%s)" % (url, last))


def verify() -> None:
    private = _read_env(PRIVATE_ENV)
    controller_token = private.get("AGENTTEAMS_AUTH_TOKEN", "")
    matrix_token = private.get("AGENTTEAMS_MATRIX_ACCESS_TOKEN", "")
    if not controller_token or not matrix_token:
        raise DeploymentError("live tokens are missing from the private runtime env")
    _, controller = _json_request(CONTROLLER_URL, "/api/v1/version", token=controller_token)
    _, manager = _json_request(CONTROLLER_URL, "/api/v1/managers/default", token=controller_token)
    _, team = _json_request(CONTROLLER_URL, "/api/v1/teams/%s" % TEAM_NAME, token=controller_token)
    workers = []
    for worker in WORKERS:
        _, payload = _json_request(
            CONTROLLER_URL, "/api/v1/workers/%s" % worker, token=controller_token
        )
        workers.append(payload)
    _, human = _json_request(
        CONTROLLER_URL, "/api/v1/humans/%s" % HUMAN_NAME, token=controller_token
    )
    _, workflow = _json_request(
        CONTROLLER_URL,
        "/api/v1/projects/%s/workflow?team=%s&includeTasks=true" % (PROJECT_ID, TEAM_NAME),
        token=controller_token,
    )
    _, matrix_identity = _json_request(
        MATRIX_URL, "/_matrix/client/v3/account/whoami", token=matrix_token
    )
    _, joined_rooms = _json_request(
        MATRIX_URL, "/_matrix/client/v3/joined_rooms", token=matrix_token
    )
    api_health = _wait_http_json(EGO_API_URL + "/api/v1/health")
    web_api_health = _wait_http_json("http://127.0.0.1:4173/api/v1/health")
    bridge_health = _wait_http_json(BRIDGE_URL + "/api/v1/agentteams/health?team=" + TEAM_NAME)
    postgres = (
        _run(
            [
                "docker",
                "compose",
                "--env-file",
                str(PRIVATE_ENV),
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "egoagentos_owner",
                "-d",
                "egoagentos",
                "-tAc",
                "select current_database() || ':' || current_user;",
            ]
        )
        .stdout.decode("utf-8")
        .strip()
    )
    if postgres != "egoagentos:egoagentos_owner":
        raise DeploymentError("PostgreSQL identity check failed")

    checks = {
        "api_health": api_health,
        "bridge_live": bridge_health.get("live") is True,
        "matrix_user": matrix_identity.get("user_id"),
        "matrix_team_room_joined": team.get("teamRoomID") in joined_rooms.get("joined_rooms", []),
        "web_same_origin_api": web_api_health.get("status") == "ok",
        "postgres_identity": postgres,
        "verified_at": _utc_now(),
    }
    expected = (
        manager.get("phase") == "Running"
        and team.get("phase") == "Active"
        and team.get("leaderReady") is True
        and team.get("readyWorkers") == len(WORKERS) - 1
        and team.get("totalWorkers") == len(WORKERS) - 1
        and all(worker.get("phase") == "Running" for worker in workers)
        and human.get("permissionLevel") == 2
        and TEAM_NAME in human.get("accessibleTeams", [])
        and team.get("teamRoomID") in joined_rooms.get("joined_rooms", [])
        and web_api_health.get("status") == "ok"
        and workflow.get("status") == "paused"
        and workflow.get("pause_reason") == GPU_PAUSE_REASON
        and bridge_health.get("live") is True
    )
    if not expected:
        raise DeploymentError("one or more live acceptance invariants failed")
    _write_public_manifest(
        private,
        controller=controller,
        manager=manager,
        team=team,
        human=human,
        workers=workers,
        workflow=workflow,
        compose={"status": "PASS", "checks": checks},
    )
    print("[verify] LIVE_LOCAL acceptance passed; secrets remain in .runtime", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "prepare",
            "install-agentteams",
            "configure-agentteams",
            "deploy-ego",
            "verify",
            "all",
        ),
    )
    args = parser.parse_args()
    try:
        if args.command in {"prepare", "all"}:
            prepare()
        if args.command in {"install-agentteams", "all"}:
            install_agentteams()
        if args.command in {"configure-agentteams", "all"}:
            configure_agentteams()
        if args.command in {"deploy-ego", "all"}:
            deploy_ego()
        if args.command in {"verify", "all"}:
            verify()
    except (DeploymentError, OSError, ValueError) as error:
        print("ERROR: %s" % error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
