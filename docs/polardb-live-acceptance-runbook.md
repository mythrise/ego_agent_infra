# PolarDB PostgreSQL live preflight and acceptance runbook

## Evidence boundary

The repository has a dated, real `postgres:16-alpine` contract result at
[`docs/evidence/postgres-local-proof-2026-08-29.md`](evidence/postgres-local-proof-2026-08-29.md).
That result proves the psycopg store against local PostgreSQL. It does **not** prove a
PolarDB endpoint, managed backup, PITR, read/write splitting, Multi-AZ failover, or
pgvector.

`ego-polardb-preflight` creates no cloud resource and calls no cloud API. Its default
`preflight` mode executes fixed `SELECT`/catalog queries only. It produces a redacted,
machine-readable JSON report and never prints a database URL or password.

## Safety model

| Mode | Default effect | Required opt-in |
|---|---|---|
| `validate-manifest` | offline JSON validation; no database connection | none |
| `preflight` | fixed read-only SQL and catalog inspection | manifest + URL environment variables |
| `preflight --active-notify` | one transient `LISTEN/NOTIFY` round trip; no durable row | explicit flag |
| `preflight --active-topology` | one TEMP table probe per endpoint, always rolled back | explicit flag |
| `fresh-schema-replay` | **drops and recreates `public`** in one dedicated database | non-production manifest authorization + explicit flag + exact database name + live database COMMENT marker |
| PITR / failover | not implemented by this CLI | external operator approval and cloud workflow; manifest remains `NOT_RUN` until evidence exists |

Never run `fresh-schema-replay` on a shared database. The test/replay contract destroys
the whole `public` schema.

Production manifests are accepted only for the fixed read-only catalog pass and require
`--allow-production-readonly`. Transient NOTIFY/TEMP probes and destructive replay are
rejected for production manifests.

## 1. Prepare a non-production target

The operator or DBA must provide:

- a dedicated, disposable database whose name begins with `egoagentos_acceptance_`;
- TLS writer endpoint and, for topology acceptance, a reader endpoint;
- a separate migration owner plus four dedicated LOGIN identities, each granted membership
  in exactly one of the NOLOGIN capability groups `egoagentos_runtime`,
  `egoagentos_auditor`, `egoagentos_evidence_writer`, or
  `egoagentos_memory_curator`;
- network allowlisting from the acceptance runner;
- a cost ceiling, recovery window, and teardown owner before any managed restore.

Copy the manifest outside the repository and replace `CHANGE_ME`:

```bash
cp deploy/polardb/acceptance-manifest.example.json /tmp/ego-polardb-acceptance.json
$EDITOR /tmp/ego-polardb-acceptance.json
```

Keep URLs in environment variables or a secret manager, not in the manifest or shell
history:

```bash
export EGO_POLARDB_WRITER_URL='postgresql://...?...sslmode=require'
export EGO_POLARDB_READER_URL='postgresql://...?...sslmode=require'
export EGO_POLARDB_RUNTIME_URL='postgresql://...?...sslmode=require'
export EGO_POLARDB_AUDITOR_URL='postgresql://...?...sslmode=require'
export EGO_POLARDB_EVIDENCE_WRITER_URL='postgresql://...?...sslmode=require'
export EGO_POLARDB_MEMORY_CURATOR_URL='postgresql://...?...sslmode=require'
```

## 2. Validate offline, then run read-only preflight

```bash
uv run --python 3.9 --extra dev ego-polardb-preflight validate-manifest \
  --manifest /tmp/ego-polardb-acceptance.json \
  --output /tmp/ego-polardb-manifest-check.json

uv run --python 3.9 --extra dev ego-polardb-preflight preflight \
  --manifest /tmp/ego-polardb-acceptance.json \
  --output /tmp/ego-polardb-preflight.json
```

The report checks:

- actual session TLS, advertised engine/version, endpoint read-only/recovery state;
- a JSONB operation and optional pgvector availability/installation;
- expected tables, migration checksums, tenant RLS and `FORCE RLS` state;
- an exact match between live migration versions/checksums and packaged SQL;
- the expected per-table tenant policy and both `USING`/`WITH CHECK` predicates;
- append-only and stage notification triggers;
- runtime/auditor/evidence-writer/memory-curator table privileges and optional real LOGIN
  identities, including proof that each LOGIN is a member of the expected NOLOGIN group;
- writer/reader topology without attempting a durable write.

`polardb_identity=PASS` requires an advertised PolarDB or `pg_settings` marker when
`require_polardb_marker=true`. PostgreSQL wire compatibility alone cannot produce that
claim. Some managed endpoints may hide vendor markers; record that as unverified rather
than editing the report.

The CLI only inspects the reader endpoint. The current API still uses one
`EGO_DATABASE_URL`; this report does not claim application-level read/write routing or
replica-lag handling.

## 3. Optional transient acceptance probes

Only after the read-only report targets the intended database:

```bash
uv run --python 3.9 --extra dev ego-polardb-preflight preflight \
  --manifest /tmp/ego-polardb-acceptance.json \
  --active-notify \
  --active-topology \
  --output /tmp/ego-polardb-active-preflight.json
```

`--active-notify` sends one random payload on `ego_polardb_preflight` and verifies that
a dedicated listener receives it. `--active-topology` attempts only a TEMP table insert
inside a transaction and rolls it back. Writer acceptance expects success; reader
acceptance expects a read-only rejection. Neither flag tests the application trigger
consumer, reconnect replay, or frontend delivery.

## 4. Apply and verify least privilege

`deploy/postgres/security_roles.sql` is intentionally separate from automatic schema
migrations. It creates four NOLOGIN group roles, revokes `PUBLIC` schema access, grants
the least-privilege matrix, and enables and forces tenant RLS. In particular, the evidence writer
can mutate only `evidence`, while Memory Curator can mutate only `memory_candidates`;
neither can update or delete a ledger row. A DBA must review and apply it as the database
owner, then create LOGIN identities through the platform secret manager.

The manifest `target.roles.*` values name those NOLOGIN capability groups. The
corresponding `*_url_env` value must authenticate a distinct, `LOGIN`-enabled identity
that is a `MEMBER` of the expected group. Preflight records `session_user` as the login
identity and verifies membership; it does not require the LOGIN and group to share a name.

```bash
psql "$EGO_POLARDB_WRITER_URL" \
  --set ON_ERROR_STOP=1 \
  --single-transaction \
  --file deploy/postgres/security_roles.sql
```

Re-run read-only preflight with runtime and auditor URL variables. Catalog grants are
not equivalent to a successful dedicated-role login; supply all four role URLs when
`require_role_logins=true`. Preflight requires both `relrowsecurity` and
`relforcerowsecurity`; a table owner otherwise bypasses ordinary RLS.

The tenant RLS policy reads the application-set `egoagentos.tenant_id` GUC. This is
trusted-application namespace filtering and a fail-closed guard when the application omits
the setting. It is not hostile tenant isolation after a database credential leak: a holder
of a runtime database credential can choose that session GUC. Use separate credentials or
databases per adversarial trust domain, or bind tenant identity at an external authenticated
database proxy.

The migration owner should run migrations before starting restricted API replicas. Set
`EGO_DATABASE_MIGRATION_MODE=verify` on those replicas: startup then compares the complete
live migration version/checksum map with packaged SQL and performs no schema write.

## 5. Destructive fresh-schema replay

This is optional and must use a disposable database. Before authorization, the DBA
sets the exact database comment:

```sql
COMMENT ON DATABASE egoagentos_acceptance_EXACT_NAME
IS 'EGOAGENTOS_DISPOSABLE_DATABASE_V1';
```

Then all four gates must agree:

1. manifest environment is `nonproduction` or `staging`;
2. `operations.fresh_schema_replay.authorized` is `true`;
3. live database name has the configured disposable prefix and matches the manifest;
4. flag, database confirmation, manifest marker, and live database COMMENT match exactly.

```bash
uv run --python 3.9 --extra dev ego-polardb-preflight fresh-schema-replay \
  --manifest /tmp/ego-polardb-acceptance.json \
  --allow-destructive \
  --confirm-database egoagentos_acceptance_EXACT_NAME \
  --confirm-marker EGOAGENTOS_DISPOSABLE_DATABASE_V1 \
  --output /tmp/ego-polardb-fresh-schema.json
```

The command re-reads the live marker before opening its destructive transaction, then
re-verifies database name, marker, and required TLS inside that same locked transaction.
It atomically drops/recreates `public` and replays packaged checksummed migrations.
Security roles must be re-applied afterward. There is no bypass flag for a production manifest, missing
prefix, missing TLS, mismatched name, or missing COMMENT marker.

## 6. PITR and Multi-AZ drill

This CLI deliberately does not invoke PolarDB backup, restore, clone, or failover APIs.
Those operations are billable and may be disruptive. Keep the manifest fields at
`NOT_RUN` until an approved operator has produced all of:

- backup policy export and retention window;
- pre-cut and post-cut audit marker IDs/hashes;
- restore job ID, requested restore timestamp, and temporary endpoint;
- restored migration checksums, table counts, and `chain_valid=true` replay;
- measured RPO/RTO and a teardown record;
- for Multi-AZ, failover operation ID and idempotent API recovery trace.

A filled manifest is an evidence index, not proof by itself. Preserve redacted provider
outputs and correlated database reports. Never commit credentials or private endpoint
URLs.

## Exit codes and claims

- `0`: all required checks pass; optional gaps may produce `PASS_WITH_GAPS`.
- `2`: manifest, safety gate, connection, or execution error.
- `3`: the live report completed but at least one required check failed.

Use the narrow claim that matches the report. A local PostgreSQL PASS remains local.
A PolarDB preflight PASS proves only the checks in that report. PITR, Multi-AZ,
application read/write splitting, event-consumer recovery, and pgvector indexed search
each require their own evidence.

## Repository verification

The offline/safety/report contract is covered without a database:

```bash
uv run --python 3.9 --extra dev pytest tests/api/test_polardb_preflight.py
```

The two real-server preflight/replay tests share the existing explicit PostgreSQL test
URL and destructive fresh-schema fixture:

```bash
EGO_TEST_POSTGRES_URL='postgresql://.../egoagentos_acceptance_test' \
  uv run --python 3.9 --extra dev pytest tests/postgres
```

The GitHub Actions PostgreSQL service already executes the entire `tests/postgres`
directory, so these acceptance tests run against PostgreSQL 16 in that job. A PASS there
remains local PostgreSQL proof, not PolarDB or PITR proof.
