# EgoAgentOS semifinal evidence index

Date: 2026-08-29  
Track: GOAI 2026 Agent Infra semifinal  
Truth rule: a design, fixture, or contract test is never promoted to a live-platform claim.

## Fastest judge replay

```bash
make demo-proof
make test
make verify
```

`make demo-proof` is the short deterministic entry point. `make test` is the full local
suite. `make benchmark-release EVIDENCE_DIR=/new/empty/persistent/path` is deliberately
fail-closed: without live AgentTeams evidence it exits non-zero and records SKIP.

## Rule-to-evidence map

| Semifinal dimension | Executable/reviewable evidence | Current status |
|---|---|---|
| Scenario and portability (20%) | `docs/competition-mapping.md`, `docs/architecture.md`, RXP schemas, real Fashion-MNIST adapter, PostgreSQL Store contract | local/contract verified; external GPU origin and PolarDB/PITR not run |
| Multi-Agent collaboration (25%) | `apps/agentteams_bridge/`, PostgreSQL bridge store, `integrations/agentteams/official-contract.lock.json`, `tests/agentteams/` | contract/fault tests PASS; live target SKIP |
| Skills (20%) | `skills/`, `skill_runtime/`, `/api/v1/skills`, `/invoke`, invocation trace | 6 discovered / 3 executable; lifecycle tests PASS |
| Engineering/security (30%) | one-time Grant, MatrixLedger, PostgreSQL roles/RLS/append-only/LISTEN-NOTIFY, strict benchmark, acceptance bundle, negative control, recovery | local proofs PASS; cloud origin unverified |
| Open source (5%) | Apache-2.0 repo, PPTX/PDF, demo script, proof, deterministic ZIP | ready locally; current revision not claimed deployed |

## Evidence ladder

| Claim | Artifact or command | Status/boundary |
|---|---|---|
| RXP causal closure | `submission/evidence/semifinal-local-proof.json` | PASS; 2/2 cells, 23 entries, public synthetic key |
| RXP verifier/API | `protocols/rxp/`, `/api/v1/rxp/demo`, `/api/v1/rxp/verify` | executable; task-store persistence not yet wired |
| Skill discovery/invoke | `skill_runtime/`, `/api/v1/skills` | 6 packages; 3 allowlisted handlers |
| Fault benchmark | `benchmarks/artifacts/2026-08-29-local-cpu.*` | 5 repetitions, 210 trials, independent oracle |
| Dynamic collaboration bridge | `apps/agentteams_bridge/`, `tests/agentteams/` | 41 contract/fault tests PASS; live Controller absent |
| Real-workload adapter | `experiments/fashion_mnist_amp/` | 13 contract/negative tests PASS; real GPU execution absent |
| One-command acceptance | `semifinal_acceptance/` | 16 tests PASS; v1 result is `CONTRACT_PASS_ORIGIN_UNVERIFIED` |
| PostgreSQL production path | `docs/evidence/postgres-local-proof-2026-08-29.md`, `deploy/postgres/` | real local PostgreSQL 16.14 tests 32/32 PASS; runtime login hardening, append-only ledgers, notifications, preflight contract |
| Judge-facing UI | `submission/screenshots/semifinal-rxp-cockpit.png` | static fixture, no backend/GPU/signature claim |
| Release gate | `make benchmark-release EVIDENCE_DIR=...` | no live target → expected non-zero/SKIP |

## Frozen identifiers

- Semifinal proof SHA-256:
  `4697748cf82283b9db832f771f997efe85da4b992f82807b510dc6d64f7f7479`
- RXP demo SHA-256:
  `178a24b303f13a480262498cd793fba6fe63570ceedb27928805b7c321362524`
- RXP ledger root:
  `sha256:2e313a284dcaaa6542d9d81919fc22bb61ab2015e18659f2dc0d323cbad47fd3`
- Benchmark semantic digest:
  `05cab481a525210026d07377bb841ca0cd73f27790e9856b3c29211320b6b996`
- Semifinal PPTX SHA-256:
  `c9cb26a5d21de9a86c641b65d4f137b04cecc6978500b10330dded004bc4cece`
- Semifinal PDF SHA-256:
  `dc9a6d7a254cb983788221d1e73291cde44aa70ec52618bed19ed63c027e54a4`
- Cockpit screenshot SHA-256:
  `28bc08a6b01d81c43a53bd1a866148c6a9b3edc31ca714f8ab2b571e31c73c3b`

## Explicit non-claims

- No live AgentTeams Controller/Team/Matrix same-run trace is present.
- A real Fashion-MNIST GPU adapter is present, but no live GPU job, trusted external
  origin, or model-improvement result is present.
- No PolarDB deployment, PITR restoration, measured RPO/RTO, or cloud IAM proof is present.
- The public RXP demo key has no production trust or key-custody meaning.
- The current application container image build was blocked at Docker Hub metadata by a
  network timeout; it is not marked verified.
- GitHub Pages proves only the static judge replay. API, AgentTeams, PostgreSQL, and GPU
  capabilities require their own local or deployed profile evidence.
