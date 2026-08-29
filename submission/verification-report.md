# Semifinal verification report — 2026-08-29

This report distinguishes executable local evidence from external-runtime claims. The
EgoLite workload, resource trace, model metrics, public RXP signing key, and browser
fixture are **SYNTHETIC**. A separate real Fashion-MNIST single-GPU FP32/AMP adapter is
implemented, but no live output from it is bundled. Live AgentTeams/GPU origin,
PolarDB/PITR, production signature trust, and an application container image are not
inferred from local tests.

## Automated repository checks

| Surface | Command | Verified result |
|---|---|---|
| FastAPI/control plane | `make test-api` | 69 passed |
| RXP/1 protocol | `make test-rxp` | 26 passed; seven schemas current |
| Skill runtime | `make test-skills` | 6 passed; 6 discovered / 3 executable |
| Semifinal proof | `make test-proof` | 3 passed; Ruff PASS |
| Strict benchmark | `make test-benchmark` | 29 passed; strict 2-repetition replay PASS |
| Acceptance bundle | `make test-acceptance` | 16 passed; eight scenarios and negative origin/receipt/Matrix/Decision/recovery checks PASS |
| AgentTeams bridge | `make test-agentteams check-agentteams` | 41 passed; Ruff/MyPy/official offline lock PASS |
| Fashion-MNIST adapter | `make test-experiments` | 13 passed; CUDA/resource/artifact/verifier contracts PASS, no live run inferred |
| MCP/integration | `make test-mcp` | 23 passed; Ruff PASS |
| Web | `make test-web` | 16 passed; Vite production build PASS |
| Submission policy | `make verify` | fail-closed deliverable, proof, boundary, and secret checks PASS |

The full `make test` run covers 242 tests across these Python/TypeScript groups: API 69,
RXP 26, Skills 6, Proof 3, Benchmark 29, Acceptance 16, AgentTeams 41, Experiments 13,
MCP 23, and Web 16. Ruff
and MyPy pass for the API, RXP, Skill runtime, benchmark, and AgentTeams bridge. Counts
are a dated snapshot, not a timeless project claim; CI and the evidence index are the
authoritative replay path.

## Deterministic RXP and Skill proof

`make demo-proof` rebuilds the proof twice and requires byte freshness. The committed
bundle is `submission/evidence/semifinal-local-proof.json`; its sidecar SHA-256 is
`4697748cf82283b9db832f771f997efe85da4b992f82807b510dc6d64f7f7479`.

- RXP fixture: `2 / 2 COMPLETE`, 23 append-only entries, structural/signature checks
  PASS, and two independently generated files are byte-identical.
- RXP Matrix/ledger root:
  `sha256:2e313a284dcaaa6542d9d81919fc22bb61ab2015e18659f2dc0d323cbad47fd3`.
- Skill registry: strict SemVer plus package-digest pinning; six packages discovered,
  three deterministic handlers executable. A repeated `research-plan` invocation with
  the same correlation/input produces the same invocation trace.
- `safe-experiment-runner` remains discoverable but generic invocation returns
  `E_NOT_EXECUTABLE`; it cannot bypass the dedicated approval path.

The reference RXP API exposes schema catalog, synthetic demo, and uploaded-ledger
verification. It does not yet persist RXP documents into the task store, and the public
fixture HMAC key is not production signature trust.

## Strict benchmark

Committed artifact: `benchmarks/artifacts/2026-08-29-local-cpu.{json,md,sha256}`.
Five seeded repetitions produce 210 trials and semantic digest
`05cab481a525210026d07377bb841ca0cd73f27790e9856b3c29211320b6b996`.

| Profile | PASS | FAIL | SKIP | Scenario clusters | Interpretation |
|---|---:|---:|---:|---:|---|
| `deterministic-core-v0.1` | 50 | 0 | 20 | 10/14 PASS | local CPU control semantics only |
| `scripted-negative-control-v1` | 0 | 70 | 0 | 0/14 PASS | deliberately naive adapter is rejected |
| `agentteams-rxp-target` | 0 | 0 | 70 | 0/14 PASS | no live target; honestly SKIP |

The benchmark-owned oracle and schema-aware trace verifier, not the adapter exit code,
decide PASS. Top-level replay requires at least two distinct run IDs with the same
semantic digest. Running the release gate with no live evidence returns non-zero.

## AgentTeams and PostgreSQL integration evidence

- The AgentTeams bridge implements seven principals, project/task/workflow/artifact
  mapping, conflict→replan, timeout→reassign, R2 recovery, compensation, restart,
  PostgreSQL JSONB checkpoints/events/receipts, full event-chain verification, and
  Skill/RXP trace references. Forty-one contract/fault tests pass and seven pinned
  official files were SHA-256 checked. No live Controller/Matrix endpoint or same-run
  target evidence was used, so live status remains `SKIP / UNVERIFIED`.
- The Fashion-MNIST adapter defines a real one-CUDA-GPU TinyCNN FP32/AMP comparison with
  900-second, 0.25-GPU-hour, and 100-MiB limits. It binds raw predictions, latency,
  memory telemetry, environment, approval, Matrix/AgentTeams receipts, independent
  review, and Decision. Its offline verifier intentionally reports
  `CONTRACT_PASS_ORIGIN_UNVERIFIED`; there is no live metric claim.
- PostgreSQL 16.14 integration was run in a real temporary Docker container: 32/32 tests
  passed for control-plane and bridge transactions, optimistic concurrency, tenant
  isolation, candidate-only memory curation, four least-privilege roles/RLS, append-only
  ledgers, migration checksums, restart/CAS/idempotency, durable event cursors, and
  `LISTEN/NOTIFY`. PolarDB deployment and PITR restore were not run. The proof bundle
  indexes the committed local PostgreSQL report; rebuilding it does not silently rerun Docker.

## Browser and submission-artifact QA

- The RXP Cockpit was checked at 1600×1000 and 390×844. The semifinal acceptance path
  exposes AgentTeams+GPU and PostgreSQL+PolarDB tabs, matrix-cell interaction works, the
  mobile document has no horizontal overflow, and the static build console is clean.
  It visibly says `STATIC FIXTURE`, `GPU RUN · NONE` (with `NO LIVE SERVICES` in the mobile
  header), and `PRODUCTION SIGNATURE TRUST · NONE`.
- The semifinal proposal inherits and edits all 16 initial-round slides in place. The
  template-fidelity checker passes with zero issues, and all 16 speaker-note blocks carry
  sources plus talk tracks.
- PPTX render and 16-page PDF render were inspected page by page. Overflow testing finds
  only the initial template's pre-existing decorative bleed on pages 1, 10, and 11; the
  same pages are flagged in the unedited initial deck.
- Final hashes are frozen in `submission/semifinal-evidence-index.md` and regenerated in
  the deterministic submission ZIP sidecar.

## Environment limitation

The latest application-image build reached Docker Hub metadata resolution for
`python:3.9-slim`, then timed out before any repository layer executed. Compose schema,
native tests, and the separate real PostgreSQL container proof pass; the application
image itself remains **UNVERIFIED** until rebuilt on a network that can reach Docker Hub.
