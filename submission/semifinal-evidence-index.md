# EgoAgentOS semifinal evidence index

Date: 2026-09-03
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
| Scenario value and industry reuse (25%) | `docs/competition-mapping.md`, `docs/architecture.md`, RXP schemas, real Fashion-MNIST adapter, PostgreSQL Store contract | local/contract verified; external GPU origin and Nexa/PITR not run |
| Multi-Agent collaboration (25%) | `apps/agentteams_bridge/`, official contract lock, `agentteams-live-local-proof.json`, `tests/agentteams/` | infrastructure + Matrix smoke `LIVE_LOCAL`; scientific workflow `NOT_RUN` |
| Skill engineering and ecosystem reuse (25%) | `skills/`, `skill_runtime/`, `/api/v1/skills`, `/invoke`, invocation trace | 6 discovered / 3 executable; lifecycle tests PASS; official Worker invocation not run |
| Engineering, runtime validation and auditable security (20%) | one-time Grant, MatrixLedger, PostgreSQL roles/RLS/append-only/LISTEN-NOTIFY, strict benchmark, acceptance bundle, negative control, recovery | local proofs PASS; cloud origin unverified |
| Open source (5%) | Apache-2.0 repo, PPTX/PDF, demo script, two proof ledgers, deterministic ZIP | final revision pending upload verification |

## Evidence ladder

| Claim | Artifact or command | Status/boundary |
|---|---|---|
| RXP causal closure | `submission/evidence/semifinal-local-proof.json` | PASS; 2/2 cells, 23 entries, public synthetic key |
| RXP verifier/API | `protocols/rxp/`, `/api/v1/rxp/demo`, `/api/v1/rxp/verify` | executable; task-store persistence not yet wired |
| Skill discovery/invoke | `skill_runtime/`, `/api/v1/skills` | 6 packages; 3 allowlisted handlers |
| Fault benchmark | `benchmarks/artifacts/2026-08-29-local-cpu.*` | 5 repetitions, 210 trials, independent oracle |
| Official AgentTeams infrastructure | `submission/evidence/agentteams-live-local-proof.json` | v1.2.3 Controller/Manager/Team/four Workers/Matrix smoke `LIVE_LOCAL`; eight scientific nodes `PENDING` |
| Dynamic collaboration bridge | `apps/agentteams_bridge/`, `tests/agentteams/` | 264 contract/fault tests PASS; full official lifecycle not run |
| Real-workload adapter | `experiments/fashion_mnist_amp/` | 16 contract/negative tests PASS; real GPU execution absent |
| One-command acceptance | `semifinal_acceptance/` | 16 tests PASS; v1 result is `CONTRACT_PASS_ORIGIN_UNVERIFIED` |
| PostgreSQL production path | `deploy/postgres/`, `tests/postgres/` | isolated PostgreSQL 16 tests 38/38 PASS; RLS, append-only ledgers, notifications and migration checks |
| Judge-facing UI | public Pages + `submission/screenshots/semifinal-rxp-cockpit.png` | `LIVE_BROWSER` planning + synthetic replay; no backend/GPU/signature claim |
| Release gate | `make benchmark-release EVIDENCE_DIR=...` | no live target → expected non-zero/SKIP |

## Frozen identifiers

- Semifinal proof SHA-256:
  `4697748cf82283b9db832f771f997efe85da4b992f82807b510dc6d64f7f7479`
- AgentTeams LIVE_LOCAL proof SHA-256:
  `6866e86792b5f0d88c79346886e3b243cc164daaed96a1f25930a40ce019db67`
- RXP demo SHA-256:
  `178a24b303f13a480262498cd793fba6fe63570ceedb27928805b7c321362524`
- RXP ledger root:
  `sha256:2e313a284dcaaa6542d9d81919fc22bb61ab2015e18659f2dc0d323cbad47fd3`
- Benchmark semantic digest:
  `05cab481a525210026d07377bb841ca0cd73f27790e9856b3c29211320b6b996`
- Semifinal PPTX SHA-256:
  `21dc8678bdcf8d4afcb313e0c8ce9022aec37aeb8ea15d7b2b8b326f7a705c49`
- Semifinal PDF SHA-256:
  `50d5f90d4ddf6b6798fa2bf787afa3f2f6a2ddf633c91bf88c5da6b4c65e3f0e`
- Cockpit screenshot SHA-256:
  `d613745fce81243acf5f6caf37df15678bb1ea157c348b549df524cf1e39a1c1`

## Explicit non-claims

- Official AgentTeams infrastructure and Matrix connectivity are `LIVE_LOCAL`; no
  scientific task lifecycle or same-run GPU/Decision trace is present.
- A real Fashion-MNIST GPU adapter is present, but no live GPU job, trusted external
  origin, or model-improvement result is present.
- No TDSQL Nexa deployment, TencentDB Agent Memory provider receipt, PITR restoration,
  measured RPO/RTO, or cloud IAM proof is present.
- The public RXP demo key has no production trust or key-custody meaning.
- The current application container image build was blocked at Docker Hub metadata by a
  network timeout; it is not marked verified.
- GitHub Pages proves only browser model calls and synthetic replay. AgentTeams and
  PostgreSQL have separate `LIVE_LOCAL` evidence; GPU and cloud database do not.
