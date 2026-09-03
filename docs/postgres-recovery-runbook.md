# PostgreSQL / PolarDB-PG data and recovery runbook

## Evidence boundary

PostgreSQL is the production data path. SQLite remains only the zero-service developer
fallback. Both runtime surfaces preserve an explicit synchronous store contract:

- the control plane selects PostgreSQL with a `postgresql://` or `postgres://`
  `EGO_DATABASE_URL`; leaving it unset selects the SQLite `EGO_DB_PATH` fallback;
- the AgentTeams bridge selects its PostgreSQL JSONB checkpoint/event/receipt store with
  `EGO_AGENTTEAMS_DATABASE_URL`; leaving it unset selects its SQLite development store;
- both PostgreSQL paths are exercised together by the real local PostgreSQL 16.14 suite.
- PolarDB for PostgreSQL is a compatibility target. No cloud instance, backup policy,
  failover, or point-in-time recovery (PITR) has been executed without project credentials.

The API redacts user information from the health response and reports only
`host[:port]/database`.

## Local startup

Generate five independent local-only secrets and keep them in the ignored `.env` file:

```bash
cp .env.example .env
for name in EGO_POSTGRES_PASSWORD EGO_RUNTIME_PASSWORD \
  EGO_AGENTTEAMS_RUNTIME_PASSWORD EGO_OPERATOR_KEY \
  EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY; do
  printf '%s=' "$name"
  openssl rand -hex 32
done
# Paste the five generated assignments into .env. Do not reuse a value.
docker compose up --build
curl --fail http://127.0.0.1:8000/api/v1/health
```

Compose runs `api-migrate` and `api-security` once with the database owner, then starts the
API with only `EGO_RUNTIME_USER` / `EGO_RUNTIME_PASSWORD` and read-only migration verification.
The optional `agentteams` profile repeats that sequence for a distinct bridge runtime login.
Missing, reused, short, owner-equal, or cross-service-equal runtime secrets stop the relevant
security job. The browser does not receive `EGO_OPERATOR_KEY`; if a local judge explicitly
needs the synthetic replay controls, set `EGO_ALLOW_UNAUTHENTICATED_DEMO=true`. That exception
does not authorize live tasks.

For the SQLite developer path, leave `EGO_DATABASE_URL` unset and start the API directly:

```bash
EGO_OPERATOR_KEY='REDACTED_32_BYTE_MINIMUM' \
  EGO_DB_PATH=/tmp/egoagentos.sqlite3 uv run uvicorn apps.api.main:app --port 8000
```

For a remote PostgreSQL-compatible service, URL-encode credentials and require TLS:

```bash
EGO_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DB?sslmode=require' \
  EGO_DATABASE_MIGRATION_MODE=verify \
  EGO_OPERATOR_KEY='REDACTED_32_BYTE_MINIMUM' \
  uv run uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
```

Use `apply` only in a short-lived migration-owner process. Restricted runtime replicas use
`verify`, which performs an exact read-only comparison of all packaged migration versions
and checksums.

Apply bridge migrations in a separate one-shot owner process:

```bash
EGO_AGENTTEAMS_DATABASE_URL='postgresql://MIGRATION_OWNER:REDACTED@HOST:5432/DB?sslmode=require' \
  EGO_AGENTTEAMS_MIGRATION_MODE=apply \
  uv run python -c 'import os; from apps.agentteams_bridge.postgres_store import PostgresBridgeStore; PostgresBridgeStore(os.environ["EGO_AGENTTEAMS_DATABASE_URL"], migration_mode="apply")'
```

Then start the long-lived bridge with only its restricted runtime URL; do not provide the
migration-owner URL to that process:

```bash
EGO_AGENTTEAMS_DATABASE_URL='postgresql://BRIDGE_RUNTIME:REDACTED@HOST:5432/DB?sslmode=require' \
  EGO_AGENTTEAMS_MIGRATION_MODE=verify \
  EGO_OPERATOR_KEY='REDACTED_32_BYTE_MINIMUM' \
  uv run uvicorn apps.agentteams_bridge.main:app --host 0.0.0.0 --port 8010
```

## Schema and concurrency invariants

In `apply` mode, the API takes a migration advisory lock, creates `schema_migrations`, and
applies each packaged SQL migration once in one transaction. The migrations create the
task, approval, evidence, memory-candidate, validated-memory, idempotency, and audit
schema plus database-enforced ledger boundaries. Each applied file records a SHA-256
checksum; packaged SQL drift fails startup rather than silently changing the meaning of
an already-applied version. In `verify` mode no DDL is attempted and any missing,
unexpected, or mismatched migration fails startup.

PostgreSQL enforces these boundaries:

1. Task writes use `WHERE version = expected_version` optimistic concurrency.
2. Service mutations lock the task/approval row before state or token transitions.
3. Idempotency keys use a transaction-scoped advisory lock before cache lookup.
4. Every audit stream `(tenant, task, generation)` uses a transaction-scoped advisory
   lock. A database trigger independently rejects a predecessor other than the current
   stream head, including direct SQL writes.
5. `UPDATE`, `DELETE`, and `TRUNCATE` of `audit_events` are rejected by triggers.
6. `evidence`, `memory_candidates`, and validated `memories` are append-only; their
   `UPDATE`, `DELETE`, and `TRUNCATE` operations are rejected by database triggers.
7. The `ego_stage_events` notification is emitted by an `AFTER INSERT` trigger and becomes
   visible to `LISTEN` consumers only after commit. A rolled-back event is silent.

`NOTIFY` is a low-latency wake-up, not the durable queue. Consumers checkpoint the audit
`sequence` and replay committed rows after reconnect; `stage_event_listener()` therefore
uses a dedicated session connection and must not be placed behind transaction pooling.

The [security role SQL](../deploy/postgres/security_roles.sql) creates separate NOLOGIN
runtime, auditor, evidence-writer, and memory-curator roles, least-privilege grants, and
tenant RLS policies without embedding a password. Compose applies it automatically between
migration and runtime startup, forces RLS even for a non-superuser table owner, and creates
a separate hardened LOGIN with
[`configure_runtime_login.sh`](../deploy/postgres/configure_runtime_login.sh). Evidence Writer
can mutate only the evidence ledger; Memory Curator can mutate only `memory_candidates`, never
validated memory. A non-Compose deployment must perform the same owner-only setup using
identities from its secret manager and set `egoagentos.tenant_id` per runtime connection.
The RLS helper returns no tenant when that setting is absent, so runtime access fails closed.
That GUC provides trusted-application namespace filtering, not hostile isolation after a
database credential leak: a credential holder can select another tenant value. Separate
credentials/databases or an authenticated proxy boundary are required between adversarial
tenants.
The LOGIN hardening step is idempotent: it removes direct grants on the current database,
`public` schema, and every existing table, column, sequence, and function before granting
exactly one designated NOLOGIN group. It then verifies that CONNECT is still effective through
that group and aborts if any direct ACL, unsafe role attribute, ownership, extra membership, or
admin option remains. Operators must grant object privileges to a NOLOGIN group and rerun the
hardening step after password rotation; never grant an application LOGIN directly.
The bridge has a separate
[`egoagentos_bridge_runtime`](../deploy/postgres/agentteams_bridge_security.sql) role; it can
update run/checkpoint state but cannot update, delete, truncate, or disable triggers on the
event and receipt ledgers.

## Verified integration test

Against an explicit disposable test database:

```bash
EGO_TEST_POSTGRES_URL='postgresql://USER:PASSWORD@127.0.0.1:5432/TEST_DB' \
  make test-postgres
```

The suite recreates only the `public` schema of that explicit test database. The verified
2026-09-03 result is **38/38 PASS on isolated local PostgreSQL 16**. It covers:

- full API completion, atomic rollback, optimistic concurrency, tenant isolation,
  idempotency contention, durable event cursors, commit-ordered `LISTEN/NOTIFY`, and
  fresh migration/checksum replay;
- runtime/auditor/evidence-writer/memory-curator roles, RLS, candidate-only memory curation,
  and database-enforced append-only evidence/memory/audit ledgers;
- AgentTeams JSONB checkpoints, CAS/per-run advisory locks, restart recovery, serialized
  event chains, receipt idempotency/uniqueness, append-only bridge ledgers, and concurrent
  migration initialization;
- the generic PostgreSQL fixture for the PolarDB preflight contract and fresh-schema gates.

CI provides an isolated `postgres:16-alpine` service and runs the same suite. This result is
not PolarDB provisioning, provider identity, managed backup, PITR, failover, or cloud IAM
evidence.

## Logical backup and restore drill

Use a restricted operator identity and a fresh destination database:

```bash
pg_dump --format=custom --no-owner --file=egoagentos.dump "$EGO_DATABASE_URL"
createdb egoagentos_restore
pg_restore --exit-on-error --no-owner --dbname=egoagentos_restore egoagentos.dump
```

Before cutover:

1. Point a non-production API at the restored database and check `/api/v1/health`.
2. Confirm every expected migration exists in `schema_migrations`.
3. Replay `/api/v1/tasks/{task_id}/events` for representative generations and require
   `chain_valid=true`.
4. Compare table counts and artifact digests; do not compare only HTTP availability.
5. Change the connection secret through the deployment secret manager, then retain the
   old database read-only until acceptance completes.

## PITR and PolarDB boundary

Self-managed PostgreSQL PITR requires WAL archiving and a tested base backup. Managed
PolarDB recovery must be configured and rehearsed through the cloud backup policy and a
temporary restored cluster. This repository cannot prove retention, RPO, RTO, regional
failover, or PITR without the target account and a recovery drill. Required evidence for
that future claim is: backup-policy export, restore job ID, restored-cluster endpoint,
event-chain verification report, measured RPO/RTO, and teardown record.
