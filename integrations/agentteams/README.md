# AgentTeams live integration

This directory is no longer only a resource-template sketch. It contains the
deployment profile and contract lock for the executable bridge in
`apps/agentteams_bridge/`.

## Truth boundary

Three states are deliberately different:

| State | What happened | Allowed claim |
|---|---|---|
| `live` | Controller health, Project API, active Team, ready Leader/Workers, and a Matrix send all returned real responses | the AgentTeams collaboration path ran |
| `dry_run` | the bridge rendered its intended graph without making any upstream request | plan only; **never** AgentTeams execution |
| fixture/contract test | local fake responses exercised parsing and failure logic | contract compatibility only; **never** live |

The bundled EgoLite task is synthetic and the bridge rejects it in live mode.
The repository therefore does not claim that the public static demo is an
AgentTeams run. A live run needs a non-synthetic EgoAgentOS task, an installed
AgentTeams deployment, a real Matrix access token, and actual model/MCP
credentials.

### Model-plane acceptance without the official Controller

`experiments/egolite_agentteam/` can connect the four planning/review roles to a
real OpenAI-compatible model gateway and run the local approval-gated EgoLite
replay. This proves live model HTTP calls and model-output contract handling only.
It deliberately records official AgentTeams Controller, Matrix, and physical GPU
as `NOT_RUN`; those labels cannot be promoted by a successful model response.

That historical harness label remains correct for its own frozen artifact. Separately, the
2026-09-02 local deployment verified the official Controller/Manager, Active Team, four Running
Worker resources, a paused Project, Bridge handshake, and Matrix messages from four Agent
identities as `LIVE_LOCAL`. It did not execute the Project through a physical GPU or terminal
Decision. See [`../../docs/acceptance/live-local-2026-09-02.md`](../../docs/acceptance/live-local-2026-09-02.md).

## Official contract pin

Implementation was checked against only the official
[`agentscope-ai/AgentTeams`](https://github.com/agentscope-ai/AgentTeams)
repository on 2026-08-29:

- latest stable release observed: `v1.2.2` at
  `849182af8e017168a5a200a87b1062142caf462d`;
- live bridge pin: official `main` at
  `223ddc2b8073e4c8b93bcbb15e1d717f196c04d9`;
- declarative resources: `agentteams.io/v1beta1` `Worker`, `Manager`, `Team`,
  and `Human` CRDs;
- Team orchestration: TeamHarness `projectflow` / `taskflow` and Matrix task
  rooms;
- Controller workflow surface: project create, workflow, declared artifacts,
  spawns/messages/history, pause, resume, replan, task cancel with
  `replacementTaskId`, and project complete.

The stable v1.2.2 CRDs are compatible with the resources here, but the
Controller Project Workflow HTTP surface used by the live bridge landed after
that tag. Consequently **main commit `223ddc2…` (or a later release that ships
the same endpoints) is the minimum live bridge contract**. The exact upstream
paths and hashes are recorded in `official-contract.lock.json`.

Verify the pin against GitHub:

```bash
python integrations/agentteams/scripts/verify_official_contract.py
```

Offline CI checks the lock shape without claiming that the network or runtime
was tested:

```bash
python integrations/agentteams/scripts/verify_official_contract.py --offline
```

## Runtime mapping

AgentTeams owns collaboration; EgoAgentOS owns research policy and evidence
acceptance.

| EgoAgentOS concern | Real AgentTeams mechanism |
|---|---|
| resource topology | seven `Worker` CRs, one referenced-member `Team`, one infrastructure `Manager` |
| delegation | Team Leader calls TeamHarness `taskflow delegate_task` |
| receipt / execution | Worker `ack_task`, then `submit_task` |
| acceptance | Leader `check_task` plus `projectflow accept_task_result` |
| observable state | Controller `GET /api/v1/projects/{id}/workflow?includeTasks=true` |
| artifact bytes | Controller declared-artifact endpoint; bridge recomputes SHA-256 |
| conflict / replan | result-envelope conflict or terminal revision/blocked state → Controller `replan` |
| timeout / reassign | Controller task `cancel` with `replacementTaskId`, then cycle-safe `replan` |
| human R2 | Controller `pause`; scoped Ego token is consumed; Controller `resume`; post-R2 DAG is applied |
| restart | PostgreSQL JSONB run/checkpoint in live Compose (SQLite dev fallback) plus fresh Controller workflow read |
| compensation | failed post-grant resume/replan/send is fenced by Controller `pause` and persisted as `COMPENSATION_REQUIRED` |

The bridge never writes Worker ACK, submission, acceptance, or terminal status
itself. Those facts must appear in AgentTeams' TeamHarness-backed workflow.

## Structured correlation

Every bridge event uses `egoagentos.agentteams-envelope.v2` and binds:

- Ego task ID;
- AgentTeams project ID;
- trace ID and correlation ID;
- immutable context version;
- attempt and causation IDs;
- canonical body SHA-256.

Every accepted Worker task must declare exactly one
`*.ego-envelope.json` artifact conforming to
`egoagentos.agentteams-result.v1`. The bridge downloads it through the official
declared-artifact endpoint, verifies project/task/trace/context correlation,
then downloads the primary artifact and recomputes `output_sha256`. Reviewer
tasks additionally require `independent_review: true` and
`review_verdict: PASS`.

## Skill evidence, without overclaiming

`GET /api/v1/agentteams/runs/{run_id}/skill-evidence` emits three distinct
evidence levels:

1. `DECLARED`: the real Worker response contains the Skill in `spec.skills`;
2. `SPAWN_AUTHORIZED`: the official project spawn response contains it in
   `subagent_skills`;
3. `TOOL_INVOKED`: the official spawn message stream contains a successful
   `tool_result`.

A declaration is not called an invocation. A successful tool result is not
attributed to a particular Skill unless task artifacts or an external trace
provide that link.

## Deploy resources

Install AgentTeams from its official repository at the pinned commit (or a
verified later release) using the official installation guide. Stage the six
EgoAgentOS Skill directories in the Manager's Worker Skill library before
applying the Worker specs; `spec.skills` is an assignment record, not proof
that missing package bytes were magically installed.

Render the non-secret resource file:

```bash
AGENTTEAMS_MODEL=qwen3.6-plus \
HIGRESS_GATEWAY_URL=http://aigw-local.agentteams.io:8080 \
python integrations/agentteams/render_resources.py \
  --output artifacts/runtime/agentteams-resources.yaml
```

Apply it with the official checkout's `install/agentteams-apply.sh` (or `agt
apply -f`). Documents are ordered Workers → Team → Manager because Team
members must already exist.

Before starting the bridge, verify all custom Skill package bytes on each
Worker as described by the official Worker guide. `agt get workers <name> -o
json | jq .skills` proves only the declarative assignment; runtime discovery
and invocation need the additional evidence above.

## Start and operate the bridge

Configure values from `.env.example` without committing tokens:

```bash
export AGENTTEAMS_CONTROLLER_URL=http://127.0.0.1:18080
export AGENTTEAMS_AUTH_TOKEN='...'
export AGENTTEAMS_MATRIX_URL=http://127.0.0.1:18080
export AGENTTEAMS_MATRIX_ACCESS_TOKEN='...'
export EGO_API_URL=http://127.0.0.1:8000
```

Probe the real path:

```bash
uv run --python 3.9 --extra dev \
  python -m apps.agentteams_bridge.cli probe --team ego-researchops
```

Run the HTTP bridge locally:

```bash
uv run --python 3.9 --extra dev \
  uvicorn apps.agentteams_bridge.main:app --host 127.0.0.1 --port 8010
```

Or start the opt-in Compose profile (AgentTeams itself remains an external
official deployment):

```bash
docker compose --profile agentteams up --build backend agentteams-bridge
```

The operator API is documented at `http://127.0.0.1:8010/docs`. The main
operations are start, reconcile, R2 grant, events, Skill evidence, and recover.
The R2 token is sent once to EgoAgentOS and is never persisted or placed in a
Matrix event.

## Verification

Contract and failure tests:

```bash
make test-agentteams check-agentteams
```

Live smoke, only when the official stack and a non-synthetic Ego task exist:

```bash
python -m apps.agentteams_bridge.cli smoke \
  --ego-task-id '<real-task-id>' \
  --objective 'Run the bounded benchmark and independently verify it' \
  --team ego-researchops
```

The smoke stops with status `WAITING_R2` unless a real scoped token is supplied.
It never manufactures one and never converts fixture success into a live PASS.

The benchmark adapter is `integrations.agentteams.benchmark_adapter.run_scenario`.
It is currently a target scaffold, not an implemented live benchmark capability.
Without `AGENTTEAMS_BENCHMARK_LIVE=1` it returns lowercase `skip`; with the live
opt-in it still returns lowercase `skip` with `capability_status=UNIMPLEMENTED`.
It does not call the bridge or consume a token. A future implementation needs a real
per-scenario fault-injection and fresh-replay harness before a binding file can enable
execution.

A future `pass` requires a completed AgentTeams workflow, at least three actual
Workers, a valid bridge event chain, official Skill/tool evidence, R2 receipt
correlation, an independent review PASS, a final EgoAgentOS evidence-gated
decision, and the scenario-specific fault/replay proof. It writes
`egoagentos.agentteams-trace/v1` and returns its relative path plus SHA-256.
The canonical happy path additionally requires a blocked unsafe action, one
committed effect, and two same-digest replay observations. Until that scenario harness
exists, the adapter returns `UNIMPLEMENTED/SKIP` before any generic terminal run rather
than manufacturing an `ERROR` or reusing it as proof for all 14 scenarios. See
`docs/agentteams-live-runbook.md`.
