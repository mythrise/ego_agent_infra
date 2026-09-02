# EgoAgentOS ResearchOps API

FastAPI control plane with PostgreSQL as the production persistence path and SQLite as a
zero-service developer fallback for the EgoLite competition demo. The workflow is a strict
state machine; policy, approvals, provenance hashes, evaluation, evidence verification, and
audit persistence are deterministic Python rather than LLM assertions.

The seeded values are always labelled **SYNTHETIC DEMO DATA**. HiClaw, Nacos, and Higress are
optional adapter metadata and are never reported as live unless a future adapter performs a
verified handshake.

## External live task and finalization contract

All live mutations and executable Skill invocations require
`Authorization: Bearer $EGO_OPERATOR_KEY`. The key must contain at least 32 UTF-8 bytes;
an empty key disables those writes with a fail-closed `503`. `EGO_OPERATOR_ID` is the
deployment-owned audit identity. A request may omit the legacy `approver` field; if it
supplies a different identity, the decision is rejected rather than trusting the caller.
Health, dashboards, task/event reads, and the side-effect-free RXP verifier remain public.

`POST /api/v1/tasks` is separate from the demo reset path. Its strict request requires
`synthetic: false`, an immutable AgentTeams `live_source` binding, a frozen `ResearchGoal`, and
an exact R2/R3 `execution_contract`. Omitting `synthetic`, sending `true`, reusing a task id, or
submitting an action payload without its matching config digest fails closed. A live task never
receives the demo's generated artifacts or modeled metrics.

After the AgentTeams pre-approval DAG completes, the bridge advances only the legal
`INTAKE -> CONTEXT -> PLAN -> PLAN_REVIEW -> APPROVAL` path. The human decision still produces a
scope-bound, expiring, single-use token; consuming it is the only way to enter `EXECUTE`.

Two typed evidence routes are available after that point:

- `POST /api/v1/tasks/{task_id}/evidence` ingests one version- and generation-bound record;
- `POST /api/v1/tasks/{task_id}/finalize` atomically ingests the remaining evidence and advances
  `EXECUTE -> OBSERVE -> EVALUATE -> VERIFY -> DECIDE -> ... -> COMPLETED`.

The terminal route requires exactly the seven evidence kinds, successful AgentTeams receipts,
a GPU receipt for metric evidence, raw paired samples whose digest matches, byte-for-byte
deterministic metric recomputation, and an independent PASS review binding the exact non-review
evidence digests. `KEEP`, `DROP`, or `INCONCLUSIVE` is derived from the recomputed results; it is
not accepted from the caller. Any missing item, stale version, digest mismatch, failed receipt,
or forged reviewer rolls the transaction back without terminal progress.

## Run locally

From the repository root:

```bash
python3.9 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
export EGO_OPERATOR_KEY="$(openssl rand -hex 32)"
export EGO_OPERATOR_ID=local.operator
# Only when using the labelled browser synthetic replay:
export EGO_ALLOW_UNAUTHENTICATED_DEMO=true
EGO_DB_PATH=/tmp/egoagentos.sqlite3 uvicorn apps.api.main:app --reload
```

Open `http://127.0.0.1:8000/docs` or check:

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/dashboard
```

The happy-path demo is deliberately two-part:

1. `POST /api/v1/demo/reset`, then `POST /api/v1/tasks/ego-lite-001/autorun` pauses at R2 approval.
2. Approve `pending_approval.id` with its exact `action_digest`. For a live task, the response
   JSON always omits the raw secret and the first successful response delivers the scope-bound
   token only in `X-Ego-Approval-Token`, with `Cache-Control: no-store`. Send that token as
   `approval_token` to enter `EXECUTE`; an idempotent replay never returns it again.

## Live model expert runs

`POST /api/v1/expert-runs` accepts one of the three input levels (`detailed`, `idea`, or
`baseline`) and starts four real server-side model calls. `GET /api/v1/expert-runs/{run_id}`
returns progressive role state, schema-validated outputs, credential-free HTTP receipts,
per-Agent focus-memory receipts, the deterministic tree/matrix summary, and the append-only
event hash chain. Both routes require the operator Bearer credential because the payload can
contain private research material.

Configure the model plane only on the API process:

```bash
export EGO_AGENT_MODEL_BASE_URL=https://apihub.agnes-ai.com/v1
export EGO_AGENT_MODEL=agnes-2.5-pro
export EGO_AGENT_MODEL_REASONING_EFFORT=low
read -s EGO_AGENT_MODEL_API_KEY
export EGO_AGENT_MODEL_API_KEY
```

`complete_json` sends `response_format={"type":"json_object"}`. The live expert service uses
low reasoning effort by default so a reasoning model cannot consume the entire completion budget
before emitting the contract JSON. Set `EGO_AGENT_MODEL_REASONING_EFFORT` to an empty value only
for gateways that reject the OpenAI-compatible parameter.

The returned truth boundary is intentionally narrower than an AgentTeams or experiment claim:
provider responses are `LIVE`; deterministic compilation and private focus memory are
`LIVE_LOCAL`; repository/literature retrieval and physical GPU execution remain `NOT_RUN` until
their own receipts exist. AgentTeams/Matrix truth is evaluated separately: the 2026-09-02 local
stack has infrastructure-level `LIVE_LOCAL` receipts, but no completed Project workflow or GPU
Decision. A reviewer `PASS` or `WARN` produces `PLAN_READY_FOR_HUMAN_REVIEW`, never an automatic
experiment dispatch.

Send the operator credential on every mutation, for example:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $EGO_OPERATOR_KEY" \
  -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:8000/api/v1/demo/reset \
  -d '{}'
```

For the browser-only, clearly labelled synthetic replay, a developer may explicitly set
`EGO_ALLOW_UNAUTHENTICATED_DEMO=true`. That exception is restricted to the synthetic reset,
advance/autorun, and approval paths, uses the fixed `demo.operator` audit identity, and cannot
create or mutate a live task or invoke a Skill. Synthetic approval retains its JSON token only
for this local demo compatibility path.

To demonstrate an evidence failure, reset with
`{"scenario":"insufficient_evidence"}`. That run deliberately omits trace evidence and pauses
at `VERIFY`; the response states the exact missing artifact without claiming success.

Every mutating route accepts `Idempotency-Key`. Reusing a key with a different body returns a
structured conflict. API errors use `{"error":{"code", "message", "details", "request_id"}}`.

At `MEMORY_SKILL`, the Memory Curator can append only `memory_candidates`. A separate
deterministic `memory-validator` actor checks the completed Evidence Gate and creates validated
memory. PostgreSQL `GRANT`/RLS policy denies the Curator direct `memories` inserts, and database
triggers reject update/delete/truncate on evidence, candidate, and validated-memory ledgers.

Set `EGO_DATABASE_URL=postgresql://...` to use the real psycopg backend. It implements the
same store contract with atomic transactions, optimistic task versions, row locks, an
append-only database trigger, stream-scoped advisory locks, durable event cursors, and
commit-ordered `ego_stage_events` notifications. Use `EGO_DATABASE_MIGRATION_MODE=apply` only
with a migration-capable owner; restricted runtime roles use `verify`, which checks the exact
migration checksum map without attempting DDL. The four least-privilege roles are runtime,
auditor, evidence writer, and memory curator. See the
[database runbook](../../docs/postgres-recovery-runbook.md) for migration, RLS, test, backup,
and recovery procedures.

Docker Compose never gives the owner DSN to the long-lived API. It runs the checksummed
`api-migrate` and role/login hardening jobs first, then starts `backend` with a distinct
non-owner LOGIN, `EGO_DATABASE_MIGRATION_MODE=verify`, forced tenant RLS, and no schema-create
privilege. Native deployments must preserve the same process and credential boundary.
The `egoagentos.tenant_id` RLS GUC is trusted-application namespace filtering, not
adversarial isolation after a runtime database credential leak; hostile trust domains need
separate credentials/databases or an authenticated proxy that owns tenant binding.

`apps/api/polardb_preflight.py` provides a fail-closed live acceptance command for a writer,
read-only node, roles, RLS, append-only triggers, JSONB/pgvector capability, migration state,
and transactional `LISTEN/NOTIFY`. Passing its generic PostgreSQL fixture tests is not proof of
a PolarDB deployment, backup policy, failover, or PITR restore; those remain `NOT RUN` until a
real cloud acceptance manifest and resulting evidence bundle are captured.

## External adapter truth states

Setting `EGO_HICLAW_URL`, `EGO_NACOS_URL`, or `EGO_HIGRESS_URL` changes the corresponding state
from `not_configured` to `configured_unverified`, never to a fabricated `ready` state. Configure
browser origins with comma-separated `EGO_CORS_ORIGINS`.

## Container

Build from the repository root so the Dockerfile can read `pyproject.toml`:

```bash
docker build -f apps/api/Dockerfile -t egoagentos-api .
docker run --rm -p 8000:8000 -v egoagentos-data:/data egoagentos-api
```

The image runs as the non-root `egoagentos` user and includes a `/api/v1/health` health check.
The repository Compose file starts PostgreSQL 16 by default and requires
`EGO_POSTGRES_PASSWORD` from the ignored `.env`; it does not contain a production secret.
