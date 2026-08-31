# AgentTeams Optimal D Architecture Delivery Plan

> **Execution:** use `superpowers:subagent-driven-development` task by task, with a fresh independent review after each gate.

**Goal:** Ship one competition-ready AgentTeams-only architecture before running comparative experiments: deterministic safety approval, an evidence-grounded two-layer memory, token-bounded focused attention, concise trace-derived user status, and restart-safe replay on SQLite/PostgreSQL.

**Selected profile:** `D_EVIDENCE_LAYERED_V1`. D is selected as the preregistered delivery default, not as an experimentally proven winner. It retains the strongest evidence/closure boundary while avoiding E's graph Navigator/Curator/Critic role and token overhead. A/B/C/E/F comparisons, optimizer calls, paid provider qualification, GPU work, and ranking claims remain explicitly deferred.

**Runtime boundary:** official AgentTeams Controller/TeamHarness/Matrix/Workers remain the sole collaboration runtime. `EgoGuardian` is a deterministic Control component, not another agent runtime. Pi/Codex code and runtimes are not imported or executed.

## Required behavior

- System risk classification runs first. A system `HIGH` assessment is reviewed independently by `EgoGuardian`; only double-`HIGH` creates an exact-argument approval. Mandatory escape, exfiltration, destructive, and evidence-tamper rules cannot be downgraded.
- Memory Layer 1 is the immutable evidence/validated-knowledge ledger. Only facts closed by a signature-valid evaluator result and terminal Decision may enter it.
- Memory Layer 2 is a deterministic per-turn working set compiled from the current requirement/checkpoint plus eligible Layer-1 facts. It is disposable and never becomes authority merely because a model used it.
- Retrieval is tenant/project/component/version/outcome/origin/lifecycle scoped, rejects conflicted/superseded/revoked facts, uses stable tie-breaking, and fits a fixed token budget while preserving active requirements, failures, and policy.
- Ordinary user status exposes the current work scope and direct children only. Approval/security events override the depth limit. Every specialist term is explained on first use from a deterministic zh-CN/en-US glossary.
- AgentTeams reviews, Matrix text, Worker output, tool results, and artifacts stay `ORIGIN_UNVERIFIED`. Only typed Workspace effects and signed evaluator facts can change trusted state.
- SQLite remains a development-compatible backend. PostgreSQL is the strong backend with append-only history, tenant isolation, post-commit notification, and fresh-schema replay evidence.

## Deferred with comparison experiments

- A/B/C/E/F profile execution, winner/Pareto computation, frozen optimizer, sealed follow-ups, blind review campaign, and paid Agnes calls.
- Navigator/Curator/Critic graph roles, real GPU work, full QEMU five-VM launch, PITR execution, and production availability claims.
- These deferrals must appear as `NOT_EXECUTED`/`DEFERRED`, never as a passing claim.

## Task 0: Close the provider-budget substrate gate

- Require replay to bind an externally trusted `budget_state_sha256`; a self-rehashed event history is not its own trust anchor.
- Bind the capability record to the exact frozen Agnes base URL.
- Re-run all prior Task-3 adversarial probes and obtain an independent `PASS` report.

## Task 1: Freeze the D contracts and schemas

**Create:**

- `apps/agentteams_bridge/extensions/__init__.py`
- `apps/agentteams_bridge/extensions/contracts.py`
- `apps/api/trusted_memory/__init__.py`
- `apps/api/trusted_memory/models.py`
- `integrations/agentteams/campaign-envelope.schema.json`
- `integrations/agentteams/safety-decision.schema.json`
- `integrations/agentteams/attention-packet.schema.json`
- `integrations/agentteams/guardian-decision.schema.json`
- `integrations/agentteams/user-status-projection.schema.json`
- `tests/agentteams/test_bridge_extension_contracts.py`
- `tests/memory/test_trusted_memory_models.py`

Freeze `CampaignBinding`, canonical effect, `RiskAssessment`, `GuardianDecision`, `SafetyDecision`, fact lifecycle/conflict/supersession records, `AttentionPacket`, `WorkHierarchy`, `UserStatusProjection`, and their domain-separated digests. Update the canonical schema digest index. Unknown fields, unordered identifiers, stale versions, cross-tenant bindings, and approval fields inside memory context fail closed.

## Task 2: Add durable append-only authority and replay

**Create/modify:**

- `apps/agentteams_bridge/migrations/postgres/002_campaign_safety_attention.sql`
- `apps/api/migrations/postgres/003_trusted_memory_core.sql`
- `apps/agentteams_bridge/store.py`
- `apps/agentteams_bridge/postgres_store.py`
- `apps/api/store_contract.py`
- `apps/api/store.py`
- `apps/api/postgres_store.py`
- `tests/agentteams/test_bridge_extension_replay.py`
- `tests/memory/test_trusted_memory_sqlite.py`
- `tests/postgres/test_trusted_memory_authority.py`

Store append-only extension events, memory events/current projections, closure roots, conflicts, supersession/revocation, and outbox rows. PostgreSQL enforces tenant filters, least-privilege roles, immutable history, CAS updates, and post-commit notification. Fresh SQLite/PostgreSQL replay must reproduce identical roots. Legacy `validated=True` rows are compatibility-only `ORIGIN_UNVERIFIED` inputs.

## Task 3: Enforce exact effects and approval

**Create/modify:**

- `apps/agentteams_bridge/extensions/safety.py`
- `apps/agentteams_bridge/extensions/guardian.py`
- `mcp_servers/src/egoagentos_mcp/workspace_contract.py`
- `mcp_servers/src/egoagentos_mcp/workspace_server.py`
- `mcp_servers/src/egoagentos_mcp/workspace_executor.py`
- `apps/agentteams_bridge/service.py`
- `tests/agentteams/test_bridge_safety.py`
- `tests/agentteams/test_bridge_guardian.py`
- `mcp_servers/tests/test_workspace_server.py`
- `mcp_servers/tests/test_workspace_executor.py`

Classify the final typed effect, run independent Guardian rules when required, and bind approval to the exact canonical final arguments, target, expiry, recovery plan, and approver receipt. The Workspace gateway is the sole accepted effect route. A changed argument, stale approval, path escape, cross-project target, or direct AgentTeams tool effect is denied.

## Task 4: Finalize trusted facts and the two memory layers

**Create/modify:**

- `benchmarks/secure_memory/substrate/scanner.py`
- `benchmarks/secure_memory/substrate/admission.py`
- `benchmarks/secure_memory/substrate/evaluator_channel.py`
- `apps/api/internal_finalizer.py`
- `apps/api/trusted_memory/closure.py`
- `apps/api/trusted_memory/lifecycle.py`
- `apps/api/trusted_memory/retrieval.py`
- `apps/api/trusted_memory/capsule.py`
- `apps/api/trusted_memory/service.py`
- `tests/memory/test_trusted_memory_closure.py`
- `tests/memory/test_trusted_memory_lifecycle.py`
- `tests/memory/test_trusted_memory_retrieval.py`

Admission-scan all model, Matrix, Workspace, evaluator, memory, and bundle text before trusted persistence. Finalization atomically stores terminal DecisionClosure plus exact signed fact cores. Candidate memories cannot self-promote. Conflicts, supersession, revocation, scope, provenance, and checkpoint watermarks are deterministic and replayable.

## Task 5: Compile focused attention and readable user status

**Create/modify:**

- `apps/agentteams_bridge/extensions/attention.py`
- `apps/agentteams_bridge/extensions/user_status.py`
- `apps/agentteams_bridge/service.py`
- `integrations/agentteams/benchmark_adapter.py`
- `integrations/agentteams/blueprint.yaml`
- `integrations/agentteams/agentteams-resources.yaml.tmpl`
- `tests/agentteams/test_bridge_attention.py`
- `tests/agentteams/test_bridge_user_status.py`

Compile a digest-bound packet containing the active requirement, checkpoint, unresolved failures/conflicts, relevant validated facts, and explicit exclusions. Enforce the frozen token cap and stable order; lower-trust text is quoted as data. Inject only through the existing AgentTeams task request. Render status from admitted events, with current+direct-child depth, glossary explanations, drill-down references, and mandatory approval/security override.

## Task 6: Integrate the D AgentTeams topology

Keep the existing roles and add only a terminal AgentTeams Extractor after Decision. Do not add E's Navigator/Curator/Critic graph. Bind every task/Worker to a signed lease, role, project, checkpoint, attention digest, and budget ticket. Replace Worker/reviewer-PASS strong finalization with the Control internal finalizer; AgentTeams PASS remains collaboration provenance only.

## Task 7: Strong offline acceptance and GitHub delivery

- Run legacy AgentTeams/API/MCP regressions, schema/static/package-boundary checks, fresh SQLite replay, disposable PostgreSQL schema/role/RLS/concurrency/replay checks, and one offline strong end-to-end flow using fake AgentTeams/Matrix/Workspace/Evaluator/provider components.
- Verify user projection depth, glossary coverage, exact approval override, no secret bytes in logs/evidence, and deterministic memory/attention roots.
- Produce a content-addressed acceptance directory with commands, raw normalized results, trace roots, known deferrals, and no production claims for unexecuted VM/provider/GPU/PITR work.
- Push only `semifinal/secure-memory-implementation` to `origin` after clean verification. Do not merge or force-push `semifinal`/main.

