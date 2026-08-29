# AgentTeams live acceptance runbook

Use this runbook only for an installed official AgentTeams stack. Local unit
fixtures do not satisfy any live checkpoint.

## 1. Pin and verify

1. Check out `agentscope-ai/AgentTeams` commit
   `223ddc2b8073e4c8b93bcbb15e1d717f196c04d9` or a later release verified to
   contain the same Project Workflow endpoints.
2. Run `python integrations/agentteams/scripts/verify_official_contract.py`.
3. Record the official checkout commit and Controller deployment/image digest
   with the evaluation artifact.

## 2. Install and provision

Follow the official AgentTeams local or Kubernetes installation guide. Do not
copy installer-generated secrets into this repository.

Stage each EgoAgentOS Skill as a complete package in the Manager's Worker Skill
library, render `agentteams-resources.yaml.tmpl`, and apply it through the
official `agentteams-apply.sh` / `agt apply -f` path. Then verify:

```bash
agt get teams ego-researchops -o json
agt get workers ego-research-lead -o json
agt get workers ego-scout -o json
agt get workers ego-architect -o json
agt get workers ego-runtime -o json
agt get workers ego-evaluator -o json
agt get workers ego-reviewer -o json
agt get workers ego-memory-curator -o json
```

Acceptance requires Team `phase=Active`, `leaderReady=true`, and every member
ready. Separately inspect each Worker's canonical persistent Skill directory;
`spec.skills` alone is not runtime-load proof.

The Team Leader must expose the official TeamHarness coordination surface
(`project-management`, `task-management`, and `team-coordination`). EgoAgentOS
Skill assignment is separate: discovery in a Worker spec is `DECLARED`, a spawn
record is `SPAWN_AUTHORIZED`, and only a successful official spawn
`tool_result` is `TOOL_INVOKED`.

## 3. Configure least privilege

The Controller bearer token must be authorized for the target Team's project
read/write endpoints. The Matrix token must belong to a user already present in
the Team room. Workers receive gateway consumer access, not upstream provider
secrets. Never print either token in test output.

The bundled Compose profile publishes both operator APIs on loopback only. The Ego API and bridge
each enforce a deployment-owned Bearer credential for writes, but this is not per-user
authentication; any shared or remote deployment must also place them behind an identity-aware
operator ingress and must not expose port 8010 directly. Generate `EGO_OPERATOR_KEY` and
`EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY` independently, each with at least 32 UTF-8 bytes. Equal
values are rejected. Set the authoritative `EGO_OPERATOR_ID`. The bridge uses the first key only
for outbound Ego API calls and the second only for inbound bridge mutations; neither is included
in receipts or configured-setting reprs.

Set:

```text
AGENTTEAMS_CONTROLLER_URL
AGENTTEAMS_AUTH_TOKEN
AGENTTEAMS_MATRIX_URL
AGENTTEAMS_MATRIX_ACCESS_TOKEN
EGO_API_URL
EGO_OPERATOR_KEY
EGO_OPERATOR_ID
EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY
EGO_AGENTTEAMS_DATABASE_URL
EGO_AGENTTEAMS_MIGRATION_MODE=verify
```

The live Compose profile uses PostgreSQL and starts a one-shot migration/security chain before
the long-lived bridge. In a shared deployment, run checksummed migration replay as a separate
owner process, apply `deploy/postgres/agentteams_bridge_security.sql` with a database/platform
administrator, then start the bridge with only a separate LOGIN granted
`egoagentos_bridge_runtime` and `EGO_AGENTTEAMS_MIGRATION_MODE=verify`. Do not expose
`EGO_AGENTTEAMS_MIGRATION_DATABASE_URL` to the long-lived bridge. Leave the runtime URL blank
only for local SQLite development through `EGO_AGENTTEAMS_BRIDGE_DB`. An explicit PostgreSQL
URL never falls back to SQLite after a connection or migration error.

## 4. Probe

Run:

```bash
python -m apps.agentteams_bridge.cli probe --team ego-researchops
```

The response may say `live: true` only after real Controller health, Project
API, Team, and readiness responses. Save it in the benchmark workspace.

## 5. Start and observe

Use a non-synthetic Ego task. The bridge intentionally rejects the bundled
EgoLite task. Start a run through the HTTP API or CLI. Confirm that:

1. Controller `POST /api/v1/projects` returned the same project ID persisted by
   the bridge;
2. Controller `replan` holds a cycle-free pre-R2 DAG;
3. Matrix returned an `event_id` for the structured `TASK_REQUEST`;
4. Team Leader delegates with TeamHarness;
5. Workers ACK, submit content-addressed artifacts, and the Leader accepts
   them;
6. workflow polls expose those transitions.

The bridge always scopes Project API calls with `?team=`. Official Controller
writes enforce `requireSameTeam` plus `checkProjectAccess` and perform their
storage update with an internal ETag/If-Match conditional write. A returned
`409` is therefore surfaced as a retryable structured conflict; it is not
silently retried over newer state.

Run reconcile periodically. Restarting the bridge is safe: the PostgreSQL JSONB
checkpoint (or SQLite development checkpoint) is reloaded and Controller workflow is fetched again. Use
`POST /api/v1/agentteams/recover` after restart. Every bridge POST must carry
`Authorization: Bearer <EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY>`; health and GET-only run, event,
receipt, index, and Skill-evidence reads remain public. Missing bridge-key configuration returns
`503`, while missing and invalid credentials return `401` and `403`.

One store operation is one database transaction. A service path containing several
store operations and external Controller/Matrix calls is not a single atomic
transaction; crash recovery depends on the durable compensation checkpoint and a fresh
Controller workflow read. A persisted owner lease is atomically renewed immediately before every
Controller, Matrix, worker-lifecycle, and Ego mutation; ledger writes validate the same owner in
their transaction, so a stale process fails closed after takeover. The lease cannot close the
crash-after-effect window: an idempotent upstream request can commit before the process records
its receipt. Reservation, stable idempotency keys, official workflow reconciliation, and
compensation checkpoints recover that boundary. Do not describe it as exactly-once external
execution.

## 6. R2 recovery

When all pre-R2 artifacts validate, the bridge pauses the Controller project
and enters `WAITING_R2`. A chat reply does nothing. Approve through EgoAgentOS with the operator
Bearer credential and the exact action digest. For live tasks the first successful decision
response carries the one-time token only in `X-Ego-Approval-Token` and never in JSON; capture it
without printing it, then send it to the bridge R2 endpoint with an idempotency key. A replay of
the approval decision does not recover a lost token; request a fresh approval instead.

The order is fixed:

1. consume the EgoAgentOS token for `APPROVAL → EXECUTE`;
2. persist only the receipt hash and grant ID;
3. resume the Controller project;
4. apply the post-R2 DAG;
5. publish `APPROVAL_GRANTED` to Matrix.

If steps 3–5 fail after token consumption, the bridge pauses the project and
enters `COMPENSATION_REQUIRED`. Retry with the same idempotency key; the bridge
does not consume the token again.

Notification failures after Controller replan/pause/complete are also durable
compensation states. `POST /api/v1/agentteams/runs/{run_id}/reconcile` retries
only the recorded recovery operation. A malformed 2xx grant response is treated
as consumed-but-unverified and fenced for manual inspection; the token is never
optimistically reused.

## 7. Fault acceptance

Exercise at least:

- ACK timeout → task cancel with `replacementTaskId` → alternate Worker replan;
- execution timeout → cancellation and bounded reassignment;
- stale `context_version` → conflict and replan;
- artifact digest mismatch → no acceptance;
- Controller 409 → structured retryable conflict, no overwrite;
- Controller 403/404 → structured non-retryable permission/not-found failure;
- Matrix failure after mutation → project pause compensation fence;
- bridge restart → persisted recovery without duplicate project creation;
- R2 token retry → no token reuse and no token in SQLite/events.

## 8. Benchmark evidence

Current capability status is **UNIMPLEMENTED**. The bridge clients, binding format,
trace schema, and independent verifier are target scaffolding; there is not yet a real
per-scenario fault-injection and fresh-replay harness for the 14 canonical cases.
Accordingly, the public adapter is fail-closed in both modes:

- without `AGENTTEAMS_BENCHMARK_LIVE=1`, it returns lowercase `skip` as unavailable;
- with `AGENTTEAMS_BENCHMARK_LIVE=1`, it returns lowercase `skip` with
  `capability_status=UNIMPLEMENTED` and
  `execution_mode=agentteams-live-target-unimplemented`.

The live opt-in does not start the bridge, consume an approval token, write a trace, or
enter an inevitably failing pseudo release gate. The future harness will bind each
scenario to a separately prepared, non-synthetic task through an uncommitted file shaped
like:

```json
{
  "happy_path": {
    "ego_task_id": "real-task-id",
    "objective": "bounded live objective",
    "approval_token": "one-time scoped token"
  }
}
```

The future harness will read its path from `AGENTTEAMS_BENCHMARK_BINDINGS_FILE`; such a
file must remain uncommitted and access-restricted. Today, providing that file does not
enable execution or upgrade the adapter beyond `UNIMPLEMENTED/SKIP`.

Once a real harness is implemented, a `pass` will require the adapter to write
`agentteams-live-trace.json` using schema
`egoagentos.agentteams-trace/v1`. Verify the returned SHA-256 against file
bytes. The trace must contain at least three agents and ordered events for task
creation, delegation, acceptance, Skill/tool invocation, human approval,
completion, independent review, and final decision, plus RXP correlation
digests and official response identifiers. It also contains `principals` for
the bridge, human, and Ego decision actor. The benchmark-owned normative schema
is `benchmarks/schemas/agentteams-rxp-trace-v1.schema.json`; semantic authority
belongs to `benchmarks.trace_verifier.verify_trace_bytes`, not an adapter
boolean or a second integration-local schema.

The 14 canonical scenarios remain fail-closed: each needs its own real fault events.
Every future PASS also needs top-level `replay.run_ids` and
`replay.semantic_digests` for at least two distinct live runs whose semantic
digests agree. Even `happy_path` additionally needs a blocked unsafe action and
exactly one committed effect. A generic successful terminal run cannot satisfy another
scenario; the public adapter therefore never attempts that generic run while the scenario
harness is unimplemented.
Contract fixtures never produce `execution_mode=real-agentteams`.

Until the harness exists, the only correct result is `SKIP`, not `ERROR` and never a
synthetic PASS.
