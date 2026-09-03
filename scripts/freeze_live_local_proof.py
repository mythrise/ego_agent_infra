#!/usr/bin/env python3
"""Freeze a credential-free proof of the local official AgentTeams smoke test."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_WORKERS = {
    "ego-research-lead",
    "ego-architect",
    "ego-reviewer",
    "ego-memory-curator",
}


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load(path: Path) -> tuple[Dict[str, Any], bytes]:
    payload = path.read_bytes()
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value, payload


def build(public_path: Path, matrix_path: Path) -> Dict[str, Any]:
    public, public_bytes = _load(public_path)
    matrix, matrix_bytes = _load(matrix_path)
    official = public.get("official_agentteams", {})
    manager = official.get("manager", {})
    team = official.get("team", {})
    workers = official.get("workers", [])
    project = public.get("project", {})
    workflow = project.get("workflow", {})
    compose = public.get("egoagentos", {}).get("compose", {})
    replies = matrix.get("replies", [])

    worker_names = {item.get("name") for item in workers}
    worker_senders = {
        item.get("matrix_user_id") for item in workers if item.get("matrix_user_id")
    }
    observed_senders = set(matrix.get("distinct_worker_senders", []))
    final_events: Dict[str, Dict[str, Any]] = {}
    for event in replies:
        sender = event.get("sender")
        if sender not in worker_senders:
            continue
        body = str(event.get("body", ""))
        final_events[sender] = {
            "event_id": event.get("event_id"),
            "origin_server_ts": event.get("origin_server_ts"),
            "body_sha256": _sha(body.encode("utf-8")),
        }

    checks = {
        "official_source_locked": official.get("tag") == "v1.2.3"
        and official.get("commit")
        == "223ddc2b8073e4c8b93bcbb15e1d717f196c04d9",
        "manager_running": manager.get("phase") == "Running",
        "team_active": team.get("phase") == "Active"
        and team.get("leader_ready") is True,
        "four_worker_resources_running": worker_names == EXPECTED_WORKERS
        and len(workers) == 4
        and all(item.get("phase") == "Running" for item in workers),
        "four_distinct_matrix_senders": matrix.get("passed") is True
        and observed_senders == worker_senders
        and len(observed_senders) == 4,
        "matrix_event_receipts_present": set(final_events) == worker_senders
        and all(item.get("event_id") for item in final_events.values()),
        "project_paused": project.get("status") == "paused"
        and workflow.get("status") == "paused",
        "workflow_frozen_pending": len(workflow.get("nodes", [])) == 8
        and all(item.get("status") == "pending" for item in workflow.get("nodes", [])),
        "gpu_not_attached": public.get("gpu", {}).get("status") == "NOT_ATTACHED"
        and matrix.get("gpu") == "NOT_ATTACHED",
        "egoagentos_compose_verified": compose.get("status") == "PASS",
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError("live-local proof checks failed: " + ", ".join(failed))

    return {
        "schema_version": "egoagentos.agentteams-live-local-proof/v1",
        "generated_at": public.get("generated_at"),
        "truth_boundary": {
            "official_agentteams_infrastructure": "LIVE_LOCAL",
            "matrix_connectivity_smoke": "LIVE_LOCAL",
            "official_scientific_workflow": "NOT_RUN",
            "physical_gpu": "NOT_ATTACHED",
            "tdsql_nexa_cloud": "NOT_CONFIGURED",
            "pitr_restore": "NOT_RUN",
        },
        "source_files": {
            "live_stack_public": {
                "path": str(public_path.relative_to(ROOT)),
                "sha256": _sha(public_bytes),
                "packaged": False,
            },
            "matrix_live_smoke": {
                "path": str(matrix_path.relative_to(ROOT)),
                "sha256": _sha(matrix_bytes),
                "packaged": False,
                "reason": "raw model prose is intentionally excluded",
            },
        },
        "official_agentteams": {
            "repository": official.get("repository"),
            "tag": official.get("tag"),
            "commit": official.get("commit"),
            "controller": official.get("controller"),
            "manager": {
                "name": manager.get("name"),
                "runtime": manager.get("runtime"),
                "configured_model": manager.get("model"),
                "phase": manager.get("phase"),
                "note": "configuration evidence, not per-event model provenance",
            },
            "team": team,
            "workers": workers,
        },
        "matrix": {
            "room_id": matrix.get("room_id"),
            "request_event_id": matrix.get("request_event_id"),
            "event_count": len(replies),
            "distinct_worker_senders": sorted(observed_senders),
            "final_event_receipts": final_events,
        },
        "project": {
            "id": project.get("id"),
            "status": project.get("status"),
            "pause_reason": project.get("pause_reason"),
            "workflow_node_count": len(workflow.get("nodes", [])),
            "workflow_statuses": sorted(
                {item.get("status") for item in workflow.get("nodes", [])}
            ),
        },
        "checks": checks,
        "status": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--public",
        type=Path,
        default=ROOT / ".runtime" / "live-stack-public.json",
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=ROOT / ".runtime" / "matrix-live-smoke-result.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "submission" / "evidence" / "agentteams-live-local-proof.json",
    )
    args = parser.parse_args()
    public_path = args.public.resolve()
    matrix_path = args.matrix.resolve()
    result = build(public_path, matrix_path)
    payload = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    checksum_path = args.output.with_suffix(".sha256")
    checksum_path.write_text(
        f"{_sha(payload)}  {args.output.name}\n", encoding="ascii"
    )
    print(json.dumps({"status": "PASS", "output": str(args.output), "sha256": _sha(payload)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
