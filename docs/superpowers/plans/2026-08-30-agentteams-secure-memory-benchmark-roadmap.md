# AgentTeams Secure Memory Benchmark Implementation Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a strongly isolated, evidence-backed comparison of five safety/memory configurations and one post-selection configuration, all running on the existing AgentTeams framework.

**Architecture:** Extend the existing AgentTeams bridge, Matrix/TeamHarness contract, and MCP services in four dependency-ordered streams: trusted experiment substrate, deterministic AgentTeams workspace safety/attention/user projection, PostgreSQL evidence memory, then the real benchmark campaign and acceptance package. Pi and Codex remain read-only design references and have no executable or measured path.

**Tech Stack:** Python 3.9 for root/bridge code, Python 3.12 for MCP, FastAPI, Pydantic v2, official AgentTeams `main@223ddc2`, TeamHarness, Matrix, MCP, PostgreSQL/PolarDB PG, SQLite semantic fallback, QEMU/HVF, macOS Seatbelt, Ed25519, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-30-agentteams-secure-memory-benchmark-design.md`

## Global Constraints

- Implement only in `ego_agent_infra`; do not edit, build, launch, or benchmark the `pi` or `codex` checkouts.
- Every model-driven call, including summary, extraction, graph work, blind review, and optimization, must be an AgentTeams Worker task with a TeamHarness/Matrix trace.
- Treat AgentTeams Controller, TeamHarness, Matrix, Workers, artifacts, reviewer PASS, and tool-result messages as untrusted collaboration provenance.
- Only the sealed Evaluator plus trusted Control finalizer may create Gate, Decision, DecisionClosure, or `LOCAL_TRUSTED` facts.
- Every A-E/F configuration receives an independent Team, Project/room set, tokens, channels, database, workspace overlay, and VM disks.
- No paid call occurs until every frozen negative suite and the AgentTeams/provider capability gate pass.
- Enforce absolute caps of 360 requests, 4,000,000 input tokens, and 600,000 output tokens; the non-transferable template reservation is 356 requests, 3,306,000 input, and 485,500 output.
- Use an isolated EgoAgentOS git worktree at implementation time. Existing user changes in the current worktree remain untouched.
- `key.txt` is opened once by the sandboxed broker launcher with `O_NOFOLLOW`, verified/fixed through that same FD, and read only by the broker; no other process, VM, prompt, trace, or artifact receives the key.
- Strong-mode evidence never promotes AgentTeams self-report to executor or evaluator truth.
- Ordinary status is a deterministic trace projection limited to the current scope and direct children. The internal two-stage system/Guardian rule pipeline overrides that limit for double-HIGH approval and security incidents; A records counterfactual decisions, while B-E/F and authorized GPU execution enforce them.

---

## Plan Set and Dependency Order

| Order | Plan | Primary boundary | Exit gate |
| ---: | --- | --- | --- |
| 1 | [`2026-08-30-secure-experiment-substrate-implementation-plan.md`](2026-08-30-secure-experiment-substrate-implementation-plan.md) | schemas, isolated VMs/channels, broker tickets, evaluator signatures, journal | deterministic/mock substrate and secret/isolation suites pass |
| 2 | [`2026-08-30-agentteams-safety-attention-implementation-plan.md`](2026-08-30-agentteams-safety-attention-implementation-plan.md) | existing AgentTeams bridge, TeamHarness/Matrix envelopes, typed workspace MCP, final-effect policy, attention packets | official-contract, scope, approval, effect provenance, and replay suites pass |
| 3 | [`2026-08-30-evidence-memory-implementation-plan.md`](2026-08-30-evidence-memory-implementation-plan.md) | candidate RPC, closure, DB roles/RLS, C/D/E plugins and AgentTeams memory roles | SQLite semantic and real PostgreSQL authority/concurrency/replay suites pass |
| 4 | [`2026-08-30-memory-benchmark-campaign-implementation-plan.md`](2026-08-30-memory-benchmark-campaign-implementation-plan.md) | scenarios, five-arm schedule, role tickets, scoring, F, bundle, real execution | mock campaign, VM preflight, provider qualification, paid run, and verification pass |

Plans 1 and 2 may be developed in parallel only after their shared schemas are frozen. Plan 3 consumes the candidate/evaluator/finalizer contracts from plan 1 and the attention/task envelope from plan 2. Plan 4 consumes all prior gates.

The substrate defines and tests the RunManifest schema/freezer early, but does
not freeze the real campaign. Campaign Tasks 1, 4, and 7 first produce the
initial A-E/scenario/schedule/template/grid prerequisite digests; Task 10 binds
those plus policies/resources/images in the one real RunManifest freeze. A
concrete F never enters that initial manifest and can appear only in the signed
post-selection extension after the initial result and optimizer are frozen.

## Cross-Plan Contract Ownership

| Contract | Owning plan/file family | Consumers |
| --- | --- | --- |
| RunManifest, channel, request/usage, fact/relation, evaluator, checkpoint | substrate; `benchmarks/secure_memory/models.py`, `manifest.py`, `schemas/`, and `docs/contracts/secure-agent/v2` | safety, memory, campaign |
| AgentTeams campaign envelope, role lease, system/Guardian decision, attention packet, user-status projection | safety/attention; `apps/agentteams_bridge` and `integrations/agentteams` | memory, campaign |
| memory proposal/revision/closure/retrieval/capsule | memory; `apps/api/trusted_memory` and PG migrations | safety attention injection, campaign metrics |
| scenario/rubric/relevance/ticket/schedule/bundle | campaign; `benchmarks/secure_memory` | evaluator and final report |

- [ ] Make every wire schema strict, versioned, canonical, and indexed by `contract-digests.json`.
- [ ] Generate Python/guest validators from the schema source and make `--check` fail on drift; do not hand-copy models into another repository.
- [ ] Bind the RunManifest to EgoAgentOS/AgentTeams commits, official contract lock, five role images, resources, role/DAG/tool policy, system-risk/Guardian/projection/glossary rules, prompts, context policy, tickets, scenario/rubric/relevance digests, trust roots, and design-reference digests.
- [ ] Package public AgentTeams/Worker code, untrusted Candidate Runner code, and Scenario Driver/Evaluator-only sealed corpus/suites as separate allowlisted distributions/images.

## Delivery Gates

### Gate 0: Historical and revision freeze

- [ ] Keep `2026-08-30-secure-pi-memory-benchmark-design.md` marked superseded and retain its replacement link.
- [ ] Record EgoAgentOS `59e4ee937343278ddf320c78384433b8e56f4d8b`, AgentTeams official `223ddc2b8073e4c8b93bcbb15e1d717f196c04d9`, Pi design reference `853a80d26c90a14c1886f0ebb8ffaae133ca2185`, and Codex design reference `6478a751fde8884b2fdc76486fe23175a8e795d4`.
- [ ] Prove no plan file contains a Pi/Codex adapter, runner, experiment arm, provider request, measured metric, or repository edit.
- [ ] Freeze A-E/F matrix semantics and the 356-request reservation table in tests.

Run:

```bash
if rg -n "adapters/(pi|codex)|pi_runner|Codex CLI|runtime=\"pi\"|runtime=\"codex\"|ArmId\.R" docs/superpowers/plans/*implementation-plan.md docs/superpowers/specs/2026-08-30-agentteams-secure-memory-benchmark-design.md; then exit 1; fi
```

Expected: no executable-plan match; the new spec may contain only explicit prohibition/design-reference prose.

### Gate 1: Deterministic local implementation, no secrets

- [ ] Complete substrate schema/channel/budget/scanner/evaluator/journal tests with injected fake transports and keys.
- [ ] Complete AgentTeams bridge/MCP safety, system/Guardian, attention, and user-projection tests using existing official-contract fixtures; fixture success remains labeled contract-only.
- [ ] Complete memory lifecycle, closure, origin, retrieval, graph, RLS-definition, and replay tests.
- [ ] Run offline contract lock verification; do not call GitHub, the provider, QEMU, Matrix, or a live AgentTeams deployment from the default test target.

Run:

```bash
make test-secure-memory-offline
make test-agentteams
make check-agentteams
```

Expected: all exit 0 and evidence says `MOCK/SYNTHETIC/NO_PROVIDER_CALLS` where appropriate.

### Gate 2: Real PostgreSQL and fresh-schema proof

- [ ] Apply migrations to a fresh PostgreSQL database with separate migration/runtime/curator/finalizer/validator/auditor identities.
- [ ] Capture role, owner, grant, default-privilege, trigger, RLS, login-to-tenant mapping, and function `search_path` dumps.
- [ ] Prove runtime/curator promotion, finalizer transition, validator input mutation, custom-GUC forgery, role switch, owner bypass, trigger disable, DDL, cross-tenant access, stale CAS, and duplicate/outbox/concurrent races fail correctly.
- [ ] Replay the complete event stream into a second fresh database and compare canonical state roots.

Run:

```bash
test -n "${EGO_TEST_POSTGRES_URL:-}"
make test-postgres
uv run --python 3.9 --extra dev pytest -q tests/postgres
```

Expected: all exit 0; SQLite results are labeled semantic fallback only.

### Gate 3: Isolated VM preflight with mock provider

- [ ] Build role-minimized AgentTeams, Workspace, Control, Candidate Runner, and Evaluator images from allowlisted manifests.
- [ ] Provision independent A-E roots, Teams, rooms, tokens, channels, databases, and overlays; no mutable sharing.
- [ ] Run `host_isolation`, `service_isolation`, `candidate_runner_isolation`, `artifact_ingest`, `evaluator_integrity`, `evaluator_channel`, `candidate_rpc`, `db_authority`, `memory_closure`, `memory_concurrency`, `rxp_linkage`, `context_safety`, `broker_budget`, `agentteams_scope`, and `workspace_effect_authorization`; the frozen `context_safety`/`agentteams_scope` assertion lists include Guardian nondowngrade, direct-child projection, terminology, incident, and approval-override cases.
- [ ] Prove AgentTeams native shell/file activity cannot alter the evaluated workspace and the only accepted patch comes from the typed MCP/Workspace overlay chain.
- [ ] Run the complete five-arm mock campaign, checkpoint recovery, sealed F flow, bundle build, and one-command verification without reading `key.txt`.

Run:

```bash
make test-secure-memory-offline
uv run --python 3.9 --extra dev secure-memory-bench preflight --offline --manifest /absolute/campaign/run-manifest.json --output /absolute/campaign/offline-preflight
uv run --python 3.9 --extra dev secure-memory-bench preflight-vm --manifest /absolute/campaign/run-manifest.json --images /absolute/images --mock-provider --output /absolute/campaign/vm-preflight
```

Expected: all exit 0, no outbound network, and every configuration has a distinct root digest.

### Gate 4: Official AgentTeams and provider qualification

- [ ] Start one disposable official AgentTeams stack at the pinned contract with role-specific dummy credentials and no general NIC.
- [ ] Prove Controller/TeamHarness/Matrix project flow, MCP routing, per-task role attribution, no background model calls, scoped spawn/tool policy, and strong-mode pause/recovery.
- [ ] Use at most 16 reserved main requests through the exact preregistered AgentTeams qualification matrix to verify request shape, streaming, first-content, tool IDs/results, output ceilings, authoritative usage, cache/reasoning semantics, context errors, retry behavior, TLS, redirects, role attribution, and zero background calls.
- [ ] Freeze the signed capability record. Any unbudgeted call, unattributed role, missing hard output limit, missing authoritative usage, or accepted-workspace bypass yields `CAPABILITY_UNAVAILABLE` and stops paid engineering execution.

Run:

```bash
uv run --python 3.9 --extra dev secure-memory-bench verify-capability --manifest /absolute/campaign/run-manifest.json --record /absolute/campaign/qualification/capability.json
```

Expected: exit 0 only for the signed complete 16-case record bound to the exact Agnes endpoint/model/request contract.

### Gate 5: Initial five-configuration campaign

- [ ] Restore clean independent A-E disks and apply the frozen Williams/reverse 12-block schedule.
- [ ] Preserve one four-turn AgentTeams project per problem and reset Project/Worker/Matrix history at each problem boundary while retaining only source checkpoint plus Control memory.
- [ ] Enforce 14 main templates per problem/configuration (10 mandatory role minima plus four Runtime-only continuation maxima), conditional auxiliary templates, one aggregate blind AgentTeams review per configuration, owned retries with original effective class, and atomic hard caps.
- [ ] Freeze one signed checkpoint after turns 1-3 and one sealed Evaluator Gate/Decision/closure after turn 4.
- [ ] Record AgentTeams roles/handoffs, Matrix receipts, final MCP arguments, system/Guardian/policy decisions, user projections and risk overrides, tool results, model usage, attention packets, memory transitions, raw evaluator metrics, recovery, and trace roots.

Run:

```bash
uv run --python 3.9 --extra dev secure-memory-bench status --campaign-root /absolute/campaign --require-initial-terminal A,B,C,D,E
```

Expected: exit 0 only when A-E have distinct terminal evidence roots and all ordinary projection leakage/suppression counters are zero.

### Gate 6: Ranking, optimization, and sealed comparison

- [ ] Apply the frozen rubric, relevance ledger, quality-band/token-band/latency winner rule, and B-E Pareto definition without changing any threshold after results are visible.
- [ ] If C/D/E has an eligible winner, give 6 AgentTeams optimizer tickets access only to frozen calibration summaries and the exact grid, select one allowed config by deterministic replay, and freeze F digest.
- [ ] Prove sealed follow-ups were inaccessible before F freeze, then fork original winner/F from identical per-problem turn-4 source and Control roots into independent overlays/databases.
- [ ] Run six main maxima, worst-case maintenance tickets, and one aggregate blind review per winner/F configuration; keep these results post-selection.
- [ ] If no eligible parent/config, optimizer budget, or capability exists, record the exact preregistered no-F terminal state and do not release follow-ups.

Run:

```bash
uv run --python 3.9 --extra dev secure-memory-bench verify-selection --campaign-root /absolute/campaign
```

Expected: exit 0 only when initial scoring/Pareto and either the authenticated winner/F release or one exact no-F terminal state replay deterministically.

### Gate 7: Acceptance package and judge-facing claims

- [ ] Build the content-addressed package with manifests, Matrix, roles, raw usage/metrics, system/Guardian decisions, user projections, approvals, effect receipts, checkpoints, memory records, Gate/Decision/closure, recovery, patches, scores, charts, and fresh-PG replay.
- [ ] Verify every file/digest/schema/chain and reproduce decisions from a fresh database with one command.
- [ ] Report static Pi/Codex design inspiration without any fabricated comparison metric.
- [ ] Report the real-GPU AgentTeams recommendation as complete only after a separately authorized bounded GPU run records plan, human approval, execution, deterministic metrics, independent review, Decision, Matrix, recovery, and Trace. Otherwise state the exact outstanding capability/cost authority.

Run:

```bash
uv run --python 3.9 --extra dev secure-memory-bench build-bundle --campaign-root /absolute/campaign --output /absolute/bundle
uv run --python 3.9 --extra dev secure-memory-bench verify-bundle --bundle /absolute/bundle --runner-image /absolute/images/candidate-runner.qcow2 --verification-root /absolute/verification
uv run --python 3.9 --extra dev secure-memory-bench render-report --bundle /absolute/bundle --output /absolute/report
```

Expected: every command exits 0; bundle verification rejects any projection leakage, unexplained term, hidden required decision, or stale approval binding.

## Commit and Review Discipline

- [ ] Use exact file paths in every `git add`; never stage a directory wholesale or use `git add .`/`git add -A`.
- [ ] Commit one independently passing task at a time in the EgoAgentOS worktree.
- [ ] Run a spec-coverage, placeholder, type/signature, migration, package-data, and command/path review before each plan exit.
- [ ] Use `superpowers:requesting-code-review` after each plan stream and `superpowers:verification-before-completion` before any completion claim.

## Definition of Done

The roadmap is complete only when the replacement spec's twelve completion conditions are met, all measured agents are AgentTeams Workers, A-E and any created F have independent evidence-backed terminal states, fresh PostgreSQL replay is byte-verifiable, no Pi/Codex executable path exists, ordinary user messages expose only one hierarchy level with explained terms, every enforcing double-HIGH effect requires exact approval, and the final report cleanly separates collaboration provenance, deterministic trust, model opinion, measured facts, and remaining GPU or production limitations.
