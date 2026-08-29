"""Fail-closed subprocess adapter for real AgentTeams + RXP trials."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from benchmarks import BENCHMARK_VERSION
from benchmarks.model import Observation, Scenario, canonical_json
from benchmarks.profiles.base import Profile
from benchmarks.trace_verifier import VerifiedTrace, verify_trace_bytes


DEFAULT_ADAPTER_MODULE = "integrations.agentteams.benchmark_adapter"
DEFAULT_ADAPTER_TIMEOUT_SECONDS = 30.0


class AgentTeamsRXPProfile(Profile):
    name = "agentteams-rxp-target"
    description = (
        "Real AgentTeams target; an isolated adapter and independently verified trace are required."
    )

    def __init__(
        self,
        *,
        adapter: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self.adapter = adapter
        configured_timeout = os.getenv("EGOAGENTOS_BENCHMARK_ADAPTER_TIMEOUT_SECONDS")
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else float(configured_timeout or DEFAULT_ADAPTER_TIMEOUT_SECONDS)
        )
        if self.timeout_seconds <= 0:
            raise ValueError("adapter timeout must be positive")

    def _adapter_reference(self) -> Tuple[Optional[str], str]:
        explicit = self.adapter or os.getenv("EGOAGENTOS_AGENTTEAMS_ADAPTER")
        if explicit:
            path = Path(explicit)
            if path.suffix == ".py" or path.is_absolute():
                if not path.is_file():
                    return None, "configured AgentTeams adapter file does not exist"
                return str(path.resolve()), ""
            if importlib.util.find_spec(explicit) is None:
                return None, "configured AgentTeams adapter module is not importable"
            return explicit, ""
        try:
            spec = importlib.util.find_spec(DEFAULT_ADAPTER_MODULE)
        except (ImportError, AttributeError, ValueError):
            spec = None
        if spec is None:
            return None, "%s is not installed" % DEFAULT_ADAPTER_MODULE
        return DEFAULT_ADAPTER_MODULE, ""

    def _run_adapter(
        self, reference: str, scenario: Scenario, seed: int, workspace: Path
    ) -> Dict[str, Any]:
        request_path = workspace / "adapter-request.json"
        response_path = workspace / "adapter-response.json"
        request = {
            "benchmark": BENCHMARK_VERSION,
            "scenario": asdict(scenario),
            "seed": seed,
            "workspace": str(workspace.resolve()),
        }
        request_path.write_text(canonical_json(request) + "\n", encoding="utf-8")
        command = [
            sys.executable,
            "-m",
            "benchmarks.adapter_worker",
            "--adapter",
            reference,
            "--request",
            str(request_path),
            "--response",
            str(response_path),
        ]
        environment = os.environ.copy()
        package_root = str(Path(__file__).resolve().parents[2])
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            package_root
            if not existing_pythonpath
            else package_root + os.pathsep + existing_pythonpath
        )
        try:
            completed = subprocess.run(
                command,
                cwd=str(workspace),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError(
                "AgentTeams adapter exceeded %.3f seconds" % self.timeout_seconds
            ) from error
        if completed.returncode != 0:
            diagnostic = completed.stderr.strip().splitlines()
            suffix = diagnostic[-1][:500] if diagnostic else "no diagnostic"
            raise RuntimeError("AgentTeams adapter process failed: %s" % suffix)
        if not response_path.is_file():
            raise RuntimeError("AgentTeams adapter produced no response file")
        raw = json.loads(response_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError("AgentTeams adapter response must be an object")
        return raw

    @staticmethod
    def _verified_pass(
        raw: Dict[str, Any], workspace: Path, scenario: Scenario, seed: int
    ) -> Tuple[VerifiedTrace, Dict[str, Any]]:
        details = raw.get("details")
        if not isinstance(details, dict):
            raise ValueError("PASS requires a details object with AgentTeams trace evidence")
        if details.get("execution_mode") != "real-agentteams":
            raise ValueError("PASS requires details.execution_mode='real-agentteams'")
        if details.get("synthetic") is not False:
            raise ValueError("PASS requires details.synthetic=false")
        trace_value = details.get("agentteams_trace_path")
        expected_digest = details.get("trace_sha256")
        if not isinstance(trace_value, str) or not isinstance(expected_digest, str):
            raise ValueError("PASS requires agentteams_trace_path and trace_sha256")
        trace_path = (workspace / trace_value).resolve()
        workspace_root = workspace.resolve()
        if workspace_root not in trace_path.parents:
            raise ValueError("AgentTeams trace must be written inside the trial workspace")
        if not trace_path.is_file():
            raise ValueError("AgentTeams trace artifact does not exist")
        trace_payload = trace_path.read_bytes()
        verified = verify_trace_bytes(trace_payload, scenario=scenario, seed=seed)
        if verified.trace_sha256 != expected_digest:
            raise ValueError("AgentTeams trace digest does not match the artifact")
        reported_roles = details.get("agent_roles")
        if not isinstance(reported_roles, list) or any(
            not isinstance(role, str) for role in reported_roles
        ):
            raise ValueError("PASS requires details.agent_roles")
        if set(reported_roles) != set(verified.agent_roles):
            raise ValueError("reported AgentTeams roles do not match the verified trace")
        safe_details = dict(details)
        safe_details.update(
            {
                "agent_roles": list(verified.agent_roles),
                "trace_sha256": verified.trace_sha256,
                "trace_root": verified.trace_root,
                "evidence_root": verified.evidence_root,
                "verified_trace_path": trace_value,
                "verified_trace_schema": "egoagentos.agentteams-trace/v1",
            }
        )
        return verified, safe_details

    def run(
        self,
        scenario: Scenario,
        seed: int,
        repetition: int,
        workspace: Path,
    ) -> Observation:
        reference, reason = self._adapter_reference()
        if reference is None:
            return Observation.skipped(
                self.name,
                scenario,
                repetition,
                seed,
                reason,
                "integrations.agentteams.benchmark_adapter.run_scenario",
            )
        started = time.perf_counter_ns()
        try:
            raw = self._run_adapter(reference, scenario, seed, workspace)
            status_value = raw.get("status")
            if not isinstance(status_value, str):
                raise ValueError("run_scenario status must be a string")
            status = status_value.lower()
            if status not in {"pass", "fail", "skip"}:
                raise ValueError("run_scenario status must be pass, fail, or skip")
            elapsed = (time.perf_counter_ns() - started) / 1_000_000
            if status != "pass":
                raw_details = raw.get("details")
                details: Dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
                reason_value = raw.get("reason") or details.get("reason")
                return Observation(
                    profile=self.name,
                    scenario_id=scenario.id,
                    repetition=repetition,
                    seed=seed,
                    status=status,
                    latency_ms=elapsed,
                    operation_count=0,
                    reason=str(reason_value or "adapter reported %s" % status),
                    assertions=["isolated real target adapter reported %s" % status],
                    implementation_path="integrations.agentteams.benchmark_adapter.run_scenario",
                    details=details,
                )
            verified, details = self._verified_pass(raw, workspace, scenario, seed)
            facts = verified.facts
            details["action_effect_count"] = facts["action_effect_count"]
            return Observation(
                profile=self.name,
                scenario_id=scenario.id,
                repetition=repetition,
                seed=seed,
                status="pass",
                latency_ms=elapsed,
                operation_count=int(facts["operation_count"]),
                task_completed=facts["task_completed"],
                unsafe_action_blocked=facts["unsafe_action_blocked"],
                approval_bypass_succeeded=facts["approval_bypass_succeeded"],
                exactly_once=facts["exactly_once"],
                trace_completeness=facts["trace_completeness"],
                evidence_completeness=facts["evidence_completeness"],
                recovered=facts["recovered"],
                reproducible=facts["reproducible"],
                hash_agreement=facts["hash_agreement"],
                dynamically_routed=facts["dynamically_routed"],
                trace_root=verified.trace_root,
                evidence_root=verified.evidence_root,
                assertions=[
                    "adapter ran in an isolated subprocess",
                    "AgentTeams/RXP trace passed the benchmark-owned schema verifier",
                ],
                implementation_path="integrations.agentteams.benchmark_adapter.run_scenario",
                details=details,
            )
        except Exception as error:
            return Observation(
                profile=self.name,
                scenario_id=scenario.id,
                repetition=repetition,
                seed=seed,
                status="error",
                latency_ms=(time.perf_counter_ns() - started) / 1_000_000,
                operation_count=0,
                reason="%s: %s" % (type(error).__name__, str(error)),
                assertions=["real target adapter or independent verifier raised an exception"],
                implementation_path="integrations.agentteams.benchmark_adapter.run_scenario",
            )
