# EgoAgentOS final deterministic acceptance — 2026-09-03

This is the final truth ledger for the semifinal package. `PASS` means the stated
artifact or runtime was actually verified; it never upgrades an unrun external action.

## Competition gates

| Official dimension | Weight | Verified evidence | Final status |
|---|---:|---|---|
| 场景价值与行业复用性 | 25% | Three input modes, deterministic Research Compiler, experiment tree/matrix, RXP/1, domain adapters | `PASS_LOCAL / GPU_ORIGIN_NOT_RUN` |
| 多 Agent 协作与自主闭环 | 25% | Official AgentTeams v1.2.3 Controller, Manager, Active Team, four Running Worker resources and four-sender Matrix smoke | `LIVE_LOCAL`; scientific workflow `NOT_RUN` |
| Skill 工程与生态复用 | 25% | Six versioned packages, digest pin, discovery/invoke trace, lifecycle and rollback tests | `PASS_LOCAL`; official Worker invocation `NOT_RUN` |
| 工程化、运行验证与安全可审计性 | 20% | 13-stage state machine, one-use Grant, Evidence Gate, append-only ledgers, PostgreSQL RLS/triggers/LISTEN-NOTIFY, recovery and negative controls | `PASS_LOCAL / CLOUD_PARTIAL` |
| 开源贡献 | 5% | Apache-2.0 source, protocol schemas, fixtures, benchmark, tests, PPT/PDF and deterministic ZIP builder | `PASS_REPOSITORY` |

## Judge-feedback closure

### Multi-Agent collaboration

- Official AgentTeams source is locked to tag `v1.2.3`, commit
  `223ddc2b8073e4c8b93bcbb15e1d717f196c04d9`.
- Controller and Manager are Running; Team `ego-researchops` is Active.
- Four Worker resources are Running; the Matrix Team room produced events from four
  distinct agent identities. The credential-free receipt is
  `submission/evidence/agentteams-live-local-proof.json`.
- Project `egoagentos-gpu-gated-v1` is intentionally paused. All eight workflow nodes
  remain `PENDING`; GPU is `NOT_ATTACHED`.
- Therefore the requested plan → approval → GPU execution → deterministic evaluation →
  independent review → Decision chain is **not claimed as complete**.

### Database and memory

- SQLite remains an explicit development fallback; the composed control plane uses
  PostgreSQL 16 and passed 38/38 isolated integration tests.
- MVCC transactions, JSONB state, optimistic concurrency, four least-privilege roles,
  RLS/FORCE RLS, append-only triggers, migration checksums and LISTEN/NOTIFY are
  implemented and locally verified.
- Each active planning role has its own local memory store plus `FOCUS.md` compact
  projection and digest-linked compact receipts. Candidate promotion remains
  evidence-gated.
- TDSQL Nexa and TencentDB Agent Memory adapters exist, but no Tencent cloud instance,
  provider receipt, backup/PITR, multi-AZ or failover drill was available. These remain
  `NOT_CONFIGURED` / `NOT_RUN`.

## Automated acceptance

| Command/surface | Result |
|---|---|
| `make test` | 530 passed, 1 skipped |
| full Python `pytest -q` | 689 passed, 1 skipped |
| isolated PostgreSQL suite | 38/38 passed |
| official local stack verify | Controller/API/Bridge/Web/PostgreSQL checks PASS |
| sanitized AgentTeams proof | 10/10 invariants PASS |
| semifinal PPTX | 16 editable slides, 16 notes slides; targeted renders inspected |
| template fidelity | PASS, zero issues |
| slide overflow comparison | Slides 1/10/11 contain unchanged inherited decorative bleed; no new overflow |

The first PostgreSQL rerun inherited `EGO_TENANT_ID=local-live` from the live stack and
failed two assertions expecting the test default `local`. The corrected isolated run
explicitly set the test tenant and passed 38/38; no production SQL change was required.

## Determinism and memory decision

- 13-stage deterministic state machine, Evidence Gate, negative VERIFY stop,
  authorization replay rejection, append-only hash chain and synthetic byte-stable
  RXP proof: `PASS_LOCAL`.
- Fresh-database replay is semantically stable, but independently generated event roots
  are not claimed byte-identical across all production-store runs: `PARTIAL`.
- Memory is physically separated and compacted for the active planning agents, but a
  cloud-native semantic compactor, read-time receipt verifier and full 13-stage per-agent
  coverage remain future work: `PARTIAL`.
- Failure recovery is transactionally fail-closed and locally tested; GPU interruption
  recovery and managed PITR are `NOT_RUN`.

## Submission truth boundary

The public demo may show `LIVE_BROWSER` expert-planning responses and synthetic replay.
It does not prove official AgentTeams scientific execution, physical GPU use, cloud
database operation or production approval signatures. The repository must be judged
together with this ledger and the packaged proof files.
