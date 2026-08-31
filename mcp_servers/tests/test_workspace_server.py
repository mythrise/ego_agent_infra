from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import Any

import pytest
from mcp import Client


def _workspace_modules() -> tuple[Any, Any, Any]:
    try:
        contract = importlib.import_module("egoagentos_mcp.workspace_contract")
        executor = importlib.import_module("egoagentos_mcp.workspace_executor")
        server = importlib.import_module("egoagentos_mcp.workspace_server")
    except ModuleNotFoundError:
        pytest.fail("workspace MCP server is missing")
    return contract, executor, server


def _effect_payload(root: Path) -> dict[str, Any]:
    contract, executor, _server = _workspace_modules()
    core = {
        "schema": "egoagentos.workspace-effect.v1",
        "operation": "WRITE_TEXT",
        "final_arguments": {
            "operation": "WRITE_TEXT",
            "content_utf8": "server-created output\n",
        },
        "target": "project-alpha/out/server.txt",
        "affected_scope": ["project-alpha/out/server.txt"],
        "project_id": "project-alpha",
        "task_id": "task-server-1",
        "workspace_checkpoint_sha256": executor.workspace_checkpoint_sha256(
            root, "project-alpha"
        ),
        "policy_sha256": "b" * 64,
        "decision": "ALLOW",
        "reversibility": "REVERSIBLE",
        "recovery": {"mode": "REMOVE_CREATED_PATH", "backup_path": None},
        "source_effect_sha256": "c" * 64,
        "safety_decision_sha256": "d" * 64,
        "projection_sha256": "0" * 64,
    }
    payload = dict(core)
    _redigest(contract, payload)
    return payload


def _redigest(contract: Any, payload: dict[str, Any]) -> None:
    core = {key: value for key, value in payload.items() if key != "effect_sha256"}
    core["projection_sha256"] = contract.workspace_projection_sha256(core)
    payload["projection_sha256"] = core["projection_sha256"]
    payload["effect_sha256"] = contract.workspace_effect_sha256(core)


def test_workspace_server_registers_one_typed_mutating_gateway() -> None:
    _contract, _executor, server = _workspace_modules()

    async def names() -> list[str]:
        tools = await server.mcp.list_tools()
        return [tool.name for tool in tools]

    assert asyncio.run(names()) == ["workspace_execute_effect"]


def test_official_client_executes_only_inside_operator_configured_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspace-root"
    project = root / "project-alpha"
    (project / "out").mkdir(parents=True)
    monkeypatch.setenv("EGO_MCP_WORKSPACE_ROOT", str(root))
    _contract, _executor, server = _workspace_modules()
    payload = _effect_payload(root)

    async def call() -> object:
        async with Client(server.mcp) as client:
            result = await client.call_tool(
                "workspace_execute_effect",
                {"effect": payload, "approval_receipt": None},
            )
            assert result.is_error is False
            return result.structured_content

    response = asyncio.run(call())

    assert isinstance(response, dict)
    assert response["effect_sha256"] == payload["effect_sha256"]
    assert response["status"] == "APPLIED"
    assert "server-created output" not in repr(response)
    assert (project / "out/server.txt").read_text(encoding="utf-8") == (
        "server-created output\n"
    )


def test_server_failure_does_not_echo_secret_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspace-root"
    project = root / "project-alpha"
    (project / "out").mkdir(parents=True)
    monkeypatch.setenv("EGO_MCP_WORKSPACE_ROOT", str(root))
    contract, _executor, server = _workspace_modules()
    payload = _effect_payload(root)
    payload["final_arguments"]["content_utf8"] = "api_key=server-secret-must-not-leak"
    _redigest(contract, payload)

    async def call() -> object:
        async with Client(server.mcp) as client:
            return await client.call_tool(
                "workspace_execute_effect",
                {"effect": payload, "approval_receipt": None},
            )

    result = asyncio.run(call())

    assert result.is_error is True
    assert "server-secret-must-not-leak" not in repr(result)
    assert not (project / "out/server.txt").exists()


def test_embedded_server_injects_exact_approval_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspace-root"
    project = root / "project-alpha"
    (project / "out").mkdir(parents=True)
    monkeypatch.setenv("EGO_MCP_WORKSPACE_ROOT", str(root))
    contract, _executor, server = _workspace_modules()
    payload = _effect_payload(root)
    payload["decision"] = "APPROVAL_REQUIRED"
    _redigest(contract, payload)
    calls: list[tuple[str, str, str, str, str]] = []

    def verify(receipt: str, effect: Any) -> None:
        calls.append(
            (
                receipt,
                effect.effect_sha256,
                effect.source_effect_sha256,
                effect.safety_decision_sha256,
                effect.projection_sha256,
            )
        )
        if receipt != "exact-approved-receipt":
            raise AssertionError("unexpected approval receipt")

    injected_mcp = server.create_workspace_server(approval_verifier=verify)

    async def call() -> object:
        async with Client(injected_mcp) as client:
            return await client.call_tool(
                "workspace_execute_effect",
                {"effect": payload, "approval_receipt": "exact-approved-receipt"},
            )

    response = asyncio.run(call())
    assert response.is_error is False
    assert calls == [
        (
            "exact-approved-receipt",
            payload["effect_sha256"],
            payload["source_effect_sha256"],
            payload["safety_decision_sha256"],
            payload["projection_sha256"],
        )
    ]
    assert (project / "out/server.txt").exists()
