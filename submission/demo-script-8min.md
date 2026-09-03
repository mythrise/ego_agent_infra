# EgoAgentOS semifinal demo script — 8 minutes

## Before recording

```bash
make demo-proof
make verify
```

Keep `submission/semifinal-evidence-index.md` open. If a live AgentTeams endpoint is not
configured, leave the target row on screen as `SKIP`; do not substitute a local fixture.

## 0:00–1:00 — Why RXP exists

Open the RXP Cockpit. Read the visible truth labels first: `STATIC FIXTURE`, `GPU RUN · NONE`
(mobile header: `NO LIVE SERVICES`), and `PRODUCTION SIGNATURE TRUST · NONE`. Explain the
failure mode: one Agent can propose, run,
measure, and write a favorable conclusion while silently omitting failed matrix cells.
These labels describe the hosted browser fixture. The repository separately contains a
real, bounded Fashion-MNIST GPU adapter, but no authenticated live output from it.

Introduce the narrow protocol claim: RXP/1 binds an experiment to
`Intent → Grant → Receipt → Evidence → Decision`; MatrixLedger proves the expected
and decided cell sets close. It complements MCP/A2A/PROV/MLflow rather than replacing them.

## 1:00–2:30 — Dynamic collaboration, not a fixed script

Show the seven principals and name at least Manager, Architect, Runtime, Evaluator, and
independent Reviewer. Walk through project creation, DAG decomposition, delegation,
acceptance, execution, verification, and terminal Decision.

Switch to the failure trace: conflict causes replan/generation change; timeout causes
bounded reassignment; crash resumes from persisted state; R2 waits for a human Grant;
failed work is compensated without erasing the original trace. State explicitly that the
official contract bridge is tested locally, while the live AgentTeams target is SKIP.
The production bridge store persists JSONB checkpoints, events, and receipts in PostgreSQL;
CAS, per-run advisory locks, receipt uniqueness, append-only triggers, and full event-chain
verification make restart and concurrency fail closed. SQLite is only the developer fallback.

## 2:30–4:15 — One token, one matrix cell

Generate and verify the deterministic protocol fixture:

```bash
uv run --python 3.9 --extra dev python -m protocols.rxp demo -o /tmp/rxp-demo.json
uv run --python 3.9 --extra dev python -m protocols.rxp verify /tmp/rxp-demo.json --demo-key
```

Show `2 / 2 COMPLETE`, 23 ledger entries, and the ledger root. Explain that a Grant binds
intent digest, run, cell, action, resource budget, expiry, and nonce. Replay, expiry,
scope mismatch, tamper, missing evidence, or self-review withholds Decision.

Then show runtime Skill discovery and invocation through OpenAPI:

1. `GET /api/v1/skills`: six packages, three executable handlers.
2. Invoke `research-plan` with its exact version and package digest; show the correlated
   input/output digests and invocation ID.
3. Invoke `safe-experiment-runner` generically; show `403 / E_NOT_EXECUTABLE`.

## 4:15–5:25 — Real GPU contract and one-command acceptance

Open `experiments/fashion_mnist_amp/config.json`. Show the frozen real Fashion-MNIST,
TinyCNN FP32-versus-AMP comparison and its hard limits: exactly one CUDA GPU, seed 42,
one physical launch, at most 900 seconds, 0.25 GPU·hour, and 100 MiB. The runner retains
every prediction and latency repetition plus GPU UUID/utilization/memory/power telemetry.

Then open `semifinal_acceptance/README.md`. Explain that the content-addressed bundle binds
Matrix messages, AgentTeams and approval receipts, raw metrics, Evidence Gate, failure/
recovery checkpoints, independent review, primary Trace, RXP Decision, and top-level
Decision. Show or run the contract tests:

```bash
make test-experiments
make test-acceptance
```

Say the terminal truth exactly: `CONTRACT_PASS_ORIGIN_UNVERIFIED` and
`live_claim_allowed=false`. Thirteen experiment tests and sixteen acceptance tests prove
fail-closed contracts, not an official AgentTeams/GPU run or model improvement.

## 5:25–6:35 — Strict benchmark and negative control

Open `benchmarks/artifacts/2026-08-29-local-cpu.md`. Point out the three profiles:

- deterministic core: 50 PASS, 20 SKIP, 10/14 clusters;
- naive negative control: 70 expected FAIL;
- live AgentTeams target: 70 SKIP, never converted to PASS.

Explain that the benchmark owns the oracle and semantic trace verifier. Run or show:

```bash
make test-benchmark
make benchmark-release EVIDENCE_DIR=/tmp/egoagentos-live-evidence
```

The second command must return non-zero without a new persistent live evidence directory
containing same-run target traces.

## 6:35–7:30 — Production-shaped state, honest boundary

Show the PostgreSQL proof: **38/38 tests passed on isolated local PostgreSQL 16** for control-plane
and AgentTeams-bridge persistence, optimistic concurrency, roles/RLS, append-only ledgers,
durable cursors, migration checksums, restart/CAS/idempotency, and `LISTEN/NOTIFY`. Show that
the Memory Curator may insert only `memory_candidates`; a separate `memory-validator`
promotes validated memory after the Evidence Gate. Immediately state what did not run:
PolarDB provisioning, provider identity, managed backup/PITR restore, measured RPO/RTO,
cloud IAM, and the latest application image build (Docker Hub metadata timeout).

## 7:30–8:00 — Close on reproducibility

Open `submission/evidence/semifinal-local-proof.json` and its `.sha256`. Finish with:

> EgoAgentOS does not ask the judge to trust an AI's narration. It makes the experiment's
> authority, completeness, evidence, recovery, and final Decision independently replayable.

End on the evidence index, repository URL, and static Demo URL. Do not claim the current
revision is deployed until GitHub Pages has been rebuilt from that exact commit.
