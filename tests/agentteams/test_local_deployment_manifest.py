from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import deploy_local_live_stack as deployer


ROOT = Path(__file__).resolve().parents[2]


def test_local_ready_manifest_has_exactly_four_no_gpu_workers() -> None:
    manifest = (ROOT / "integrations/agentteams/local-ready-team.yaml.tmpl").read_text(
        encoding="utf-8"
    )
    assert manifest.count("kind: Worker") == 4
    assert "name: ego-research-lead" in manifest
    assert "name: ego-architect" in manifest
    assert "name: ego-reviewer" in manifest
    assert "name: ego-memory-curator" in manifest
    assert "name: ego-runtime" not in manifest
    assert "name: ego-evaluator" not in manifest
    assert "permissionLevel: 2" in manifest
    assert "accessibleTeams: [ego-researchops]" in manifest


def test_readiness_workflow_is_acyclic_and_gpu_gated() -> None:
    tasks = json.loads(
        (ROOT / "integrations/agentteams/readiness-workflow.json").read_text(encoding="utf-8")
    )
    ids = [task["taskId"] for task in tasks]
    assert len(ids) == len(set(ids))
    seen = set()
    for task in tasks:
        assert set(task["dependsOn"]).issubset(seen)
        seen.add(task["taskId"])
    assert ids.index("human-r2") < ids.index("gpu-execution")
    assert ids.index("gpu-execution") < ids.index("deterministic-evaluation")
    assert tasks[-1]["assignedRole"] == "ego-reviewer"


def test_runtime_directory_is_gitignored() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".runtime/" in ignored


def test_local_deployment_uses_agnes_pro_model() -> None:
    script = (ROOT / "scripts/deploy_local_live_stack.py").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert 'MODEL_NAME = "agnes-2.5-pro"' in script
    assert "EGO_AGENT_MODEL:-agnes-2.5-pro" in compose
    assert "EGO_AGENT_MODEL=agnes-2.5-pro" in env_example
    assert "AGENTTEAMS_CONTROLLER_PORT=18090" in env_example
    assert "http://agentteams-controller:8090" in env_example


def test_deployer_preserves_real_home_for_docker_context() -> None:
    script = (ROOT / "scripts/deploy_local_live_stack.py").read_text(encoding="utf-8")
    assert '"HOME": os.environ.get("HOME", str(Path.home()))' in script
    assert '"HOME": str(runtime_home)' not in script
    assert "official_log.chmod(0o600)" in script
    assert '"/var/run/docker.sock"' in script
    assert "agentteams-install-colima.sh" in script
    assert 'official_checkout_modified": False' in script
    assert '"AGENTTEAMS_UPGRADE_KEEP_ALL": "1"' in script


def test_official_checkout_must_match_pin_and_be_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "AgentTeams"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(checkout), "config", "user.name", "Test"], check=True)
    (checkout / "README.md").write_text("official\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(checkout), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(checkout), "commit", "-qm", "fixture"], check=True)
    commit = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    monkeypatch.setattr(deployer, "OFFICIAL_ROOT", checkout)
    monkeypatch.setattr(deployer, "OFFICIAL_COMMIT", "0" * 40)
    with pytest.raises(deployer.DeploymentError, match="official checkout drift"):
        deployer._ensure_official_checkout()

    monkeypatch.setattr(deployer, "OFFICIAL_COMMIT", commit)

    deployer._ensure_official_checkout()
    (checkout / "README.md").write_text("modified\n", encoding="utf-8")
    with pytest.raises(deployer.DeploymentError, match="local modifications"):
        deployer._ensure_official_checkout()


def test_base_runtime_image_is_pulled_only_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[tuple[str, ...]] = []

    def missing_then_pull(command, **_kwargs):
        commands.append(tuple(command))
        return subprocess.CompletedProcess(
            command,
            1 if command[:3] == ["docker", "image", "inspect"] else 0,
            stdout=b"",
            stderr=b"",
        )

    monkeypatch.setattr(deployer, "_run", missing_then_pull)
    deployer._ensure_base_runtime_image()
    assert commands == [
        ("docker", "image", "inspect", "python:3.11-slim"),
        ("docker", "pull", "python:3.11-slim"),
    ]

    commands.clear()
    monkeypatch.setattr(
        deployer,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, stdout=b"", stderr=b""
        ),
    )
    deployer._ensure_base_runtime_image()
    assert commands == []


def test_private_env_write_is_atomic_and_owner_only(tmp_path: Path) -> None:
    target = tmp_path / "runtime" / "live-stack.env"
    deployer._write_private_env(target, {"TOKEN": "opaque-test-value"})

    assert deployer._read_env(target) == {"TOKEN": "opaque-test-value"}
    assert target.stat().st_mode & 0o777 == 0o600
    assert list(target.parent.glob(".*.tmp")) == []


def test_private_env_rejects_control_characters(tmp_path: Path) -> None:
    with pytest.raises(deployer.DeploymentError, match="unsafe control character"):
        deployer._write_private_env(
            tmp_path / "live-stack.env",
            {"TOKEN": "unsafe\nvalue"},
        )


def test_private_env_atomic_failure_preserves_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "runtime" / "live-stack.env"
    deployer._write_private_env(target, {"TOKEN": "original-value"})

    def fail_replace(_source, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(deployer.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        deployer._write_private_env(target, {"TOKEN": "replacement-value"})

    assert deployer._read_env(target) == {"TOKEN": "original-value"}
    assert list(target.parent.glob(".*.tmp")) == []


def test_compose_exposes_controller_without_routing_it_through_higress() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    proxy = (ROOT / "deploy/agentteams/controller_proxy.py").read_text(encoding="utf-8")
    assert "agentteams-controller-proxy:" in compose
    assert "agentteams-net" in compose
    assert "AGENTTEAMS_CONTROLLER_PORT:-18090" in compose
    assert 'UPSTREAM_HOST = "agentteams-controller"' in proxy
    assert "UPSTREAM_PORT = 8090" in proxy
    assert '"127.0.0.1:${AGENTTEAMS_CONTROLLER_PORT:-18090}:8080"' in compose


def test_local_http_acceptance_bypasses_system_proxy() -> None:
    script = (ROOT / "scripts/deploy_local_live_stack.py").read_text(encoding="utf-8")
    assert "urllib.request.ProxyHandler({})" in script
    assert "DIRECT_HTTP.open" in script


def test_redeploy_reuses_existing_human_resource() -> None:
    script = (ROOT / "scripts/deploy_local_live_stack.py").read_text(encoding="utf-8")
    assert "agentteams-resources-existing-human.yaml" in script
    assert "if human_status == 200:" in script
    assert 'if "kind: Human" not in document' in script
    assert "_matrix_join(room_id, matrix_token)" in script
    assert '"matrix_team_room_joined"' in script
    assert '"web_same_origin_api"' in script
    assert '"live_acceptance": _optional_acceptance_summary()' in script


def test_local_compose_reuses_available_python_runtime_and_current_web_build() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    api_dockerfile = (ROOT / "apps/api/Dockerfile").read_text(encoding="utf-8")
    bridge_dockerfile = (ROOT / "apps/agentteams_bridge/Dockerfile").read_text(encoding="utf-8")
    web_server = (ROOT / "deploy/web/local_server.py").read_text(encoding="utf-8")
    assert "FROM python:3.11-slim" in api_dockerfile
    assert "COPY integrations ./integrations" in api_dockerfile
    assert "FROM python:3.11-slim" in bridge_dockerfile
    assert "./apps/web/dist:/usr/share/egoagentos:ro" in compose
    assert "EGO_ARTIFACT_ROOT: /data/artifacts" in compose
    assert "egoagentos-artifacts:/data" in compose
    assert "api-storage-init:" in compose
    assert "chown -R 100:101 /var/lib/egoagentos/agent-memory /data" in compose
    assert 'BACKEND_HOST = os.environ.get("EGO_WEB_BACKEND_HOST", "backend")' in web_server
    assert 'self.path = "/index.html"' in web_server
    assert compose.count("image: egoagentos-local-api:latest") == 2
    assert compose.count("image: egoagentos-local-agentteams-bridge:latest") == 2
    assert '"build",\n            "backend",\n            "agentteams-bridge"' in (
        ROOT / "scripts/deploy_local_live_stack.py"
    ).read_text(encoding="utf-8")
