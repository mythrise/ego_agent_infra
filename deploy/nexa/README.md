# TDSQL Nexa + TencentDB Agent Memory deployment profile

EgoAgentOS uses two different Tencent data products for two different jobs:

- **TDSQL Nexa** is the production transaction/evidence authority. Point
  `EGO_NEXA_DATABASE_URL` at the instance's PostgreSQL-compatible SQL endpoint.
  The existing versioned migrations, MVCC transactions, append-only triggers,
  RLS/GRANT boundaries, and LISTEN/NOTIFY path are reused.
- **TencentDB Agent Memory** is the agent context service. Configure its v3
  endpoint and isolation coordinates. At each completed phase EgoAgentOS writes
  the conversation, forces an archive/compact, and also writes the deterministic
  per-agent local SQLite + `FOCUS.md` projection.

Required production variables:

```dotenv
EGO_NEXA_DATABASE_URL=postgresql://RUNTIME_USER:SECRET@NEXA_SQL_HOST:5432/egoagentos
TENCENT_AGENT_MEMORY_ENDPOINT=https://MEMORY_ENDPOINT
TENCENT_AGENT_MEMORY_API_KEY=REDACTED
TENCENT_AGENT_MEMORY_SERVICE_ID=SERVICE_ID
TENCENT_AGENT_MEMORY_SPACE_ID=egoagentos-production
EGO_AGENT_MEMORY_ROOT=/var/lib/egoagentos/agent-memory
```

Run migrations with the owner credential, then run the API with the least-
privilege runtime credential exactly as described in
[`../../docs/postgres-recovery-runbook.md`](../../docs/postgres-recovery-runbook.md).
Never put either credential in
a receipt, Markdown focus file, or frontend environment variable.

Acceptance order:

1. `GET /api/v1/research/storage` must report Nexa `configured_unprobed`, never
   `LIVE`, before a network check.
2. Authenticated `GET /api/v1/research/storage?probe_nexa=true` must return a
   successful SQL receipt. A generic PostgreSQL version string proves SQL
   reachability but does not by itself prove the host is a Nexa instance; retain
   the Tencent console instance receipt separately.
3. Commit one disposable agent phase with remote sync and confirm both
   `/v3/skill/conversation/add` and `/v3/skill/conversation/force-archive`
   provider receipts.
4. Verify that another `agent_id` cannot query or mutate the first agent's L0–L3
   namespace.
5. Perform fresh-schema replay and compare every canonical digest.

No Nexa or TencentDB Agent Memory endpoint is bundled with this repository.
Without these variables, provider status is truthfully `NOT_CONFIGURED`; the
tested local fallback is `LIVE_LOCAL` and must not be described as Nexa.
