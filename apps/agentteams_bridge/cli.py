"""Operator CLI and live smoke harness for the AgentTeams bridge."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from .errors import BridgeError
from .main import build_service
from .models import GrantRequest, RunState, StartRunRequest


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="verify real Controller/Team/Matrix readiness")
    probe.add_argument("--team", default="ego-researchops")

    start = subparsers.add_parser("start", help="start one live AgentTeams research run")
    start.add_argument("--ego-task-id", required=True)
    start.add_argument("--objective", required=True)
    start.add_argument("--team", default="ego-researchops")
    start.add_argument("--dry-run", action="store_true")

    reconcile = subparsers.add_parser("reconcile", help="poll and reconcile a persisted run")
    reconcile.add_argument("run_id")

    grant = subparsers.add_parser("grant-r2", help="consume R2 token and resume AgentTeams")
    grant.add_argument("run_id")
    grant.add_argument("--approval-token", required=True)
    grant.add_argument("--idempotency-key", required=True)

    smoke = subparsers.add_parser("smoke", help="run live start/reconcile smoke against Docker")
    smoke.add_argument("--ego-task-id", required=True)
    smoke.add_argument("--objective", required=True)
    smoke.add_argument("--team", default="ego-researchops")
    smoke.add_argument("--timeout", type=int, default=900)
    smoke.add_argument("--approval-token")
    smoke.add_argument("--idempotency-key", default="agentteams-smoke-r2")

    args = parser.parse_args()
    service = build_service()
    try:
        if args.command == "probe":
            _print(service.probe_live(args.team))
            return 0
        if args.command == "start":
            run = service.start_run(
                StartRunRequest(
                    ego_task_id=args.ego_task_id,
                    objective=args.objective,
                    team=args.team,
                    mode="dry_run" if args.dry_run else "live",
                )
            )
            _print(run.model_dump(mode="json"))
            return 0
        if args.command == "reconcile":
            _print(service.reconcile(args.run_id).model_dump(mode="json"))
            return 0
        if args.command == "grant-r2":
            run = service.grant_r2(
                args.run_id,
                GrantRequest(
                    approval_token=args.approval_token,
                    idempotency_key=args.idempotency_key,
                ),
            )
            _print(run.model_dump(mode="json"))
            return 0
        if args.command == "smoke":
            service.probe_live(args.team)
            run = service.start_run(
                StartRunRequest(
                    ego_task_id=args.ego_task_id,
                    objective=args.objective,
                    team=args.team,
                )
            )
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                result = service.reconcile(run.id)
                run = result.run
                _print({"state": run.state.value, "actions": result.actions})
                if run.state == RunState.WAITING_R2:
                    if not args.approval_token:
                        _print(
                            {
                                "status": "WAITING_R2",
                                "run_id": run.id,
                                "message": "rerun grant-r2 with a real scoped token",
                            }
                        )
                        return 3
                    run = service.grant_r2(
                        run.id,
                        GrantRequest(
                            approval_token=args.approval_token,
                            idempotency_key=args.idempotency_key,
                        ),
                    )
                if run.state == RunState.COMPLETED:
                    _print({"status": "PASS", "live": True, "run_id": run.id})
                    return 0
                if run.state in {RunState.BLOCKED, RunState.COMPENSATION_REQUIRED}:
                    _print({"status": "ERROR", "run": run.model_dump(mode="json")})
                    return 2
                time.sleep(5)
            _print({"status": "ERROR", "code": "smoke_timeout", "run_id": run.id})
            return 2
    except BridgeError as error:
        _print({"status": "ERROR", "error": error.as_dict()})
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
