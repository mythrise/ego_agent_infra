# Local PostgreSQL contract proof — 2026-08-29

Truth boundary: this is a local disposable Docker PostgreSQL result. It is not evidence of
PolarDB provisioning, managed backup, failover, or PITR.

## Runtime

```text
PostgreSQL 16.14 on aarch64-unknown-linux-musl
postgres:16-alpine
control-plane migrations: 001_control_plane.sql, 002_ledger_boundaries.sql
AgentTeams bridge migration: 001_bridge_control_plane.sql
security policies: security_roles.sql, agentteams_bridge_security.sql
```

## Reproduction

```bash
docker run --name egoagentos-pg-contract \
  -e POSTGRES_DB=egoagentos_test \
  -e POSTGRES_USER=egoagentos_test \
  -e POSTGRES_PASSWORD='<local disposable value>' \
  -p 127.0.0.1:55439:5432 -d postgres:16-alpine

EGO_TEST_POSTGRES_URL='postgresql://egoagentos_test:<redacted>@127.0.0.1:55439/egoagentos_test' \
  uv run --python 3.9 --extra dev pytest tests/postgres -q
```

Observed result:

```text
................................                                         [100%]
32 passed
```

The 32 tests cover both production PostgreSQL paths:

- control-plane API persistence, cross-record rollback, row-lock/optimistic concurrency,
  tenant isolation, durable event cursors, commit-only `LISTEN/NOTIFY`, and migration
  replay/checksum drift;
- database-enforced append-only evidence, memory-candidate, validated-memory, audit,
  bridge-event, and bridge-receipt ledgers;
- runtime, auditor, evidence-writer, and memory-curator least-privilege roles plus RLS;
- separate runtime LOGIN hardening, historical direct-GRANT cleanup, NOLOGIN capability
  groups, and verify-only startup without owner credentials;
- Memory Curator candidate-only writes and separate validator promotion;
- AgentTeams JSONB checkpoints, CAS/advisory locking, restart recovery, serialized event
  chains, receipt idempotency/uniqueness, and concurrent migration initialization;
- fail-closed PolarDB-compatible preflight assertions for catalog policies, notifications,
  topology markers, and fresh-schema replay.

These tests used local PostgreSQL only. The PolarDB-specific TLS endpoint, engine marker,
writer/read-only topology, managed backups, PITR restore, cloud IAM, and measured RPO/RTO
remain `NOT RUN`.

The executable assertions are the evidence source; this note is only an index to the
command and observed environment.
