# AgentTeams bridge service

This service turns the official AgentTeams Controller and TeamHarness state
into a durable EgoAgentOS collaboration ledger. It is a live adapter, not the
static browser replay.

See [`../../integrations/agentteams/README.md`](../../integrations/agentteams/README.md)
for the version pin, deployment steps, failure semantics, API workflow, and
truth boundary.

Key modules:

- `clients.py`: official Controller, Matrix, and EgoAgentOS HTTP clients;
- `service.py`: dispatch, reconcile, artifact validation, timeout/reassignment,
  R2 recovery, typed evidence finalization, compensation, and Skill evidence;
- `store.py`: shared persistence contract and SQLite development fallback;
- `postgres_store.py`: PostgreSQL/PolarDB-PG JSONB checkpoints plus transactionally serialized event and receipt chains;
- `main.py`: FastAPI operator surface;
- `cli.py`: probe, run, R2, reconcile, and live Docker smoke commands.

The service version is `0.3.0`. Its expected Project Workflow contract is
pinned to official AgentTeams main commit
`223ddc2b8073e4c8b93bcbb15e1d717f196c04d9`; the live trace separately records
the Controller version response and must not present the expected pin as a
runtime image attestation.

## Live completion and export boundary

Every bridge mutation (`runs`, `reconcile`, `r2-grant`, and `recover`) requires
`Authorization: Bearer` with the bridge-only
`EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY`. The key must contain at least 32 UTF-8 bytes and
must be generated independently from `EGO_OPERATOR_KEY`; equal configured values are
rejected at startup. Missing configuration leaves mutations fail-closed with `503`, while
missing and invalid credentials return `401` and `403`. Health and GET-only audit exports
remain public. Put a shared deployment behind an identity-aware ingress because this
deployment key does not identify individual operators.

The bridge reads `EGO_OPERATOR_KEY` and sends it as a Bearer credential on EgoAgentOS API
mutations. The corresponding `BridgeSettings.ego_operator_key` field is excluded from repr, and
upstream receipts never copy request authorization headers. Live startup should therefore use
the same key (minimum 32 UTF-8 bytes) as the Ego API. An empty value leaves Ego live writes
fail-closed; it is not a development fallback.

A live run can attach only to an EgoAgentOS task created with `synthetic=false`; the task's team,
trace, correlation id, context version, objective, and initial stage must match exactly. Completed
AgentTeams TaskMeta entries are accepted only after their declared result-envelope and primary
artifact bytes pass digest validation. Metric and reviewer artifacts have additional typed
contracts. The bridge then sends the seven resulting evidence records to the EgoAgentOS terminal
finalization API and verifies `COMPLETED`, Evidence Gate `pass`, and a real terminal decision before
it marks its own run complete.

`GET /api/v1/agentteams/runs/{run_id}/receipts` exports the append-only receipt chain. It contains
the raw, secret-free Matrix message request and response, official AgentTeams project/artifact/
completion responses, the reviewer decision, and the EgoAgentOS finalization receipt. A reused
receipt key is accepted only when its canonical payload is identical.

`GET /api/v1/agentteams/runs/{run_id}/acceptance-input-index` cross-indexes those receipts with
accepted metric artifacts, bridge events, Ego task/events, and Skill evidence. This is deliberately
**not** an acceptance bundle: `bundle_assembled` remains `false`. A separate collector still has to
fetch both services, redact, write `acceptance-input.json`, assemble the immutable files, and run
the offline verifier. Contract tests use injected transports and do not constitute a live official
AgentTeams or GPU run.

## Persistence selection

`EGO_AGENTTEAMS_DATABASE_URL=postgresql://...` explicitly selects the PostgreSQL
backend. A malformed or unavailable explicit URL fails startup and never falls back to
SQLite. `EGO_AGENTTEAMS_BRIDGE_DB` is used only when that URL is blank and is intended
for local development.

The PostgreSQL backend replays checksummed migrations through a bridge-specific
`bridge_schema_migrations` ledger. Set `EGO_AGENTTEAMS_MIGRATION_DATABASE_URL` to a
separate owner connection in shared deployments, apply
`deploy/postgres/agentteams_bridge_security.sql` with a database/platform administrator,
and use a LOGIN identity granted only the resulting `egoagentos_bridge_runtime` role for
the runtime URL. Runs and checkpoints
are JSONB. Per-run advisory transaction locks serialize event and receipt chains;
database triggers reject ledger UPDATE, DELETE, and TRUNCATE. Receipt key and receipt
hash uniqueness are database constraints.

Each public store call is atomic. A higher-level service sequence that calls
`update_run`, `archive_receipt`, and `append_event` separately is not one distributed
transaction; recovery relies on the persisted compensation checkpoint and upstream
reconciliation. This backend does not claim exactly-once external effects.

Live operations hold a persisted, time-bounded owner lease. The bridge atomically renews and
asserts that owner immediately before every Controller, Matrix, worker-lifecycle, or Ego API
mutation. Event and receipt inserts validate the same owner in their write transaction, so an
expired process cannot append after another process takes over. This fencing deliberately does
not erase the crash-after-effect boundary: an upstream idempotent mutation can commit and the
bridge can crash before its receipt/checkpoint write. Start reservation, stable idempotency keys,
official workflow reads, and compensation recovery reconcile that case; they are not a claim of
distributed exactly-once delivery.

The real integration suite uses local disposable PostgreSQL 16. It is not evidence of a
live PolarDB instance, managed backup, PITR, failover, or official AgentTeams execution.
