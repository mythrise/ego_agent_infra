# RXP Bench v1

RXP Bench measures whether an auto-research control plane turns an experiment request into
an auditable, repeatable state transition instead of an opaque final answer. It is an
infrastructure benchmark. It does not score model intelligence or claim physical GPU results.

## What actually runs

Three profiles share the same versioned 14-scenario corpus:

1. `scripted-negative-control-v1` is a deliberately unsafe fixed script. It has no approval,
   evidence, durable recovery, or lease protocol. It is a negative control, **not a measured
   agent system**. Its reproducibility and hash-agreement fields are `null`.
2. `deterministic-core-v0.1` calls the repository's real state machine, SQLite store, approval
   policy, canonical hashing, evidence gate, and audit-chain verifier.
3. `agentteams-rxp-target` calls a real integration only if the installable module
   `integrations.agentteams.benchmark_adapter` exists, declares
   `BENCHMARK_ADAPTER_VERSION = "rxp-bench/v1"`, and exports
   `run_scenario(scenario, seed, workspace)`. Otherwise every target trial is `SKIP`. No
   synthetic AgentTeams event is converted into a pass.

A target adapter runs in a subprocess with a hard timeout. A target `PASS` additionally requires
`details.execution_mode="real-agentteams"`, `details.synthetic=false`, and a digest-bound trace
inside the per-trial workspace. The benchmark-owned verifier then derives all result facts from
the trace; it ignores adapter-provided pass booleans. Missing, forged, hanging, or inconsistent
evidence becomes `ERROR`.

The normative trace is `egoagentos.agentteams-trace/v1`. It binds official AgentTeams project,
task, worker, Matrix, workflow-response and correlation identifiers to a non-empty lifecycle;
three distinct workers; Skill invocation; a runtime human approval; independent review; a final
decision; and the RXP Intent → Grant → Receipt → Evidence → Matrix chain. Scenario-specific
failure events are mandatory, so one generic successful trace cannot pass all 14 scenarios. See
[`trace-contract.md`](trace-contract.md) and
[`schemas/agentteams-rxp-trace-v1.schema.json`](schemas/agentteams-rxp-trace-v1.schema.json).

The corpus is immutable within a version:

- `benchmarks/corpus/v1/scenarios.json` contains scenario data and the fixed master seed.
- `benchmarks/corpus/v1/scenario.schema.json` documents its JSON schema.
- the runner records the corpus SHA-256 in every result.

## Run it

```bash
make benchmark
# After package installation, the equivalent entry point is: rxp-bench --strict
```

For a faster CI/local smoke run:

```bash
python -m benchmarks.runner \
  --profiles scripted-negative-control-v1,deterministic-core-v0.1,agentteams-rxp-target \
  --repetitions 2 \
  --strict \
  --output-json benchmarks/artifacts/smoke.json \
  --output-md benchmarks/artifacts/smoke.md
```

`--strict` fails when an executed deterministic-core scenario fails, the core raises an
error, or approval bypass succeeds even once. Capability gaps remain visible `SKIP`s and
reduce coverage; they do not turn CI green by being counted as passes.

A release claim is stricter and always requires a new or empty persistent evidence directory:

```bash
EVIDENCE_DIR="$PWD/release-evidence-$(date +%Y%m%dT%H%M%S)" make benchmark-release
```

Each target trial is stored as
`<profile>/<scenario>/repetition-NNN/{manifest.json,trace.json}`. The release gate independently
replays every bundle and fails if a bundle is absent, altered, stale, or mismatched with the
result JSON. Do not commit credentials or raw private experiment data into this directory.

## Metrics and denominators

Each raw trial records status, fixed seed, measured wall time, operation count, assertion
evidence, implementation path, content roots, and only the metrics that scenario can expose.
`null` means not measured, never zero.

| Metric | Definition |
|---|---|
| Task completion | Completed / trials with an explicit completion outcome |
| Unsafe action block | Blocked / executed adversarial actions |
| Approval bypass | Successful bypasses / executed approval attacks; required value is **0** |
| Exactly once | Single committed side effect / trials exposing duplication |
| Trace completeness | Required correlated lifecycle event types present / required types |
| Evidence completeness | Verified AgentTeams, HITL, review, decision, official-response and RXP bindings |
| Recovery and MTTR | Recovered / recovery trials; process reopen-to-valid-state wall time |
| Reproducibility | Equal semantic projections across two independently identified runs |
| Hash agreement | Equal canonical SHA-256 for those projections |
| Dynamic routing | Successful replan or reassignment / trials exposing conflict or timeout |
| Cost and latency | Measured wall time and operation count; external cost is `null` without a billing meter |

Binary rates include Wilson 95% confidence intervals after collapsing repetitions by scenario.
Continuous metrics first average repetitions inside each scenario, then bootstrap over scenario
clusters with 2,000 fixed-seed resamples. Reports show PASS / FAIL / ERROR / SKIP counts and the
all-trial, attempted, measured, and scenario-cluster denominators. Repetitions measure this
implementation's stability on a fixed corpus, not generalization to arbitrary research tasks.

## Truth boundary

- Benchmark payloads and the EgoLite workflow are synthetic.
- No GPU is requested or used by the committed baseline artifact.
- No external LLM or AgentTeams service is called unless its real adapter is installed.
- A missing target, monetary meter, or capability is `SKIP`/`null`, not inferred.
- Raw JSON is canonical UTF-8 JSON: sorted keys, compact separators, NaN forbidden.
- The semantic digest excludes wall time, MTTR, and diagnostic details. It includes the verified
  `trace_root` and `evidence_root`, so a different trace hash produces a different result digest.
- `--release-gate` without `--evidence-dir` always fails. An in-memory PASS is not release proof.

See [semifinal-score-mapping.md](semifinal-score-mapping.md) for the competition mapping and
`benchmarks/artifacts/` for committed local evidence.
