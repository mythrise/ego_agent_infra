# Semifinal verification report — 2026-09-03

This report separates deterministic local proof, official local infrastructure,
browser model calls and unrun external resources. The hosted EgoLite workload remains
synthetic. Physical GPU execution, TDSQL Nexa/TencentDB Agent Memory cloud operation and
managed PITR are not inferred from code or local PostgreSQL.

## Automated repository checks

| Surface | Verified result |
|---|---|
| `make test` | 530 passed, 1 skipped |
| Full Python `pytest -q` | 689 passed, 1 skipped |
| API/control plane | 89 passed |
| RXP/1 protocol | 26 passed |
| Skill runtime | 6 passed |
| Semifinal proof | 3 passed |
| Strict benchmark | 29 passed |
| Acceptance bundle | 16 passed |
| AgentTeams bridge/contracts | 264 passed, 1 skip in the grouped gate |
| Experiment adapters | 16 passed |
| MCP/integration | 53 passed |
| Web | 28 passed; Vite production build PASS |
| Isolated PostgreSQL | 38/38 passed |

Ruff, MyPy, schema validation, Web build and submission-policy checks pass in the grouped
gate. Counts are a dated snapshot and are not converted into cloud-runtime claims.

## Deterministic RXP and Skill proof

`make demo-proof` rebuilds the synthetic proof and requires byte freshness. The committed
artifact is `submission/evidence/semifinal-local-proof.json` with SHA-256
`4697748cf82283b9db832f771f997efe85da4b992f82807b510dc6d64f7f7479`.

- RXP fixture: 2/2 complete, 23 append-only entries, structural/signature checks PASS.
- Matrix/ledger root:
  `sha256:2e313a284dcaaa6542d9d81919fc22bb61ab2015e18659f2dc0d323cbad47fd3`.
- Six Skill packages are discovered and three allowlisted handlers are executable.
- Repeated deterministic invocations preserve the same trace; unsafe generic execution
  remains fail-closed.

RXP reference documents are not yet the authoritative persisted format for every task-
store transition. Fresh-store replay preserves semantic decisions, but a universal exact
event-root identity claim is not made.

## AgentTeams LIVE_LOCAL proof

The official `agentscope-ai/AgentTeams` source is locked to tag `v1.2.3`, commit
`223ddc2b8073e4c8b93bcbb15e1d717f196c04d9`.

- Controller and Manager are Running; Team `ego-researchops` is Active.
- Four Worker resources are Running.
- A real Team Matrix room produced post-request events from four distinct Agent senders.
- Bridge, API, Web and PostgreSQL same-origin/health checks pass.
- Sanitized receipt:
  `submission/evidence/agentteams-live-local-proof.json`, SHA-256
  `6866e86792b5f0d88c79346886e3b243cc164daaed96a1f25930a40ce019db67`.

The project is intentionally paused; its eight scientific workflow nodes are all
`PENDING`, and GPU is `NOT_ATTACHED`. This proves infrastructure and Matrix connectivity,
not a complete plan → approval → GPU → evaluation → review → Decision chain. Raw model
prose is deliberately excluded from the submission; event/body digests remain in the
sanitized receipt.

## PostgreSQL, database authority and memory

The isolated PostgreSQL 16 suite passed 38/38 for control-plane and bridge transactions,
tenant isolation, optimistic concurrency, candidate-only memory curation, least-privilege
roles/RLS, append-only ledgers, migration checksums, restart/idempotency, durable cursors
and LISTEN/NOTIFY.

The first rerun inherited `EGO_TENANT_ID=local-live` and failed two assertions expecting
the test default `local`. Re-running against the same isolated test database with the test
tenant explicitly set passed 38/38; no production SQL fix was required.

Per-agent local memory stores and `FOCUS.md` projections are physically separated for the
active planning roles, with digest-linked compact receipts after small phases. This is a
structured local compactor, not proof of TencentDB Agent Memory semantic compaction. No
TDSQL Nexa or TencentDB Agent Memory provider receipt, backup/PITR, multi-AZ or failover
operation is present.

## Browser and presentation QA

- The public Web supports Chinese/English, three custom input modes, editable assumptions,
  browser model calls and a deterministic judge replay with explicit truth labels.
- Public browser calls are `LIVE_BROWSER`; GPU/Controller/Matrix assumptions on Pages are
  not live infrastructure receipts.
- The final proposal contains 16 editable slides and 16 notes slides. Slides 2, 4, 6 and
  12–16 were updated to the final evidence boundary and inspected after rendering.
- Template-fidelity verification reports zero issues.
- Overflow testing flags only slides 1, 10 and 11, exactly matching the inherited deck's
  pre-existing decorative bleed; no new content overflow was introduced.

PPTX SHA-256:
`21dc8678bdcf8d4afcb313e0c8ce9022aec37aeb8ea15d7b2b8b326f7a705c49`.

PDF SHA-256:
`50d5f90d4ddf6b6798fa2bf787afa3f2f6a2ddf633c91bf88c5da6b4c65e3f0e`.

## Remaining external gates

- Official AgentTeams scientific workflow with Skill invocation and terminal Decision.
- One same-run, cost-controlled physical GPU experiment and origin receipt.
- TDSQL Nexa/TencentDB Agent Memory live credentials and provider receipts.
- Managed backup/PITR, read/write split, multi-AZ failover and measured RPO/RTO.
- Optional public/unlisted eight-minute demo video.

These remain `NOT_RUN`, `NOT_ATTACHED` or `NOT_CONFIGURED` in the deck, package and portal.
