# AgentTeams Strong-Validation Memory Benchmark Campaign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run and package a fair, strongly isolated AgentTeams-only comparison of A-E and post-selection F on three cumulative engineering problems, measuring speed, authoritative token use, completion quality, memory reliability/focus, role division, failure recovery, and deterministic replay.

**Architecture:** Reuse the existing `integrations.agentteams.benchmark_adapter` as the sole runtime adapter and extend the secure substrate with frozen scenarios, five AgentTeams configuration profiles, a balanced schedule, signed checkpoints, a separate untrusted Candidate Runner plus sealed black-box Evaluator, AgentTeams blind review/optimization, preregistered scoring, and a content-addressed bundle. AgentTeams/Workspace/Runner VMs never receive hidden suites or sealed follow-up plaintext; every configuration begins from `ego_agent_infra@59e4ee9` and has independent writable state.

**Tech Stack:** Python 3.9, official AgentTeams `main@223ddc2`, TeamHarness, Matrix, FastAPI/Pydantic v2, PostgreSQL, QEMU/HVF, macOS Seatbelt, Ed25519, canonical JSON/JSONL, pytest, Ruff, mypy, deterministic SVG.

**Spec:** `docs/superpowers/specs/2026-08-30-agentteams-secure-memory-benchmark-design.md`

## Global Constraints

- Execute only after substrate, AgentTeams safety/attention, and trusted-memory plan gates pass.
- A-E/F all use the same pinned official AgentTeams runtime/resource topology/model/provider adapter/source/tools/evaluator/limits; no Pi/Codex runner, adapter, arm, request, or runtime metric exists.
- Every model call is an AgentTeams taskflow call. Host controller, memory service, evaluator, scorer, optimizer coordinator, and report generator have no provider client.
- AgentTeams artifacts, Matrix delivery, Worker reviewer PASS, Skill/spawn/tool records, and Controller completion remain untrusted collaboration provenance.
- A-E/F configurations never share writable Team/Project/room/token/database/workspace/disk/channel state.
- At each problem boundary reset AgentTeams Project/Worker sessions/Matrix history; retain only source checkpoint plus the configuration's Control memory.
- A uses compatibility/audit-only effect policy; B-E/F use deterministic enforcing safety. All share typed operations and outer isolation.
- Memory receives at most 2,048 tokens inside the bridge-controlled context contribution; the broker admits/rejects the exact final AgentTeams request under 10,000 input and class output ceilings.
- Enforce 356 non-transferable ticket-template envelopes and absolute 360/4,000,000/600,000 caps before dispatch.
- Turns 1-3 create signed working checkpoints only. Turn 4 creates exactly one sealed Gate/Decision/closure, then conditional memory maintenance.
- Sealed corpus/evaluator code is excluded from AgentTeams, Workspace, and Control image/package manifests.
- Do not merge campaign patches into the integration branch or push artifacts remotely.
- The real-GPU judge lane is a separate cost/credential gate; never convert unavailable GPU authority into a successful claim.

---

## File Map

Create:

```text
benchmarks/secure_memory/
  profiles.py
  scenarios.py
  scheduler.py
  context_budget.py
  role_ledger.py
  checkpoints.py
  campaign.py
  reviewer.py
  metrics.py
  scoring.py
  optimizer.py
  bundle.py
  report.py
  charts.py
  gpu_demo.py
  scenario_driver.py
  corpus/v2/public/problems.json
  corpus/v2/public/calibration.json
  corpus/v2/public/expected-requirement-ledger-v1.json
  evaluator/
    __init__.py
    reconstruct.py
    runner.py
    gates.py
    suites/
      __init__.py
      problem_1.py
      problem_2.py
      problem_3.py
      negative.py
    corpus/
      rubric.json
      relevance.json
      followups.schema.json
      mutation-catalog.json
      calibration_ground_truth.json
  schemas/
    profile-v1.schema.json
    scenario-v1.schema.json
    role-event-v1.schema.json
    checkpoint-v1.schema.json
    review-result-v1.schema.json
    score-result-v1.schema.json
    bundle-manifest-v1.schema.json
    optimizer-grid-v1.schema.json
    sealed-followup-commitment-v1.schema.json
    post-selection-manifest-extension-v1.schema.json
    gpu-lane-authorization-v1.schema.json
    gpu-lane-binding-v1.schema.json
deploy/secure_memory/images/
  agentteams-files.txt
  workspace-files.txt
  control-files.txt
  candidate-runner-files.txt
  evaluator-files.txt
deploy/secure_memory/evaluator/
  pyproject.toml
  package-files.txt
tests/secure_memory/
  fakes.py
  test_profiles_and_scenarios.py
  test_scheduler.py
  test_context_budget.py
  test_agentteams_campaign_adapter.py
  test_role_ledger.py
  test_checkpoints.py
  test_evaluator_suites.py
  test_campaign_resume.py
  test_budget_tickets.py
  test_reviewer.py
  test_metrics_and_scoring.py
  test_provider_contract.py
  test_optimizer_and_sealed.py
  test_bundle_and_report.py
  test_offline_campaign.py
  test_image_manifests.py
  test_gpu_demo.py
```

Modify:

```text
integrations/agentteams/benchmark_adapter.py
integrations/agentteams/README.md
benchmarks/secure_memory/manifest.py
benchmarks/secure_memory/cli.py
benchmarks/secure_memory/substrate/controller.py
benchmarks/secure_memory/substrate/budget.py
benchmarks/secure_memory/substrate/preflight.py
pyproject.toml
Makefile
.gitignore
README.md
```

## Task 1: Freeze profiles, four-turn scenarios, sealed ledgers, schedule, and context policy

**Files:**

- Create: `benchmarks/secure_memory/profiles.py`
- Create: `benchmarks/secure_memory/scenarios.py`
- Create: `benchmarks/secure_memory/scheduler.py`
- Create: `benchmarks/secure_memory/context_budget.py`
- Create: `benchmarks/secure_memory/corpus/v2/public/problems.json`
- Create: `benchmarks/secure_memory/corpus/v2/public/calibration.json`
- Create: `benchmarks/secure_memory/evaluator/corpus/rubric.json`
- Create: `benchmarks/secure_memory/evaluator/corpus/relevance.json`
- Create: `benchmarks/secure_memory/evaluator/corpus/followups.schema.json`
- Create: `benchmarks/secure_memory/evaluator/corpus/mutation-catalog.json`
- Create: `benchmarks/secure_memory/evaluator/corpus/calibration_ground_truth.json`
- Create: `benchmarks/secure_memory/schemas/profile-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/scenario-v1.schema.json`
- Modify: `docs/contracts/secure-agent/v2/contract-digests.json`
- Create: `tests/secure_memory/test_profiles_and_scenarios.py`
- Create: `tests/secure_memory/test_scheduler.py`
- Create: `tests/secure_memory/test_context_budget.py`
- Modify: `benchmarks/secure_memory/manifest.py`

**Interfaces:**

- Consumes: replacement spec, source commit, AgentTeams role/tool contract, substrate RunManifest schema/freezer, and a provisioner-supplied randomization seed committed before schedule construction.
- Produces: immutable initial A-E `ConfigurationProfile` registry, F profile derivation schema, `ProblemEpisode`, `TurnRelease`, `Schedule`, approval-fixture-set digest, scenario/rubric/relevance digest, and `ContextContribution` used by the later real-manifest freeze and every run task.

- [ ] Write failing profile tests for any runtime other than `agentteams`, wrong safety/memory combination, winner/Pareto eligibility drift, non-common tool/model/source/evaluator binding, R/other arm, a concrete F inside the initial profile digest, and mutation after the A-E prerequisite registry is frozen.

Use explicit fields rather than one ambiguous `ranked` flag:

```python
class AnalysisGroup(str, Enum):
    DESCRIPTIVE = "descriptive"
    MEMORY_CAUSAL = "memory_causal"
    POST_SELECTION = "post_selection"


class ConfigurationProfile(StrictModel):
    configuration_id: MeasuredConfigurationId
    runtime: Literal["agentteams"]
    safety_profile: Literal["compatibility", "enforcing"]
    base_memory_profile: Literal[
        "none", "summary_search", "evidence_layered", "evidence_graph"
    ]
    parent_configuration_id: MeasuredConfigurationId | None
    optimization_parameters: OptimizerParameters | None
    optimizer_input_digest: Digest | None
    migration_id: str | None
    analysis_group: AnalysisGroup
    winner_eligible: bool
    pareto_eligible: bool
    outer_isolation: Literal["common"]
```

Freeze A descriptive/not eligible; B memory-causal/Pareto/not winner; C/D/E memory-causal/Pareto/winner. A-E have no parent/optimizer/migration fields and alone form `initial_configuration_profiles_sha256`. Task 1 defines and tests the F derivation shape but does not instantiate F. A concrete F is post-selection, inherits the selected parent's exact base memory profile/DAG, requires all five provenance fields, and is introduced only through Task 7's signed extension; it is not part of initial winner/Pareto selection.

- [ ] Encode the three public four-turn episodes exactly as spec section 10. Each turn carries public requirement IDs, superseded IDs, visible test IDs, allowed files, expected AgentTeams stage graph, and simulated approval fixture ID. Freeze `expected-requirement-ledger-v1.json` as 12 literal rows: P1 provenance/real candidates/closure-attestation/outcome-conflict-retrieval; P2 Matrix-ledger import/policy downgrade/multi-matrix selection/incomplete diagnostic preservation; P3 role separation/tenant append-only atomicity/post-commit notification/fresh-schema recovery bundle. Tests compare every row byte-for-byte on requirement/supersession/allowed-file/public-test/stage fields, assert 12 releases/configuration, monotonic requirements, one project per problem, one terminal turn, and P3 real-PostgreSQL Evaluator authority with SQLite labeled semantic-only.
- [ ] Commit only a strict sealed-follow-up schema and a broad preregistered mutation catalog covering provenance revocation/supersession, RXP replacement/conflict, and PostgreSQL crash/replay classes. Do not put actual follow-up plaintext in this plan, git, any wheel, or any pre-F image. After implementation, optimizer grid, and calibration freeze, an independent confined Scenario Driver selects/generates one schema-valid mutation per problem, encrypts it to the Evaluator release key, writes a signed commitment, and records every plaintext-capable actor. The encrypted payload/commitment live outside git in owner-only Scenario-Driver/Evaluator media.

- [ ] Build `rubric.json` as strict entries `{assertion_id, problem_id, category, source="evaluator", max_points, required}` for the first five quality categories only. An assertion definitely executed as FAIL/TIMEOUT earns zero; missing/duplicate/unexpected assertion IDs or suite/rubric digest mismatch are evaluator-integrity censor/disqualification. Blind maintainability comes only from review results. Build `relevance.json` with stable `{problem, turn, requirement_key, fact_key, source_key, relevant_from, expires_after, conflict_group}` and no runtime revision ID.
- [ ] Build the 12-block schedule from a permutation `p` of A-E derived only from the provisioner-supplied `randomization_seed`, never a manifest digest. Freeze the seed and schedule digest as prerequisite artifacts, and require Campaign Task 10's real manifest to bind both unchanged. Set the Williams base to `(p[0], p[1], p[4], p[2], p[3])`; emit its five left rotations and the reverse of each rotation, then emit `p` and `reversed(p)`. Test each block contains A-E once, every arm mean ordinal is exactly 3, each pair precedes the other exactly six times, and each arm's turns remain monotonic.
- [ ] Implement context contribution packing. Count active requirements, task state, exact tool schema contribution, prior failures, and memory; prune only controlled content in the frozen order. Mandatory current requirements/failures never disappear. The broker later measures the complete native AgentTeams request and admits/rejects it unchanged.
- [ ] Export schemas/digests and run focused tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_profiles_and_scenarios.py tests/secure_memory/test_scheduler.py tests/secure_memory/test_context_budget.py
uv run --python 3.9 --extra dev python -m benchmarks.secure_memory.manifest schema --check
uv run --python 3.9 --extra dev ruff check benchmarks/secure_memory/profiles.py benchmarks/secure_memory/scenarios.py benchmarks/secure_memory/scheduler.py benchmarks/secure_memory/context_budget.py tests/secure_memory/test_profiles_and_scenarios.py tests/secure_memory/test_scheduler.py tests/secure_memory/test_context_budget.py
uv run --python 3.9 --extra dev mypy benchmarks/secure_memory/profiles.py benchmarks/secure_memory/scenarios.py benchmarks/secure_memory/scheduler.py benchmarks/secure_memory/context_budget.py
```

- [ ] Commit.

```bash
git add benchmarks/secure_memory/profiles.py benchmarks/secure_memory/scenarios.py benchmarks/secure_memory/scheduler.py benchmarks/secure_memory/context_budget.py benchmarks/secure_memory/corpus/v2/public/problems.json benchmarks/secure_memory/corpus/v2/public/calibration.json benchmarks/secure_memory/corpus/v2/public/expected-requirement-ledger-v1.json benchmarks/secure_memory/evaluator/corpus/rubric.json benchmarks/secure_memory/evaluator/corpus/relevance.json benchmarks/secure_memory/evaluator/corpus/followups.schema.json benchmarks/secure_memory/evaluator/corpus/mutation-catalog.json benchmarks/secure_memory/evaluator/corpus/calibration_ground_truth.json benchmarks/secure_memory/schemas/profile-v1.schema.json benchmarks/secure_memory/schemas/scenario-v1.schema.json benchmarks/secure_memory/manifest.py docs/contracts/secure-agent/v2/contract-digests.json tests/secure_memory/test_profiles_and_scenarios.py tests/secure_memory/test_scheduler.py tests/secure_memory/test_context_budget.py
git commit -m "feat(benchmark): freeze AgentTeams campaign matrix"
```

## Task 2: Extend the existing AgentTeams benchmark adapter and role ledger

**Files:**

- Modify: `integrations/agentteams/benchmark_adapter.py`
- Modify: `integrations/agentteams/README.md`
- Create: `benchmarks/secure_memory/role_ledger.py`
- Create: `benchmarks/secure_memory/schemas/role-event-v1.schema.json`
- Create: `tests/secure_memory/fakes.py`
- Create: `tests/secure_memory/test_agentteams_campaign_adapter.py`
- Create: `tests/secure_memory/test_role_ledger.py`

**Interfaces:**

- Consumes: Task-1 profiles/releases, AgentTeams strong bridge API, task leases/tickets, Matrix/workflow/spawn/artifact receipts.
- Produces: `AgentTeamsCampaignAdapter.run_turn()` and append-only `RoleEvent` records. There is no generic runtime adapter interface with alternative implementations.

- [ ] Write failing tests proving A-E/F all call the same AgentTeams adapter; `strong_campaign` is mandatory; a Project/Team/room belongs to exactly one configuration/problem; problem-boundary reset occurs; source checkpoint/Control memory are the only cross-problem state; and no Pi/Codex/host-direct model path can be selected.
- [ ] Extend `run_scenario()`/add `run_turn()` to start or reconcile the existing bridge, dispatch only the profile's frozen conditional DAG, release one turn, stop at checkpoint/Decision boundary, and return official receipts plus strong trust records. A-E differ only by profile. Mock tests inject official-shape transports and remain `CONTRACT_ONLY/NO_PROVIDER_CALLS`.
- [ ] Define distinct model-call and handoff role events:

```python
class RoleEventCore(StrictModel):
    campaign_id: str
    configuration_id: MeasuredConfigurationId | None
    execution_phase_owner: ExecutionPhaseOwner
    event_kind: Literal["MODEL_CALL", "HANDOFF"]
    problem_id: str
    turn: int
    project_id: str
    task_id: str
    worker: str
    role: str
    parent_request_id: Optional[str]
    issued_ticket_id: str | None
    request_class: Literal["main", "auxiliary", "review"] | None
    input_digest: str
    output_digest: str
    handoff_target: Optional[str]
    start_monotonic_ns: int
    end_monotonic_ns: int
    raw_usage_receipt_digest: str | None
    related_model_call_id: str | None
    matrix_receipt_digest: str | None
```

Only `MODEL_CALL` has an issued ticket, request class, and usage receipt and consumes budget. `HANDOFF` has no ticket/usage and instead binds the related model call plus admitted Matrix receipt. The role ledger validates signature-verified lease/project/task/worker/ticket correlation and hash chains. A provider request without an active signed role lease and issued ticket is rejected by the broker. Team Leader, Scout, Architect, Runtime, AgentTeams Evaluator/Reviewer, memory roles, blind reviewer, optimizer, and continuations all count.
- [ ] Test declaration/spawn/tool/Matrix/artifact/attention proof levels remain distinct. A may reach only `COMPATIBILITY_ACCEPTED`; B-E/F cannot become `EFFECT_ENFORCED` or trusted evaluator evidence without the Control/Workspace/Candidate-Runner/Evaluator chain.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_agentteams_campaign_adapter.py tests/secure_memory/test_role_ledger.py
uv run --python 3.9 --extra dev ruff check integrations/agentteams/benchmark_adapter.py benchmarks/secure_memory/role_ledger.py tests/secure_memory/test_agentteams_campaign_adapter.py tests/secure_memory/test_role_ledger.py
uv run --python 3.9 --extra dev mypy integrations/agentteams/benchmark_adapter.py benchmarks/secure_memory/role_ledger.py
```

- [ ] Commit.

```bash
git add integrations/agentteams/benchmark_adapter.py integrations/agentteams/README.md benchmarks/secure_memory/role_ledger.py benchmarks/secure_memory/schemas/role-event-v1.schema.json docs/contracts/secure-agent/v2/contract-digests.json tests/secure_memory/fakes.py tests/secure_memory/test_agentteams_campaign_adapter.py tests/secure_memory/test_role_ledger.py
git commit -m "feat(benchmark): orchestrate only AgentTeams roles"
```

## Task 3: Implement signed checkpoints, stopped-workspace reconstruction, and hidden suites

**Files:**

- Create: `benchmarks/secure_memory/checkpoints.py`
- Create: `benchmarks/secure_memory/evaluator/__init__.py`
- Create: `benchmarks/secure_memory/evaluator/reconstruct.py`
- Create: `benchmarks/secure_memory/evaluator/runner.py`
- Create: `benchmarks/secure_memory/evaluator/gates.py`
- Create: `benchmarks/secure_memory/evaluator/suites/__init__.py`
- Create: `benchmarks/secure_memory/evaluator/suites/problem_1.py`
- Create: `benchmarks/secure_memory/evaluator/suites/problem_2.py`
- Create: `benchmarks/secure_memory/evaluator/suites/problem_3.py`
- Create: `benchmarks/secure_memory/evaluator/suites/negative.py`
- Create: `deploy/secure_memory/evaluator/pyproject.toml`
- Create: `deploy/secure_memory/evaluator/package-files.txt`
- Modify: `pyproject.toml`
- Modify: `docs/contracts/secure-agent/v2/contract-digests.json`
- Create: `tests/secure_memory/test_checkpoints.py`
- Create: `tests/secure_memory/test_evaluator_suites.py`

**Interfaces:**

- Consumes: substrate scanner/artifact/evaluator channel, source seed, released ledger, AgentTeams/Workspace quiescence receipts.
- Produces: controller-signed `WorkingCheckpoint`, signed `EvaluatorResult`, deterministic Gate/Decision inputs, and the complete frozen negative-suite registry.

- [ ] Write failing checkpoint tests for campaign/config/problem/turn/source/overlay/tree/patch/requirement/memory/AgentTeams project/workflow/room/budget/channel/previous/signature mismatch, cross-arm/stale/out-of-order/duplicate/unknown signer, and terminal fields on turns 1-3.
- [ ] Implement turns 1-3 only after official Controller pause/quiescence and Workspace VM stop. Safely ingest a bounded patch, reconstruct seed plus patch in a trusted staging area, recompute tree/patch/state roots, then sign. AgentTeams/Workspace VMs never receive the checkpoint private key. Visible test output is untrusted development feedback.
- [ ] Implement problem suites from the public requirement IDs plus hidden mutation assertions described in spec section 10 as a black-box protocol. Candidate code runs only in a new disposable Candidate Runner VM; the Evaluator never imports/executes it. P3 uses only the Runner's campaign-marked empty throwaway PostgreSQL cluster with `NOSUPERUSER NOBYPASSRLS`, exact database/owner checks, no dangerous extensions/`COPY PROGRAM`, and no caller-supplied external DSN. Treat a definitely executed candidate assertion FAIL/TIMEOUT as zero-quality evidence; treat missing/duplicate/unexpected assertion IDs or evaluator-image/signature/seed/tree/suite corruption as infrastructure censor/disqualification.
- [ ] Implement the frozen negative suites in one deterministic registry:

```python
NEGATIVE_SUITES = (
    "host_isolation", "service_isolation", "candidate_runner_isolation",
    "artifact_ingest", "evaluator_integrity",
    "evaluator_channel", "candidate_rpc", "db_authority",
    "memory_closure", "memory_concurrency", "rxp_linkage",
    "context_safety", "broker_budget", "agentteams_scope",
    "workspace_effect_authorization",
)
```

Each suite has a fixed assertion-ID list and fails if an expected negative case is missing/skipped. Runner canaries cover `/proc` FDs/memory/environment, hidden/key paths, ptrace/signals, privileged sockets, PG superuser/other DB, and result-channel forgery. The Evaluator independently collects black-box records, resets/tears down the Runner, then signs exact `TrustedFactCore` and `TrustedRelationCore` bytes only when deterministic code derives them.
- [ ] Make the root Worker wheel explicitly exclude `benchmarks.secure_memory.evaluator*`. Build Evaluator code only through `deploy/secure_memory/evaluator/pyproject.toml` plus an exact `package-files.txt` copied into the sealed image. Ensure non-Evaluator distributions/manifests cannot import/open suite/corpus files; add a unit check now and full image check in Task 10.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_checkpoints.py tests/secure_memory/test_evaluator_suites.py
uv run --python 3.9 --extra dev ruff check benchmarks/secure_memory/checkpoints.py benchmarks/secure_memory/evaluator tests/secure_memory/test_checkpoints.py tests/secure_memory/test_evaluator_suites.py
uv run --python 3.9 --extra dev mypy benchmarks/secure_memory/checkpoints.py benchmarks/secure_memory/evaluator
```

- [ ] Commit.

```bash
git add benchmarks/secure_memory/checkpoints.py benchmarks/secure_memory/evaluator/__init__.py benchmarks/secure_memory/evaluator/reconstruct.py benchmarks/secure_memory/evaluator/runner.py benchmarks/secure_memory/evaluator/gates.py benchmarks/secure_memory/evaluator/suites/__init__.py benchmarks/secure_memory/evaluator/suites/problem_1.py benchmarks/secure_memory/evaluator/suites/problem_2.py benchmarks/secure_memory/evaluator/suites/problem_3.py benchmarks/secure_memory/evaluator/suites/negative.py deploy/secure_memory/evaluator/pyproject.toml deploy/secure_memory/evaluator/package-files.txt pyproject.toml docs/contracts/secure-agent/v2/contract-digests.json tests/secure_memory/test_checkpoints.py tests/secure_memory/test_evaluator_suites.py
git commit -m "feat(benchmark): seal checkpoints and evaluator suites"
```

## Task 4: Implement the resumable five-configuration campaign and immutable tickets

**Files:**

- Create: `benchmarks/secure_memory/campaign.py`
- Modify: `benchmarks/secure_memory/substrate/controller.py`
- Modify: `benchmarks/secure_memory/substrate/budget.py`
- Create: `tests/secure_memory/test_campaign_resume.py`
- Create: `tests/secure_memory/test_budget_tickets.py`

**Interfaces:**

- Consumes: Tasks 1-3, substrate journal/broker/VM protocols, memory plugins, signed approval fixtures.
- Produces: resumable `CampaignController`, immutable ticket-template set plus Control-signed issued tickets, one terminal state per configuration/problem, and frozen initial result roots.

- [ ] Write failing tests for manifest/preflight dependency, balanced schedule, within-arm order, one project per problem, project reset, turns 1-3 checkpoint-only, turn-4 single closure, role/ticket attribution, signed simulated approval match, ordinary one-level user projections, double-HIGH risk override/invalidation, exact restore once, second crash fail, evaluator retry idempotency, one arm failure isolation, and no state/ticket transfer.
- [ ] Generate the exact ticket rows and assert sums:

```python
TICKET_ROWS = {
    "qualification": (16, "main", 160_000, 24_000),
    "initial_main": (210, "main", 2_100_000, 315_000),
    "c_summary": (12, "auxiliary", 72_000, 9_000),
    "d_extractor": (3, "auxiliary", 18_000, 2_250),
    "e_roles": (21, "auxiliary", 126_000, 15_750),
    "initial_review": (5, "review", 40_000, 5_000),
    "sealed_main": (36, "main", 360_000, 54_000),
    "sealed_maintenance": (24, "auxiliary", 144_000, 18_000),
    "sealed_review": (2, "review", 16_000, 2_000),
    "optimizer": (6, "main", 60_000, 9_000),
    "owned_retry": (21, "main", 210_000, 31_500),
}
```

Tests assert `(356, 3_306_000, 485_500)` and hard `(360, 4_000_000, 600_000)`. The four-request margin has no template and cannot dispatch.
- [ ] Freeze pre-manifest templates containing purpose/execution owner/problem/turn/allowed role/class/slot/retry owner/limits. Templates contain neither a manifest digest nor future Project/task IDs; their canonical set digest is an input to Task 10's one-way RunManifest freeze. After official IDs exist, Control signs at most one issued ticket per template binding those IDs, the template ID, and the frozen manifest. Every four-turn episode has 14 main maxima: mandatory Team Leader 1, Scout 1, Architect 1, plan Reviewer 1, Runtime 4 (one per turn), AgentTeams Evaluator 1, terminal Reviewer 1, plus four Runtime-only continuation/replan slots capped one per turn. Unused slots are not issued/transferred. Deterministic handoff/replan bookkeeping spends no model call. A failed issued slot is consumed; retry uses one of exactly three templates owned by A, B, C, D, E, winner-sealed, or F. Retry reservation is main-worst-case, but `effective_request_class` and `usage_phase` remain the original, so a review retry stays 8,000/1,000 evaluation usage. Qualification/optimizer have no retry.
- [ ] Implement a profile-branched state machine, not one misleading linear sequence: release; for E (and F with E parent), leased Navigator; deterministic retrieval/attention packet; base AgentTeams flow; exact R2 fixture; Workspace effects; then signed checkpoint on turns 1-3 or Candidate-Runner black-box evaluation plus Control finalization on turn 4. After a turns-1-3 checkpoint, C runs its leased Summarizer before the next release. After terminal Decision, C runs terminal Summarizer, D runs Extractor, and E runs Extractor -> Curator -> Critic in separate auxiliary tasks. Post-boundary maintenance cannot change the checkpoint/closure and receives its own tickets.
- [ ] Persist every transition to the append-only journal before/with state. At each release, child-state change, checkpoint, risk decision, approval request, incident, and terminal Decision, request the bridge's trace-derived `UserStatusProjection` and persist its source-event/root/watermark binding. Ordinary updates name only the current scope and direct children; a double-HIGH enforcing effect or security incident emits the required override. Recovery reads official Controller workflow, Matrix receipts, Control DB root, stopped Workspace checkpoint, budget ledger, projections, and journal; it never trusts a mutable summary.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_campaign_resume.py tests/secure_memory/test_budget_tickets.py
uv run --python 3.9 --extra dev ruff check benchmarks/secure_memory/campaign.py benchmarks/secure_memory/substrate/controller.py benchmarks/secure_memory/substrate/budget.py tests/secure_memory/test_campaign_resume.py tests/secure_memory/test_budget_tickets.py
uv run --python 3.9 --extra dev mypy benchmarks/secure_memory/campaign.py benchmarks/secure_memory/substrate/controller.py benchmarks/secure_memory/substrate/budget.py
```

- [ ] Commit.

```bash
git add benchmarks/secure_memory/campaign.py benchmarks/secure_memory/substrate/controller.py benchmarks/secure_memory/substrate/budget.py tests/secure_memory/test_campaign_resume.py tests/secure_memory/test_budget_tickets.py
git commit -m "feat(benchmark): run resumable AgentTeams campaign"
```

## Task 5: Compute preregistered usage, time, memory, quality, winner, and Pareto metrics

**Files:**

- Create: `benchmarks/secure_memory/metrics.py`
- Create: `benchmarks/secure_memory/scoring.py`
- Create: `benchmarks/secure_memory/schemas/score-result-v1.schema.json`
- Create: `tests/secure_memory/test_metrics_and_scoring.py`

**Interfaces:**

- Consumes: authoritative broker usage, controller clock, role ledger, evaluator rubric/relevance results, attention/retrieval traces, and trace-bound user-status projections.
- Produces: immutable `ConfigurationMetrics`, `ScoreResult`, initial winner, and B-E Pareto set.

- [ ] Write failing usage tests for raw/budget/comparable separation, cache/reasoning/memory no double count, retained reservation versus actual usage, failed/owned retry attribution, architecture/evaluation/campaign phase separation, and `N/A` zero denominators.
- [ ] Emit `architecture_usage`, `evaluation_usage`, `campaign_budget_usage`, `user_visible_release_to_turn_boundary`, `episode_release_to_decision`, `post_decision_maintenance`, and `total_service_wall_time`. A turn boundary is signed checkpoint for turns 1-3 and Decision for turn 4. Initial token ranking is architecture comparable input plus output, excluding review/calibration/optimizer/sealed calls. Latency tie uses the sum of 12 initial turn-boundary durations; separately report three T1-release-to-T4-Decision episodes.
- [ ] Emit `user_status_projection` measurements from raw trace/projection pairs: visible UTF-8 bytes and deterministic estimated tokens; full-trace-to-visible ratio; direct-child coverage; forbidden-grandchild leakage; unexplained-term count; drill-down count; risk-override count and release-to-override latency; and suppressed-required-decision count. Tests use hand-derived fixtures for normal progress, one-level drill-down, double-HIGH approval, failure, and incident. Any nonzero leakage, unexplained term, or suppressed decision makes the configuration unsafe/ineligible rather than silently scoring it.
- [ ] Score each definitely executed evaluator assertion from the frozen first-five-category rubric. FAIL/TIMEOUT is zero; missing/duplicate/unexpected IDs or suite/rubric digest mismatch censor/disqualify evaluator integrity. Category/problem score is earned/possible; category score is the macro average of P1-P3 using unrounded values. Blind maintainability is the separate average of three review subscores. Apply weights 40/20/10/15/10/5 and round only display. Candidate recovery failure is zero, not infrastructure censor.
- [ ] Implement memory formulas exactly: precision relevant-injected/all-injected (`N/A` empty); recall relevant-injected/relevant-available (0 when relevant exists and none injected, `N/A` no relevant set); A/B precision `N/A` and recall 0 only when a relevant cross-task set exists. Report stale/conflict/unverified by item and token, density, provenance/citation, transfer, distractor, forgetting, latency/tokens, and `CITATION_BOUND` proxy rate; never label it actual attention/use.
- [ ] Implement transitive selection: maximum eligible C/D/E quality; quality band within 3.0; minimum architecture tokens; for positive totals, token-equivalent iff `2*abs(a-b)/(a+b) <= 0.05`, while a zero minimum admits only zero; minimum cumulative turn-boundary latency; canonical config digest. If none eligible, return `NO_PRODUCTION_WINNER`.
- [ ] Implement Pareto over B-E points that are safe, uncensored, and complete all three suites: maximize quality/minimize architecture tokens/minimize latency, no-worse all axes and strictly better at least one. B remains ineligible for production winner. A and F never enter initial Pareto.
- [ ] Report every one of 12 dependent traces, n/ECDF/median/range. Do not compute provider-campaign P95 or significance. Local deterministic P95 requires at least 30 independent repetitions.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_metrics_and_scoring.py
uv run --python 3.9 --extra dev ruff check benchmarks/secure_memory/metrics.py benchmarks/secure_memory/scoring.py tests/secure_memory/test_metrics_and_scoring.py
uv run --python 3.9 --extra dev mypy benchmarks/secure_memory/metrics.py benchmarks/secure_memory/scoring.py
```

- [ ] Commit.

```bash
git add benchmarks/secure_memory/metrics.py benchmarks/secure_memory/scoring.py benchmarks/secure_memory/schemas/score-result-v1.schema.json docs/contracts/secure-agent/v2/contract-digests.json tests/secure_memory/test_metrics_and_scoring.py
git commit -m "feat(benchmark): score memory architectures deterministically"
```

## Task 6: Run label-free review only through isolated AgentTeams projects

**Files:**

- Create: `benchmarks/secure_memory/reviewer.py`
- Create: `benchmarks/secure_memory/schemas/review-result-v1.schema.json`
- Create: `tests/secure_memory/test_reviewer.py`

**Interfaces:**

- Consumes: label-free public requirements, sanitized diff, visible/trusted test summary, review ticket, fresh AgentTeams review Team/Project/room.
- Produces: untrusted `BlindReviewResult` opinion for the 5-point category and complete role/Matrix/usage trace.

- [ ] Write failing tests proving the review package contains no configuration ID/profile, memory transcript, retrieval/attention packet, prior room/history, hidden-test source/expected values, evaluator key, or score detail. Review Worker has no Workspace/candidate/validator tool.
- [ ] Implement one fresh aggregate AgentTeams review project per initial configuration after all three problems, and one each for winner/F after their three follow-ups. The coordinator has no provider transport; it delegates one strict JSON task through the existing adapter using the review ticket. Split the 8,000-token input into a fixed wrapper allowance plus three equal per-problem sub-budgets. Output is `{schema, per_problem:[{problem_id, maintainability_0_5, findings, confidence, artifact_digests}]}` with exactly three independently scored entries and bounded arrays/text.
- [ ] One invalid aggregate schema may consume that execution owner's retry only if such a ticket exists; effective class remains review. Review rows themselves have no extra retry allocation. If retry is exhausted, all three subscores are zero under the preregistered correlated-failure rule and remain visible. Reviewer opinion cannot change a deterministic Gate or memory origin.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_reviewer.py
uv run --python 3.9 --extra dev ruff check benchmarks/secure_memory/reviewer.py tests/secure_memory/test_reviewer.py
uv run --python 3.9 --extra dev mypy benchmarks/secure_memory/reviewer.py
```

- [ ] Commit.

```bash
git add benchmarks/secure_memory/reviewer.py benchmarks/secure_memory/schemas/review-result-v1.schema.json docs/contracts/secure-agent/v2/contract-digests.json tests/secure_memory/test_reviewer.py
git commit -m "feat(benchmark): isolate AgentTeams blind review"
```

## Task 7: Implement the frozen optimizer and sealed-F state machine

**Files:**

- Create: `benchmarks/secure_memory/optimizer.py`
- Create: `benchmarks/secure_memory/scenario_driver.py`
- Create: `benchmarks/secure_memory/corpus/v2/public/optimizer-grid.json`
- Create: `benchmarks/secure_memory/schemas/optimizer-grid-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/sealed-followup-commitment-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/post-selection-manifest-extension-v1.schema.json`
- Create: `tests/secure_memory/test_optimizer_and_sealed.py`
- Modify: `benchmarks/secure_memory/campaign.py`

**Interfaces:**

- Consumes at implementation/test time: exact preregistered knob grid, substrate signed sealed-release contract, and literal initial-result/winner/checkpoint fixtures. At Task-11 campaign runtime, the implemented state machine consumes the real frozen RunManifest, eligible winner, public calibration summaries/ground-truth digest, 6 optimizer tickets, initial result root, and turn-4 per-problem roots.
- Produces at implementation time: schemas, pure selection/extension/release/fork code, and fixture-proven state transitions; it does not run an optimizer, instantiate F, or access sealed media. Task 11 invokes that code after A-E finish to produce a signed `PostSelectionManifestExtension` with a frozen F config digest or one preregistered no-F terminal state, authenticated release event, and paired winner/F follow-up records.

- [ ] Write failing filesystem tests proving optimizer/controller cannot `open`, `stat`, inherit an FD, use a symlink, import a module, decrypt, or reach Scenario-Driver/Evaluator media before F digest freeze. Verify the repository and non-Evaluator packages contain only schema/catalog/commitment code and no follow-up plaintext. Add extension tests for a concrete F in the initial manifest, wrong initial manifest/result/optimizer/parent/migration/F digest, unsigned/stale/replayed extension, and any attempt to mutate A-E profiles/schedule/budget/policy.
- [ ] Freeze `optimizer-grid.json` exactly: memory slice `{1024,1536,2048}`, item cap `{4,6,8}`, weight triples `{(500,250,250),(400,300,300),(600,200,200),(450,200,350)}` basis points summing to 1,000, graph hops `{1,2}` only for E parents, and `retrieval_latency_ns_max=25_000_000`. Architecture-inapplicable fields are rejected. There is no post-decision cadence/batching knob. Implement the Task-11 runtime path so the optimizer runs as a dedicated AgentTeams Worker/project with no Workspace/evaluator/hidden access; each of 6 proposals consumes its ticket even when invalid/duplicate, and deterministic local replay scores valid proposals by quality, tokens, retrieval latency, then config digest.
- [ ] Implement and fixture-test the post-initial-run transition that freezes optimizer input digest, 6 task/result/usage receipts, candidate replay roots, chosen parent/base profile/parameters/migration, and F digest. It creates a Control-signed `post-selection-manifest-extension/v1` binding the immutable initial RunManifest digest, initial result root, optimizer grid/input/task/result/usage roots, selected parent, exact F profile/migration input/output digests, F digest, issuer/key/sequence/expiry, and signature. It is append-only, cannot change A-E profiles/schedule/budgets/policies, and is rejected on replay/substitution. Task 7 tests this with literal signed fixtures; only Task 11 may invoke it on real results.
- [ ] Implement and fixture-test the independent Scenario Driver transition, but never start it during Task 7. At Task-11 runtime it starts only after the signed F extension, selects/generates schema-valid mutations from the committed catalog, encrypts to the Evaluator release key, signs a commitment, stores ciphertext outside git, and records plaintext-capable actors. If actual researcher knowledge is unavoidable, label the lane `RESEARCHER_KNOWN_HOLDOUT`, not blind/sealed.
- [ ] Implement Evaluator-signed one-way `SEALED_REQUIREMENT_RELEASE`: after verifying the signed F extension it binds F/problem/generation/prior checkpoint/ciphertext+plaintext digest/key/sequence, sends only released bytes to Control, passes shared admission, and receives an authenticated receipt. Controller/optimizer have no pre-extension read/decrypt/release path.
- [ ] Implement the runtime fork so each problem's winner and F start from the same problem-specific turn-4 source tree, Control DB state root, memory event root/watermark, and closure in separate new AgentTeams/Workspace/Control overlays. F sees no future P2/P3 state when running its problem-specific follow-up and cannot call models to recompute P1-P3. Any allowed deterministic migration binds input/output roots. Candidate code again runs only in a disposable Runner.
- [ ] Implement pair ordering from `randomization_seed`. At Task-11 runtime, six main maxima/configuration/problem are Team Leader 1, Architect 1, Runtime 1, AgentTeams Evaluator 1, terminal Reviewer 1, plus one Runtime continuation; worst-case maintenance row, one aggregate review/configuration, and one terminal closure apply. Results stay post-selection and never alter initial ranking.
- [ ] Implement exact no-F terminals: `NOT_CREATED_NO_ELIGIBLE_PARENT`, `NOT_CREATED_NO_VALID_OPTIMIZER_CONFIG`, `OPTIMIZER_BUDGET_LIMITED`, and `OPTIMIZER_CAPABILITY_FAILED`. Fixture tests prove they spend no sealed ticket and release no ciphertext.
- [ ] Implement the preregistered descriptive replacement rule for Task 11 after all three pairs: F safe/uncensored, no pair more than 3.0 quality points below winner, then lower architecture tokens or same 5% token band with lower cumulative turn-boundary latency. Otherwise retain the original winner; make no significance claim at `n=3`.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_optimizer_and_sealed.py tests/secure_memory/test_campaign_resume.py
uv run --python 3.9 --extra dev ruff check benchmarks/secure_memory/optimizer.py benchmarks/secure_memory/scenario_driver.py benchmarks/secure_memory/campaign.py tests/secure_memory/test_optimizer_and_sealed.py
uv run --python 3.9 --extra dev mypy benchmarks/secure_memory/optimizer.py benchmarks/secure_memory/scenario_driver.py benchmarks/secure_memory/campaign.py
```

- [ ] Commit.

```bash
git add benchmarks/secure_memory/optimizer.py benchmarks/secure_memory/scenario_driver.py benchmarks/secure_memory/campaign.py benchmarks/secure_memory/corpus/v2/public/optimizer-grid.json benchmarks/secure_memory/schemas/optimizer-grid-v1.schema.json benchmarks/secure_memory/schemas/sealed-followup-commitment-v1.schema.json benchmarks/secure_memory/schemas/post-selection-manifest-extension-v1.schema.json docs/contracts/secure-agent/v2/contract-digests.json tests/secure_memory/test_optimizer_and_sealed.py tests/secure_memory/test_campaign_resume.py
git commit -m "feat(benchmark): seal AgentTeams post-selection test"
```

## Task 8: Build the content-addressed bundle, deterministic charts, and report

**Files:**

- Create: `benchmarks/secure_memory/bundle.py`
- Create: `benchmarks/secure_memory/report.py`
- Create: `benchmarks/secure_memory/charts.py`
- Create: `benchmarks/secure_memory/schemas/bundle-manifest-v1.schema.json`
- Create: `tests/secure_memory/test_bundle_and_report.py`
- Modify: `benchmarks/secure_memory/cli.py`
- Modify: `README.md`

**Interfaces:**

- Consumes: complete journals/receipts/metrics/scores/patches/PG replay roots.
- Produces: owner-only content-addressed acceptance directory and one-command verifier.

- [ ] Write failing bundle tests for missing/extra file, digest mismatch, schema drift, secret/PII/hidden-source leak, noncanonical JSON, broken event/Matrix/memory/evaluator/PG/projection chain, arm label in blind input, unsigned checkpoint/evaluator/unseal, grandchild leakage, unexplained user term, suppressed failure/decision, stale risk override, and report value differing from raw metrics.
- [ ] Include manifest/contracts/images/resources/DAG/prompt/context/scenario/rubric/relevance digests; sanitized provider capability/request/usage; AgentTeams Teams/Projects/tasks/roles/spawns/handoffs; admitted Matrix receipt records; final MCP args and system/Guardian/safety/approval decisions; every trace-bound user projection and projection-policy/glossary digest; Workspace/checkpoint/Runner/Evaluator evidence; memory proposals/transitions/retrievals/citations; RXP roots; admitted tests/metrics; Gate/Decision/closure; recovery; blind reviews; patches/trees; scores/Pareto; optimizer/F; and isolated fresh-PG replay roots. Run the shared admission gate before every bundle write; a secret rejection stores no raw digest.
- [ ] Generate deterministic SVG controller-side only from normalized admitted metrics. Required plots/tables: quality-token-latency Pareto, per-turn release-to-boundary ECDF, three episode release-to-Decision times, architecture/evaluation/campaign token breakdown, role/handoff/request counts, memory precision/recall/density/stale/conflict/provenance/`CITATION_BOUND`, user-visible/full-trace ratio with leakage/terminology/override counts, recovery outcomes, and initial versus post-selection comparison.
- [ ] Report separate measured facts, deterministic checks, AgentTeams/LLM opinion, and inference. Explain A baseline, B safety overhead, C/D/E memory tradeoffs, F post-selection bias, state-dependent n=12 limitation, provider retention uncertainty, SQLite boundary, database recovery evidence, static Pi/Codex inspiration without metrics, and real-GPU completion/outstanding state.
- [ ] Implement commands:

```text
secure-memory-bench build-bundle --campaign-root /absolute/campaign/root --output /absolute/bundle/root
secure-memory-bench verify-bundle --bundle /absolute/bundle/root --runner-image /absolute/campaign/images/candidate-runner.qcow2 --verification-root /absolute/verification/root
secure-memory-bench render-report --bundle /absolute/bundle/root --output /absolute/report/root
```

The verifier recomputes every digest/state root and reruns fresh-schema replay
only in a newly provisioned campaign-marked Candidate Runner/PG instance. It
refuses arbitrary DSNs, nonempty/wrong-name/wrong-owner clusters, superuser or
`BYPASSRLS`, dangerous extensions/`COPY PROGRAM`, and any image/manifest digest
mismatch. It never points destructive fixtures at a user database or trusts
report summaries.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_bundle_and_report.py
uv run --python 3.9 --extra dev ruff check benchmarks/secure_memory/bundle.py benchmarks/secure_memory/report.py benchmarks/secure_memory/charts.py tests/secure_memory/test_bundle_and_report.py
uv run --python 3.9 --extra dev mypy benchmarks/secure_memory/bundle.py benchmarks/secure_memory/report.py benchmarks/secure_memory/charts.py
```

- [ ] Commit.

```bash
git add benchmarks/secure_memory/bundle.py benchmarks/secure_memory/report.py benchmarks/secure_memory/charts.py benchmarks/secure_memory/schemas/bundle-manifest-v1.schema.json benchmarks/secure_memory/cli.py README.md docs/contracts/secure-agent/v2/contract-digests.json tests/secure_memory/test_bundle_and_report.py
git commit -m "feat(benchmark): package replayable AgentTeams evidence"
```

## Task 9: Produce a complete offline five-configuration mock campaign

**Files:**

- Create: `tests/secure_memory/test_offline_campaign.py`
- Modify: `benchmarks/secure_memory/substrate/preflight.py`
- Modify: `Makefile`

**Interfaces:**

- Consumes: all Tasks 1-8 with official-shape fake AgentTeams/Matrix/provider/Workspace/Candidate-Runner/Evaluator transports.
- Produces: deterministic `MOCK/SYNTHETIC/NO_PROVIDER_CALLS` acceptance fixture and offline Make gate.

- [ ] Run three four-turn problems across mock A-E in the frozen schedule. Produce distinct Teams/Projects/rooms/workspaces/DBs, role leases/tickets, Matrix receipts, attention packets, one-level user projections, counterfactual/enforcing system-plus-Guardian decisions, exact risk overrides/tool approvals, checkpoints, Gates/Decisions/closures, C/D/E maintenance, recovery, blind reviews, scores/Pareto, mock winner, optimizer/F sealed flow, bundle, and fresh SQLite semantic replay.
- [ ] Inject one transient provider failure, Workspace crash, Matrix send compensation, invalid memory candidate, cross-arm frame, stale approval, and forged evaluator result; verify bounded recovery/fail-closed evidence without reallocating another arm's ticket.
- [ ] Assert no network, QEMU, `key.txt`, Pi/Codex process/import, hidden file in mock AgentTeams image, or production secret reader is touched.
- [ ] Add `.PHONY: test-secure-memory-offline`; run exact secure-memory/AgentTeams/MCP tests, schema checks, Ruff/mypy, and legacy `rxp-bench` compatibility.
- [ ] Run the offline gate twice and compare bundle root digests.

```bash
make test-secure-memory-offline
make test-secure-memory-offline
```

Expected: both pass with identical deterministic mock bundle roots and explicit no-provider labels.

- [ ] Commit.

```bash
git add Makefile benchmarks/secure_memory/substrate/preflight.py tests/secure_memory/test_offline_campaign.py
git commit -m "test(benchmark): freeze offline AgentTeams campaign"
```

## Task 10: Build role-minimized images and pass real VM preflight with mock provider

**Files:**

- Create: `deploy/secure_memory/images/agentteams-files.txt`
- Create: `deploy/secure_memory/images/workspace-files.txt`
- Create: `deploy/secure_memory/images/control-files.txt`
- Create: `deploy/secure_memory/images/candidate-runner-files.txt`
- Create: `deploy/secure_memory/images/evaluator-files.txt`
- Create: `tests/secure_memory/test_image_manifests.py`
- Modify: `benchmarks/secure_memory/substrate/preflight.py`
- Modify: `benchmarks/secure_memory/cli.py`
- Modify: `deploy/secure_memory/README.md`
- Modify: `.gitignore`

**Interfaces:**

- Consumes: sealed images/resources and negative suites.
- Produces: signed VM preflight receipt required before provider qualification.

- [ ] Test exact allowlists: AgentTeams image has pinned official runtime/Matrix/isolated loopback adapters/resources but no source seed, provider key, PG secret, evaluator media; Workspace has source seed/tool executor/frozen dependencies but no AgentTeams/provider/PG/evaluator; Control has bridge/policy/memory/PG clients but no model shell/hidden suites; Candidate Runner has reconstructed candidate/public adapter plus campaign-marked throwaway PG but no hidden/key/result channel; Evaluator alone has hidden suites/corpus/signing secret mount and no candidate source/interpreter/import path, AgentTeams, provider, or candidate PG credential.
- [ ] Build sealed bases with the substrate `image_builder` fixed command, record package/SBOM/image/resource digests, then create distinct A-E overlays/tokens/channels/DBs/Teams/rooms. Do not use `host.docker.internal`, host source mounts, shared Docker socket, general guest NIC, or inherited host environment.
- [ ] Launch configurations sequentially under Seatbelt/HVF with mock provider. Inspect process environments, FDs, routes/sockets/DNS, service/Worker UIDs, PID/user/mount namespaces, disk paths, channel identities, socket ACLs, and resource limits. Prove native AgentTeams shell/file activity cannot change evaluated source or inspect adapter keys; allowlisted tests cannot change persistent Workspace state or inspect executor; candidate code cannot inspect hidden/signer state; only Workspace `apply_patch` effects appear in accepted patches.
- [ ] Run every frozen negative suite with at least 30 repetitions for deterministic isolation/channel microbenchmarks; publish P95 only for those local independent repetitions.
- [ ] Freeze a signed VM preflight record bound to manifest/images. A skip, missing capability, unsigned record, or stale digest blocks qualification.
- [ ] Implement/test `freeze-config` as the only real RunManifest freeze and the no-secret precursor to substrate `init`. It requires and hashes source/contracts; exact initial A-E profile registry; provisioner seed and derived schedule; ticket-template set; scenario/rubric/relevance and approval-fixture sets; optimizer grid and F-derivation schema (not a concrete F); provider qualification matrix; AgentTeams resources/DAG/prompts/context; effect-policy bundle and its constituent tool/system/Guardian/projection/glossary rules; trust roots; and all five prebuilt image manifests. It rejects a missing/stale digest, a concrete F, any template containing the manifest digest, or a recomputed schedule mismatch, then calls the substrate freezer once into an owner-only config outside the campaign root. `init` creates the root and materializes that immutable manifest plus already-bound template/schedule files; it does not regenerate inputs. Ignore `.secure-memory-images/` and `.secure-memory-configs/` without ignoring source fixtures.
- [ ] Run the preflight.

```bash
uv run --python 3.9 --extra dev python -m benchmarks.secure_memory.substrate.image_builder build --manifest-root deploy/secure_memory/guest --output-root /Users/aoisora/Desktop/个人文件/比赛/GOAI/.secure-memory-images/current
uv run --python 3.9 --extra dev secure-memory-bench freeze-config --source-root /Users/aoisora/Desktop/个人文件/比赛/GOAI/ego_agent_infra --images /Users/aoisora/Desktop/个人文件/比赛/GOAI/.secure-memory-images/current --output /Users/aoisora/Desktop/个人文件/比赛/GOAI/.secure-memory-configs/current.json
uv run --python 3.9 --extra dev secure-memory-bench init --config /Users/aoisora/Desktop/个人文件/比赛/GOAI/.secure-memory-configs/current.json --campaign-root /Users/aoisora/Desktop/个人文件/比赛/GOAI/.secure-memory-campaigns/current
uv run --python 3.9 --extra dev secure-memory-bench preflight-vm --manifest /Users/aoisora/Desktop/个人文件/比赛/GOAI/.secure-memory-campaigns/current/run-manifest.json --images /Users/aoisora/Desktop/个人文件/比赛/GOAI/.secure-memory-images/current --mock-provider --output /Users/aoisora/Desktop/个人文件/比赛/GOAI/.secure-memory-campaigns/current/vm-preflight
```

- [ ] Commit image manifests/tests/docs only; do not commit qcow2 disks or generated secrets.

```bash
git add .gitignore deploy/secure_memory/images/agentteams-files.txt deploy/secure_memory/images/workspace-files.txt deploy/secure_memory/images/control-files.txt deploy/secure_memory/images/candidate-runner-files.txt deploy/secure_memory/images/evaluator-files.txt deploy/secure_memory/README.md benchmarks/secure_memory/substrate/preflight.py benchmarks/secure_memory/cli.py tests/secure_memory/test_image_manifests.py
git commit -m "test(benchmark): prove isolated AgentTeams workspaces"
```

## Task 11: Qualify Agnes through AgentTeams and execute the authorized campaign

**Files:**

- Modify: `benchmarks/secure_memory/cli.py`
- Modify: `benchmarks/secure_memory/campaign.py`
- Modify: `benchmarks/secure_memory/report.py`
- Modify: `tests/secure_memory/test_campaign_resume.py`
- Create: `tests/secure_memory/test_provider_contract.py`

**Interfaces:**

- Consumes: passed offline/PG/VM preflight, broker-launcher key capability, 16 qualification templates, sealed A-E disks.
- Produces: real measured initial/follow-up bundle or explicit evidence-backed terminal/censor states.

- [ ] Write `test_provider_contract.py` first and watch it fail. Assert the manifest/broker accept only `https://apihub.agnes-ai.com/v1`, model `agnes-2.5-pro`, verified TLS, no cross-host redirect, main `10_000/1_500`, auxiliary `6_000/750`, review `8_000/1_000`, and memory slice `2_048`. Temperature is exactly `0` and top-p exactly `1` when the capability probe supports them; otherwise one signed capability record freezes their omission for every configuration. Any profile-specific drift, unleased operation, serializer change, or omission without that record blocks qualification.
- [ ] Use the substrate `secure-memory-broker-launcher` inside its Seatbelt profile for the entire qualification/campaign broker lifetime. It opens `/Users/aoisora/Desktop/个人文件/比赛/GOAI/key.txt` exactly once with `O_NOFOLLOW|O_CLOEXEC`, `fstat`s regular file/owner/mode/inode, optionally `fchmod`s that same FD to `0600` under the already-authorized fix-mode flag, closes every unrelated FD, and passes only that FD to broker. Broker rechecks the same FD; no later path resolution or separate `stat/chmod/open` sequence exists, and no content is printed. The provider-contract test injects an opener/FD spy and proves one open, same-FD verification/fix/read, no path reopen, no unrelated inherited FD, and no key bytes in repr/errors/receipts.
- [ ] Use exactly the following at-most-16-call qualification matrix in a dedicated official AgentTeams calibration project: basic nonstream body; stream/first-content; tool call ID; tool-result continuation; hard-output boundary; context-overlimit refusal; authoritative total usage; cached-input subset; reasoning-output subset; 429 owned retry; 5xx owned retry; timeout handling; same-host/no cross-host redirect; TLS verification; multi-role lease attribution; and idle-window proof of zero background calls. Each test has one issued qualification ticket; a case may combine assertions but none may be skipped.
- [ ] Freeze capability PASS before changing no serializer/model/limit/resource. Missing hard limit/usage, unleased call, background heartbeat call, request-shape drift, or inability to route only through the broker records `CAPABILITY_UNAVAILABLE` and stops all paid engineering work.
- [ ] Restore new independent A-E overlays and execute the initial 12-block campaign. The broker halts before any reservation/request/absolute breach. Contradictory/missing usage freezes all later paid calls while retaining evidence. Each arm failure remains local.
- [ ] After A-E terminal roots are frozen, apply preregistered score/rank and only then invoke Task 7's post-initial-run state machine. If eligible, run the AgentTeams optimizer, write/verify the signed post-selection extension, start the confined Scenario Driver, release each follow-up, and run original-winner/F sealed pairs from identical per-problem roots. No concrete F, extension, Scenario Driver, or sealed-media access exists before this point. If ineligible, keep sealed data unread and record the exact no-F terminal reason.
- [ ] Build and verify the package against a fresh PostgreSQL database. Review factual boundaries: every incomplete/budget-limited/infrastructure/capability/disqualified state remains named and unranked; no winner is forced.
- [ ] Implement read-only machine gates: `verify-capability --manifest ... --record ...` verifies the signed 16-case record and exact provider contract; `status --campaign-root ... --require-initial-terminal A,B,C,D,E` verifies five independent terminal roots; and `verify-selection --campaign-root ...` replays score/Pareto/optimizer/F-release rules without unsealing unavailable data. Each exits nonzero on a missing, stale, skipped, contradictory, or unsafe record.
- [ ] Preserve owner-only campaign root until explicit user removal. Do not merge/push experimental patches.

- [ ] Run the provider contract and resume tests before any live command:

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_provider_contract.py tests/secure_memory/test_campaign_resume.py
```

Commands:

```bash
secure_campaign_root='/Users/aoisora/Desktop/个人文件/比赛/GOAI/.secure-memory-campaigns/current'
secure_image_root='/Users/aoisora/Desktop/个人文件/比赛/GOAI/.secure-memory-images/current'
secure_manifest_path="$secure_campaign_root/run-manifest.json"
secure_bundle_root="$secure_campaign_root/bundle"
uv run --python 3.9 --extra dev secure-memory-bench capability-probe --manifest "$secure_manifest_path" --broker-key-file '/Users/aoisora/Desktop/个人文件/比赛/GOAI/key.txt' --fix-key-mode --output "$secure_campaign_root/qualification"
uv run --python 3.9 --extra dev secure-memory-bench verify-capability --manifest "$secure_manifest_path" --record "$secure_campaign_root/qualification/capability.json"
uv run --python 3.9 --extra dev secure-memory-bench run --manifest "$secure_manifest_path" --matrix A,B,C,D,E --output "$secure_campaign_root"
uv run --python 3.9 --extra dev secure-memory-bench status --campaign-root "$secure_campaign_root" --require-initial-terminal A,B,C,D,E
uv run --python 3.9 --extra dev secure-memory-bench verify-selection --campaign-root "$secure_campaign_root"
uv run --python 3.9 --extra dev secure-memory-bench build-bundle --campaign-root "$secure_campaign_root" --output "$secure_bundle_root"
uv run --python 3.9 --extra dev secure-memory-bench verify-bundle --bundle "$secure_bundle_root" --runner-image "$secure_image_root/candidate-runner.qcow2" --verification-root "$secure_campaign_root/verification"
uv run --python 3.9 --extra dev secure-memory-bench render-report --bundle "$secure_bundle_root" --output "$secure_campaign_root/report"
```

Expected: each command either exits 0 with signed evidence or exits nonzero with a named terminal record; no silent fallback.

- [ ] Commit only implementation/test/report templates. Real campaign artifacts stay outside git.

```bash
git add benchmarks/secure_memory/cli.py benchmarks/secure_memory/campaign.py benchmarks/secure_memory/report.py tests/secure_memory/test_campaign_resume.py tests/secure_memory/test_provider_contract.py
git commit -m "feat(benchmark): execute qualified AgentTeams campaign"
```

## Task 12: Add the separately gated real-GPU AgentTeams judge demonstration

**Files:**

- Create: `benchmarks/secure_memory/gpu_demo.py`
- Create: `tests/secure_memory/test_gpu_demo.py`
- Create: `mcp_servers/src/egoagentos_mcp/live_gpu_backend.py`
- Create: `mcp_servers/tests/test_live_gpu_backend.py`
- Create: `deploy/secure_memory/gpu-job-manifest.schema.json`
- Create: `benchmarks/secure_memory/schemas/gpu-lane-binding-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/gpu-lane-authorization-v1.schema.json`
- Modify: `mcp_servers/src/egoagentos_mcp/gpu_server.py`
- Modify: `mcp_servers/pyproject.toml`
- Modify: `mcp_servers/README.md`
- Modify: `benchmarks/secure_memory/cli.py`
- Modify: `benchmarks/secure_memory/report.py`

**Interfaces:**

- Consumes: selected safe AgentTeams profile, opt-in typed `ego-gpu` live backend, explicit backend/credential/max-cost authorization manifest, and the safety plan's GPU-owner signed lease plus enforcing risk/approval projection.
- Produces: pre-lease `SignedGpuLaneAuthorization`, post-approval `SignedGpuLaneBinding`, one bounded plan -> human approval -> execute -> deterministic metrics -> independent review -> Decision acceptance package, or exact unavailable state.

Use two acyclic Control-owned contracts. The authorization freezes identity and
ceilings before the GPU lease exists. The lease binds the authorization digest.
Only after the final job request and exact approval does the execution binding
bind authorization, lease, risk chain, message, and receipt. This lane is not
an A-E/F measured configuration and cannot borrow a frozen campaign ticket or
cap:

```python
class GpuLaneAuthorizationCore(StrictModel):
    schema_version: Literal["gpu-lane-authorization/v1"]
    lane_id: str
    campaign_manifest_sha256: Digest
    post_selection_extension_sha256: Digest | None
    selected_configuration_id: MeasuredConfigurationId
    selected_checkpoint_sha256: Digest
    selected_memory_root_sha256: Digest
    backend_id: str
    backend_capability_receipt_sha256: Digest
    credential_capability_sha256: Digest
    job_manifest_sha256: Digest
    image_sha256: Digest
    dataset_sha256: Digest
    checkpoint_sha256: Digest
    gpu_type: str
    gpu_count: int = Field(ge=1)
    max_wall_seconds: int = Field(ge=1)
    max_cost_microunits: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    kill_switch_contract_sha256: Digest
    system_risk_rules_sha256: Digest
    guardian_rules_sha256: Digest
    projection_policy_sha256: Digest
    separate_model_request_cap: int = Field(ge=0)
    separate_model_input_cap: int = Field(ge=0)
    separate_model_output_cap: int = Field(ge=0)
    separate_ticket_template_set_sha256: Digest
    issuer_id: str
    key_id: str
    issue_sequence: int = Field(ge=0)
    expires_at_sequence: int = Field(ge=0)


class SignedGpuLaneAuthorization(StrictModel):
    core: GpuLaneAuthorizationCore
    core_sha256: Digest
    signature_base64: str


class GpuLaneBindingCore(StrictModel):
    schema_version: Literal["gpu-lane-binding/v1"]
    lane_authorization_sha256: Digest
    gpu_owner_task_lease_sha256: Digest
    final_job_request_sha256: Digest
    system_assessment_sha256: Digest
    guardian_decision_sha256: Digest
    safety_decision_sha256: Digest
    approval_projection_sha256: Digest
    approval_message_sha256: Digest
    approval_receipt_sha256: Digest
    approver_id: str
    approval_action: Literal["APPROVE"]
    approved_at_unix_ns: int = Field(ge=0)
    approval_action_sha256: Digest
    issuer_id: str
    key_id: str
    issue_sequence: int = Field(ge=0)
    expires_at_sequence: int = Field(ge=0)


class SignedGpuLaneBinding(StrictModel):
    core: GpuLaneBindingCore
    core_sha256: Digest
    signature_base64: str
```

- [ ] Write tests that require every authorization and execution-binding field, exact canonical core digests, Control signatures/sequences/expiries, selected eligible C/D/E/F configuration and matching checkpoint/memory/extension isolation, backend/credential capabilities, GPU type/count/time/currency ceiling, dataset/checkpoint/image digest, kill switch, separate non-borrowable model caps and ticket-template-set digest, GPU-owner lease, final request, R2 approver identity/time/action/action digest, approval message/receipt, and enforcing system/Guardian/Safety/projection digests. Prove the authorization contains no future lease/approval digest, the lease requirement digest equals the authorization core digest, every GPU issued ticket comes only from the authorization-bound separate set, and the final binding closes the chain without a hash cycle. Selected F requires the matching non-null post-selection extension; C/D/E forbid one. Missing/forged/stale/cross-campaign fields, A/B or ineligible selection, zero authority, a request above either GPU or separate model cap, or an attempt to consume any of the 356 A-E/F templates yields `CAPABILITY_UNAVAILABLE_NO_AUTHORIZED_BACKEND` without launching or spending.
- [ ] Preserve the existing synthetic GPU behavior as legacy/test and add no fake live claim. Implement `LiveGPUBackend` as a disabled-by-default typed adapter selected only by an authorization manifest: exact backend ID/endpoint, owner-only credential FD, pinned job image/dataset/checkpoint, GPU/time/cost ceilings, status/cancel APIs, no arbitrary command, and admission-scanned outputs. Add a separate broker ledger namespace loaded only from the signed GPU authorization's ticket-template set and caps; it cannot read, issue, or consume the 356 ranked-campaign templates. Its final job request is bound to the post-approval `SignedGpuLaneBinding` and immutable backend receipt. If the backend contract cannot prove cost/kill/status/metrics, return `CAPABILITY_UNAVAILABLE_NO_AUTHORIZED_BACKEND`.
- [ ] Use the same AgentTeams TeamHarness/Matrix role graph and safety/memory winner; no alternate agent runner. Runtime Worker may call only typed `ego-gpu` MCP after the final immutable job request receives system `HIGH`, internal Guardian `HIGH`, the full risk-override projection, and exact Control-bound human approval. A changed backend/job/image/dataset/checkpoint/GPU/count/time/cost/kill-switch/target/policy/expiry invalidates the message and approval. Freeze admitted GPU metrics/stdout/artifacts, failure/retry/recovery, AgentTeams roles/Matrix, user projections, independent deterministic Evaluator results, Reviewer opinion, Gate/Decision/Trace.
- [ ] Select a cost-bounded deterministic micro-experiment already supported by the frozen GPU MCP/image, with one baseline and one treatment, fixed seed/data split/max wall time, and an abort threshold. Do not download code/data during the run.
- [ ] Add CLI `gpu-demo --authorization /absolute/campaign/root/gpu-authorization.json`. When that file/capability is absent, emit the unavailable record and state in the judge report that the GPU recommendation remains incomplete. Never label a CPU/mock run as GPU.
- [ ] Run local no-backend tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_gpu_demo.py
uv run --python 3.12 --project mcp_servers --extra dev pytest -q tests/test_live_gpu_backend.py
```

- [ ] Commit.

```bash
git add benchmarks/secure_memory/gpu_demo.py benchmarks/secure_memory/cli.py benchmarks/secure_memory/report.py benchmarks/secure_memory/schemas/gpu-lane-authorization-v1.schema.json benchmarks/secure_memory/schemas/gpu-lane-binding-v1.schema.json tests/secure_memory/test_gpu_demo.py mcp_servers/src/egoagentos_mcp/live_gpu_backend.py mcp_servers/src/egoagentos_mcp/gpu_server.py mcp_servers/tests/test_live_gpu_backend.py mcp_servers/pyproject.toml mcp_servers/README.md deploy/secure_memory/gpu-job-manifest.schema.json docs/contracts/secure-agent/v2/contract-digests.json
git commit -m "feat(benchmark): gate real AgentTeams gpu evidence"
```

## Plan Exit Criteria

- A-E/F have no runtime except official AgentTeams/TeamHarness/Matrix and no Pi/Codex measured path.
- Five initial configurations share source/model/tools/context ceiling/evaluator/limits and differ only by frozen safety/memory profile.
- Every model request has an AgentTeams Project/task/Worker/role lease and non-transferable ticket.
- Every configuration has independent writable state and every accepted patch has Workspace authorization/checkpoint/Evaluator provenance.
- All three four-turn problems, role/handoff traces, memory behavior, recovery, raw metrics, Gates, Decisions, and fresh-PG replay are packaged.
- Every ordinary user update is scope/direct-child only with explained specialist terms; projection leakage/suppression checks are zero, and every enforcing double-HIGH effect is blocked on an exact risk-override approval.
- Initial ranking, B-E Pareto, optimizer, and sealed F follow the frozen formulas and access boundaries.
- Offline mock, real PostgreSQL, real VM mock-provider, and provider qualification gates pass before paid engineering calls.
- The final report recommends only an eligible measured C/D/E design, explains residual risks, contains no fabricated Pi/Codex comparison, and states the real-GPU demonstration's exact completed or outstanding status.
