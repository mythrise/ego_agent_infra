# AgentTeams Evidence-Grounded Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace boolean `validated` memory with closure-bound immutable revisions, database-enforced authority, deterministic retrieval/attention capsules, and comparable AgentTeams A/B/C/D/E profiles while preserving legacy readability and SQLite development compatibility.

**Architecture:** A new `apps.api.trusted_memory` package separates untrusted AgentTeams task proposals, trusted closure construction, validator-only lifecycle transitions, filtered retrieval, and deterministic context rendering. PostgreSQL is the production source of truth with forced RLS, narrow NOLOGIN roles, finalizer/validator procedures, append-only events/outbox, and durable cursor replay. All model-driven summary/extraction/navigation/curation/criticism occurs through leased AgentTeams tasks; Control services remain deterministic.

**Tech Stack:** Python 3.9, FastAPI, Pydantic v2, psycopg 3, PostgreSQL/PolarDB PG, SQLite development fallback, Ed25519 via `cryptography`, RXP/1 canonical ledgers, pytest, Ruff, mypy.

**Spec:** [`docs/superpowers/specs/2026-08-30-agentteams-secure-memory-benchmark-design.md`](../specs/2026-08-30-agentteams-secure-memory-benchmark-design.md)

## Global Constraints

- Work in an isolated `ego_agent_infra` worktree after the substrate and AgentTeams safety/attention contracts land; never touch Pi/Codex.
- Do not let the AgentTeams or Workspace VM connect to PostgreSQL. AgentTeams roles submit only leased `CandidateProposal` frames to their configuration's Control VM.
- Every model-driven Summarizer, Extractor, Navigator, Curator, and Critic output must arrive as an official AgentTeams task artifact/envelope and remains untrusted proposal data.
- Do not accept `tenant_id`, Gate, Decision, closure, origin, RXP trust, validator, lifecycle state, or audit head as proposal truth. Recompute them from controller/evaluator/database records.
- Use a distinct `finalizer` Control-service principal to atomically append the authenticated evaluator result, terminal event, origin record, and DecisionClosure. Runtime/curator cannot finalize; validator cannot create or change these inputs; the migration owner is never an application login.
- Freeze one `DecisionClosure` after the terminal state event and in the same database finalization transaction. The closure binds that terminal event hash; the later `decision.closure.frozen` audit event may cite the closure without creating a digest cycle.
- Turns 1-3 may update C's explicitly unverified summary and rebuild D/E working capsules, but they cannot create a final Gate, closure, `after_decision`, or validated cross-task memory.
- One four-turn problem creates exactly one terminal Decision/closure. A sealed fifth modification creates a new generation and closure and cannot rewrite the original.
- `LOCAL_TRUSTED` requires an authenticated sealed evaluator result. `ATTESTED_EXTERNAL` requires a non-agent Ed25519 issuer in the frozen trust configuration. `ORIGIN_UNVERIFIED`, `SYNTHETIC`, and `REVOKED` never promote.
- A `LOCAL_TRUSTED` promotion must match exact canonical `TrustedFactCore` bytes signed inside the evaluator result; the existence of a passing evaluator receipt does not validate arbitrary LLM wording. A trusted graph edge independently requires exact signed `TrustedRelationCore` bytes. External promotion similarly requires the issuer signature to bind the exact fact/relation/revision core.
- Legacy `MemoryRecord(validated=True)` remains readable only through a compatibility view labeled `ORIGIN_UNVERIFIED`; it is never silently upgraded.
- SQL tenant/project/component/version/outcome/origin/lifecycle filters run under forced RLS before ranking. Default retrieval includes only active `VALIDATED` revisions.
- Memory/context text is lower-trust quoted data. It cannot grant approval, capabilities, tool access, policy changes, or instruction precedence.
- SQLite is a development fallback only. Do not use it to claim concurrent writers, database roles, forced RLS, notifications, PITR, or disaster recovery.
- Every PostgreSQL command in this plan must use a disposable database. The existing test fixture recreates `public`.

---

## File Map

Create:

```text
apps/api/trusted_memory/
  __init__.py
  canonical.py
  models.py
  contract.py
  store_contract.py
  closure.py
  origin.py
  scanner.py
  quota.py
  lifecycle.py
  retrieval.py
  capsule.py
  service.py
  legacy.py
  sqlite_store.py
  postgres_store.py
  events.py
  rxp_import.py
  agentteams_tasks.py
  plugins/
    __init__.py
    noop.py
    summary_search.py
    evidence_layered.py
    evidence_graph.py
protocols/rxp/trust.py
apps/api/migrations/postgres/003_trusted_memory_core.sql
apps/api/migrations/postgres/004_rxp_import_and_graph.sql
apps/api/internal_finalizer.py
benchmarks/secure_memory/schemas/
  decision-closure-v1.schema.json
  memory-revision-v1.schema.json
  memory-event-v1.schema.json
  retrieval-result-v1.schema.json
  context-packet-v1.schema.json
tests/memory/
  test_models_and_canonical.py
  test_scanner_and_quota.py
  test_closure.py
  test_origin_attestation.py
  test_lifecycle.py
  test_retrieval_and_capsule.py
  test_plugins.py
  test_rxp_import.py
  test_agentteams_memory_tasks.py
  test_sqlite_compatibility.py
tests/postgres/
  test_memory_authority.py
  test_memory_concurrency.py
  test_memory_notifications.py
  test_memory_replay.py
```

Modify:

```text
apps/api/models.py
apps/api/memory.py
apps/api/store_contract.py
apps/api/store.py
apps/api/postgres_store.py
apps/api/service.py
apps/api/main.py
apps/api/rxp_runtime.py
apps/api/polardb_preflight.py
apps/agentteams_bridge/service.py
apps/agentteams_bridge/clients.py
deploy/postgres/security_roles.sql
deploy/polardb/acceptance-manifest.example.json
deploy/polardb/acceptance-manifest.schema.json
tests/api/test_api.py
tests/api/test_live_finalization.py
tests/api/test_atomicity.py
tests/api/test_rxp_api.py
tests/api/test_domain.py
tests/api/test_polardb_preflight.py
tests/agentteams/test_bridge_strong_finalization.py
tests/postgres/test_postgres_store.py
tests/postgres/test_polardb_preflight.py
pyproject.toml
README.md
docs/postgres-recovery-runbook.md
docs/contracts/secure-agent/v2/contract-digests.json
```

## Task 1: Define canonical memory, closure, retrieval, and plugin contracts

**Files:**

- Create: `apps/api/trusted_memory/__init__.py`
- Create: `apps/api/trusted_memory/canonical.py`
- Create: `apps/api/trusted_memory/models.py`
- Create: `apps/api/trusted_memory/contract.py`
- Create: `apps/api/trusted_memory/store_contract.py`
- Create: `benchmarks/secure_memory/schemas/decision-closure-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/memory-revision-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/memory-event-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/retrieval-result-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/context-packet-v1.schema.json`
- Modify: `docs/contracts/secure-agent/v2/contract-digests.json`
- Create: `tests/memory/test_models_and_canonical.py`
- Modify: `pyproject.toml`

- [ ] Write strict-model tests for unknown fields, noncanonical/duplicate digests, duplicate evidence IDs, invalid state/outcome/origin combinations, overlong IDs, unsorted scope fields, self-containing closure digests, and a `ContextPacket` that attempts to carry an approval/capability field.
- [ ] Confirm collection fails before the package exists.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory/test_models_and_canonical.py
```

- [ ] Implement domain-separated canonical hashing and these fixed enums:

```python
class MemoryOrigin(str, Enum):
    LOCAL_TRUSTED = "LOCAL_TRUSTED"
    ATTESTED_EXTERNAL = "ATTESTED_EXTERNAL"
    ORIGIN_UNVERIFIED = "ORIGIN_UNVERIFIED"
    SYNTHETIC = "SYNTHETIC"
    REVOKED = "REVOKED"


class MemoryState(str, Enum):
    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    QUARANTINED = "QUARANTINED"
    CONFLICTED = "CONFLICTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    SUPERSEDED = "SUPERSEDED"


class DecisionOutcome(str, Enum):
    KEEP = "KEEP"
    DROP = "DROP"
    INCONCLUSIVE = "INCONCLUSIVE"


class RXPPolicy(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    REQUIRED_STRUCTURE_ONLY = "REQUIRED_STRUCTURE_ONLY"
    REQUIRED_TRUSTED_GRANTS = "REQUIRED_TRUSTED_GRANTS"
```

- [ ] Implement `DecisionClosureCore` and keep its digest outside the hashed core:

```python
class DecisionClosureCore(StrictModel):
    schema_version: Literal["egoagentos-decision-closure/v1"]
    closure_id: str
    task_id: str
    generation: int = Field(ge=1)
    task_version: int
    decision_id: str
    decision_event_digest: str
    decision_outcome: DecisionOutcome
    terminal_stage: str
    terminal_state: str
    gate_result_digest: str
    evidence_digests: tuple[str, ...]
    policy_version: str
    rule_version: str
    terminal_audit_head: str
    origin: MemoryOrigin
    evaluator_result_digest: str | None
    verified_fact_digests: tuple[str, ...]
    verified_relation_digests: tuple[str, ...]
    rxp_policy: RXPPolicy
    selected_rxp_roots: tuple[str, ...]
    rxp_policy_digest: str | None


class DecisionClosure(StrictModel):
    core: DecisionClosureCore
    closure_digest: str
```

Validators require sorted unique evidence/fact/relation/RXP digests and policy-consistent presence/absence. Import `TrustedFactCore`, `TrustedRelationCore`, `CandidateProposal`, `MeasuredConfigurationId`, and canonical generation directly from `benchmarks.secure_memory.models`; do not duplicate their wire definitions.

- [ ] Implement immutable revision/event/retrieval/capsule models and deterministic schema export into the five shared schema files. The common schema checker/index must reject drift. `ContextPacket` contains data and citations only:

```python
class ContextPacket(StrictModel):
    schema_version: Literal["secure-memory-context/v1"]
    retrieval_id: str
    trust_label: Literal["NO_MEMORY", "UNVERIFIED_SUMMARY", "VALIDATED_EVIDENCE"]
    objective: str
    active_requirements: tuple[str, ...]
    supersessions: tuple[str, ...]
    code_test_state: tuple[str, ...]
    unresolved_risks: tuple[str, ...]
    safety_boundary: tuple[str, ...]
    memory_items: tuple[ContextMemoryItem, ...]
    unresolved_work: tuple[str, ...]
    packet_digest: str
```

- [ ] Implement the common plugin protocol exactly once:

```python
class MemoryPlugin(Protocol):
    architecture: MemoryArchitecture

    def before_turn(
        self, query: MemoryQuery, task_state: TaskState, budget: ContextBudget
    ) -> ContextPacket: ...

    def retrieve(
        self, query: MemoryQuery, filters: RetrievalFilters, budget: ContextBudget
    ) -> RetrievalResult: ...

    def after_decision(
        self,
        trace: DecisionTrace,
        evidence: Sequence[EvidenceRef],
        decision: DecisionClosure,
    ) -> ProposalSet: ...

    def consolidate(
        self, scope: MemoryScope, watermark: ConsolidationWatermark
    ) -> ConsolidationResult: ...

    def explain(self, retrieval_id: str) -> ProvenanceView: ...
```

Define narrow `ClosureInputStore`, `CuratorStore`, `ValidatorStore`, and `MemoryReadStore` protocols rather than giving every service the full database surface.

- [ ] Add `tests/memory` to pytest paths and `apps.api.trusted_memory*` to package discovery. Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory/test_models_and_canonical.py
uv run --python 3.9 --extra dev ruff check apps/api/trusted_memory tests/memory/test_models_and_canonical.py
uv run --python 3.9 --extra dev mypy apps/api/trusted_memory
```

- [ ] Commit.

```bash
git add pyproject.toml apps/api/trusted_memory/__init__.py apps/api/trusted_memory/canonical.py apps/api/trusted_memory/models.py apps/api/trusted_memory/contract.py apps/api/trusted_memory/store_contract.py benchmarks/secure_memory/schemas/decision-closure-v1.schema.json benchmarks/secure_memory/schemas/memory-revision-v1.schema.json benchmarks/secure_memory/schemas/memory-event-v1.schema.json benchmarks/secure_memory/schemas/retrieval-result-v1.schema.json benchmarks/secure_memory/schemas/context-packet-v1.schema.json docs/contracts/secure-agent/v2/contract-digests.json tests/memory/test_models_and_canonical.py
git commit -m "feat(memory): define trusted memory contracts"
```

## Task 2: Enforce proposal scanning, quotas, and legacy downgrade

**Files:**

- Create: `apps/api/trusted_memory/scanner.py`
- Create: `apps/api/trusted_memory/quota.py`
- Create: `apps/api/trusted_memory/legacy.py`
- Create: `tests/memory/test_scanner_and_quota.py`
- Create: `tests/memory/test_sqlite_compatibility.py`
- Modify: `apps/api/models.py`
- Modify: `apps/api/memory.py`

- [ ] Write tests for invalid UTF-8, secret/PII patterns, prompt-injection/escalation language, binary attachments, overlong statements, too many refs, per-turn/problem/campaign proposal overflow, graph node/edge overflow, memory-byte overflow, duplicate canonical content, and a retry after rejection.
- [ ] Assert schema/quota rejection receipts may contain the canonical non-secret proposal digest, reason codes, bounded lengths/counts, and quota consumption. Secret/PII/credential rejection receipts contain only source class, reason, and count: no raw content, raw-content digest, reversible token, or temporary copy.
- [ ] Write legacy tests proving old `MemoryRecord(validated=True)` parses and displays but maps to `ORIGIN_UNVERIFIED`, is excluded from default retrieval, and cannot be promoted without a new closure-bound revision.
- [ ] Confirm tests fail.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory/test_scanner_and_quota.py tests/memory/test_sqlite_compatibility.py
```

- [ ] Implement immutable limits shared with candidate RPC:

```python
PROPOSALS_PER_TURN = 16
PROPOSALS_PER_PROBLEM = 32
PROPOSALS_PER_CAMPAIGN = 128
CANONICAL_PAYLOAD_BYTES = 64 * 1024
STATEMENT_UTF8_BYTES = 2_048
EVIDENCE_REFS = 16
GRAPH_NODES_PER_PROBLEM = 64
GRAPH_EDGES_PER_PROBLEM = 128
MEMORY_BYTES_PER_PROBLEM = 16 * 1024 * 1024
MEMORY_BYTES_PER_CAMPAIGN = 64 * 1024 * 1024
QUEUE_DEPTH = 32
```

Duplicate accepted content returns the old receipt without an event. Every rejected attempt consumes its proposal opportunity. `trusted_memory.scanner` is a thin memory-policy wrapper over the substrate `EvidenceAdmissionGate`, not a divergent secret scanner; candidate, retrieval, review, and context text pass it before store/forward. Scanner and quota ordering must be deterministic and frozen in a rule version.

- [ ] Keep `apps/api/models.py::MemoryCandidate/MemoryRecord` as deprecated compatibility exports. Keep `apps/api/memory.py::require_validated_memory()` as a deprecated fail-closed wrapper that can only call the new validator contract; do not delete or weaken it while `tests/api/test_domain.py` and existing service callers still import it. Task 13 migrates every caller and only then removes the wrapper if `rg` proves no import remains.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory/test_scanner_and_quota.py tests/memory/test_sqlite_compatibility.py tests/api/test_domain.py
uv run --python 3.9 --extra dev ruff check apps/api/trusted_memory/scanner.py apps/api/trusted_memory/quota.py apps/api/trusted_memory/legacy.py tests/memory/test_scanner_and_quota.py tests/memory/test_sqlite_compatibility.py
uv run --python 3.9 --extra dev mypy apps/api/trusted_memory/scanner.py apps/api/trusted_memory/quota.py apps/api/trusted_memory/legacy.py
```

- [ ] Commit.

```bash
git add apps/api/models.py apps/api/memory.py apps/api/trusted_memory/scanner.py apps/api/trusted_memory/quota.py apps/api/trusted_memory/legacy.py tests/memory/test_scanner_and_quota.py tests/memory/test_sqlite_compatibility.py tests/api/test_domain.py
git commit -m "feat(memory): reject unsafe and unbounded proposals"
```

## Task 3: Reconstruct DecisionClosure and verify origin independently

**Files:**

- Create: `apps/api/trusted_memory/closure.py`
- Create: `apps/api/trusted_memory/origin.py`
- Create: `tests/memory/test_closure.py`
- Create: `tests/memory/test_origin_attestation.py`

- [ ] Write closure tests for missing Gate/evidence/decision/terminal event/evaluator fact/relation, wrong generation/task/version/outcome, unsorted evidence/facts/relations, changed fact/relation bytes or support digest, changed audit head, stale closure, cross-lineage or cross-fact replay, decision substitution, terminal mismatch, selected RXP root mismatch, and proposal-supplied trusted fields. The same closure may promote several distinct signed facts exactly once per candidate lineage.
- [ ] Write origin tests for a valid evaluator receipt, forged evaluator signature, cross-arm receipt, valid external Ed25519 attestation, agent/self attestation, unknown/revoked/expired/not-yet-valid key, wrong algorithm/key ID/schema digest, modified canonical bytes, and trust snapshot mismatch.
- [ ] Confirm tests fail.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory/test_closure.py tests/memory/test_origin_attestation.py
```

- [ ] Implement `ClosureBuilder` over read-only trusted inputs:

```python
class ClosureBuilder:
    def freeze(
        self,
        task: TerminalTaskSnapshot,
        gate: TrustedGateSnapshot,
        decision: TrustedDecisionEvent,
        evidence: Sequence[TrustedEvidenceRef],
        terminal_event: TrustedAuditEvent,
        rxp: FrozenRXPSelection,
        origin: VerifiedOrigin,
    ) -> DecisionClosure:
        require_same_task_generation(task, gate, decision, evidence, terminal_event, rxp)
        require_terminal_event(task, terminal_event)
        require_gate_and_decision(gate, decision)
        require_verified_origin(origin, task, decision)
        core = build_closure_core(
            task=task,
            gate=gate,
            decision=decision,
            evidence=tuple(sorted(evidence, key=lambda item: item.digest)),
            terminal_event=terminal_event,
            rxp=rxp,
            origin=origin,
        )
        return DecisionClosure(
            core=core,
            closure_digest=canonical_sha256("decision-closure", core),
        )
```

It recomputes every digest, validates causal identity, sorts sets, builds `DecisionClosureCore`, hashes it, and returns immutable bytes. It never accepts a proposal or blind review as a trusted input.

- [ ] Implement frozen trust configuration:

```python
class TrustKey(StrictModel):
    issuer_id: str
    key_id: str
    algorithm: Literal["Ed25519"]
    public_key_base64: str
    valid_from: datetime
    valid_until: datetime
    revoked_at: datetime | None


class TrustSnapshot(StrictModel):
    keys: tuple[TrustKey, ...]
    canonical_schema_digest: str
    revocation_snapshot_digest: str
    snapshot_digest: str
```

Only controller-authenticated evaluator results produce `LOCAL_TRUSTED`, and only for exact signed `TrustedFactCore`/`TrustedRelationCore` bytes included in the closure. Merely connecting two trusted endpoints never creates a trusted edge. Only a non-agent issuer in this snapshot produces `ATTESTED_EXTERNAL`, and its signature preimage must include the exact fact/relation/revision core, scope, outcome, validity, schema, and source receipt.

- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory/test_closure.py tests/memory/test_origin_attestation.py
uv run --python 3.9 --extra dev ruff check apps/api/trusted_memory/closure.py apps/api/trusted_memory/origin.py tests/memory/test_closure.py tests/memory/test_origin_attestation.py
uv run --python 3.9 --extra dev mypy apps/api/trusted_memory/closure.py apps/api/trusted_memory/origin.py
```

- [ ] Commit.

```bash
git add apps/api/trusted_memory/closure.py apps/api/trusted_memory/origin.py tests/memory/test_closure.py tests/memory/test_origin_attestation.py
git commit -m "feat(memory): bind promotion to trusted decision closure"
```

## Task 4: Implement immutable lifecycle, CAS, and SQLite semantic fallback

**Files:**

- Create: `apps/api/trusted_memory/lifecycle.py`
- Create: `apps/api/trusted_memory/sqlite_store.py`
- Create: `tests/memory/test_lifecycle.py`
- Modify: `tests/memory/test_sqlite_compatibility.py`

- [ ] Write transition-table tests for every allowed and forbidden edge, including attempted terminal-to-trusted resurrection. Corrected content must create a new revision linked to the old lineage.
- [ ] Test expected-prior-revision CAS, duplicate idempotency key, same idempotency key with different command digest, concurrent promote/revoke ordering, stale closure, duplicate `(closure, fact, lineage)` consumption, permitted second fact/lineage consumption from the same multi-fact closure, cross-lineage replay, conflict quarantine, evidence-triggered revocation, and deterministic state rebuild from events.
- [ ] Test SQLite rollback across revision/event/current/outbox, duplicate replay, and documented `production_guarantees=False` flags.
- [ ] Confirm tests fail.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory/test_lifecycle.py tests/memory/test_sqlite_compatibility.py
```

- [ ] Encode the lifecycle as data, not scattered conditionals:

```python
ALLOWED_TRANSITIONS = {
    MemoryState.CANDIDATE: frozenset({
        MemoryState.VALIDATED,
        MemoryState.REJECTED,
        MemoryState.QUARANTINED,
        MemoryState.CONFLICTED,
        MemoryState.EXPIRED,
        MemoryState.REVOKED,
    }),
    MemoryState.VALIDATED: frozenset({
        MemoryState.SUPERSEDED,
        MemoryState.CONFLICTED,
        MemoryState.EXPIRED,
        MemoryState.REVOKED,
    }),
}
```

All other source states have no outgoing transitions. A validator command includes tenant, lineage, expected revision, target state, reason code, closure digest, actor identity, and idempotency key.

- [ ] Implement SQLite tables/events/current/outbox with one transaction and an application lock, while reporting that role/RLS/concurrency/notification/PITR claims are unsupported. Rebuild `memory_current` solely from ordered events in tests.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory/test_lifecycle.py tests/memory/test_sqlite_compatibility.py
uv run --python 3.9 --extra dev ruff check apps/api/trusted_memory/lifecycle.py apps/api/trusted_memory/sqlite_store.py tests/memory/test_lifecycle.py tests/memory/test_sqlite_compatibility.py
uv run --python 3.9 --extra dev mypy apps/api/trusted_memory/lifecycle.py apps/api/trusted_memory/sqlite_store.py
```

- [ ] Commit.

```bash
git add apps/api/trusted_memory/lifecycle.py apps/api/trusted_memory/sqlite_store.py tests/memory/test_lifecycle.py tests/memory/test_sqlite_compatibility.py
git commit -m "feat(memory): enforce immutable lifecycle and replay"
```

## Task 5: Create PostgreSQL trusted-memory schema and role boundary

**Files:**

- Create: `apps/api/migrations/postgres/003_trusted_memory_core.sql`
- Modify: `deploy/postgres/security_roles.sql`
- Create: `tests/postgres/test_memory_authority.py`
- Modify: `tests/postgres/test_postgres_store.py`
- Modify: `apps/api/polardb_preflight.py`
- Modify: `deploy/polardb/acceptance-manifest.example.json`
- Modify: `deploy/polardb/acceptance-manifest.schema.json`
- Modify: `tests/api/test_polardb_preflight.py`
- Modify: `tests/postgres/test_polardb_preflight.py`

- [ ] Write PostgreSQL tests that first fail against migrations 001/002. They must prove:
  - runtime/curator cannot insert/update/delete validated revisions/current/events/outbox or finalization inputs;
  - finalizer can invoke terminal finalization only and cannot submit candidates, transition memory, alter evidence/RXP inputs, or read another tenant;
  - validator cannot create/change candidate, evidence, Gate, Decision, audit, closure, origin, or RXP inputs;
  - auditor is read-only;
  - no login role can `SET ROLE` another group/owner, create schema, alter table, disable trigger, grant, truncate, or own a protected function/table;
  - every tenant table has `ENABLE` and `FORCE ROW LEVEL SECURITY`;
  - arbitrary `SET LOCAL egoagentos.tenant_id='victim'` fails because the login-to-tenant mapping does not authorize it, and cross-tenant reads/writes fail after pooled tenant reset;
  - direct forged-closure promotion and owner/trigger bypass attempts fail;
  - the old runtime `INSERT memories` grant is gone.
- [ ] Add migration compatibility tests that expect ordered 001, 002, 003 instead of hard-coding two migrations.
- [ ] Run only against a disposable database and confirm failures.

```bash
test -n "${EGO_TEST_POSTGRES_URL:-}"
uv run --python 3.9 --extra dev pytest -q \
  tests/postgres/test_memory_authority.py \
  tests/postgres/test_postgres_store.py \
  tests/postgres/test_polardb_preflight.py
```

- [ ] Create append-only input/history tables and rebuildable views:

```text
decision_closures
trusted_evaluator_results
origin_attestations
service_principal_tenants
memory_candidate_revisions
memory_revisions
memory_events
memory_current
memory_outbox
memory_consumer_cursors
memory_retrievals
memory_retrieval_items
memory_summary_revisions
```

Every table has `tenant_id`; append-only tables reject update/delete/truncate. `memory_current` and consumer cursors are mutable only through owner-controlled functions.

- [ ] Define NOLOGIN group roles `egoagentos_runtime`, `egoagentos_memory_curator`, `egoagentos_memory_finalizer`, `egoagentos_memory_validator`, and `egoagentos_auditor`. Deployment creates separate per-arm/per-tenant LOGIN identities outside source and grants each exactly one group role. Revoke PUBLIC schema/table/sequence/function/default privileges. The finalizer service verifies Ed25519 signed bytes against the frozen manifest, then receives EXECUTE only on a fixed-search-path `egoagentos_finalize_decision` procedure that checks principal/tenant/identity/sequence/uniqueness and atomically writes the exact signed evaluator bytes, origin, terminal event, and closure inputs; it receives no direct input-table DML. Fresh replay independently re-verifies the stored signature instead of trusting the service flag.
- [ ] Store the DBA-owned mapping `(session_user, tenant_id, service_role)` in `service_principal_tenants`. Make `egoagentos_current_tenant()` return the transaction GUC only when that mapping authorizes the current login and role. RLS policies require both row tenant equality and this mapping, so setting a custom GUC alone grants nothing. Revoke mapping-table DML and tenant-setter execution from all Agent-facing identities.
- [ ] Implement one validator-owned procedure with fixed search path and typed parameters:

```sql
CREATE FUNCTION public.egoagentos_apply_memory_transition(
    p_tenant_id TEXT,
    p_lineage_id TEXT,
    p_expected_revision INTEGER,
    p_target_state TEXT,
    p_closure_digest TEXT,
    p_trusted_fact_digest TEXT,
    p_reason_code TEXT,
    p_idempotency_key TEXT
) RETURNS TABLE(event_id TEXT, revision_id TEXT, outbox_sequence BIGINT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NOT pg_has_role(session_user, 'egoagentos_memory_validator', 'member') THEN
        RAISE EXCEPTION 'validator role required' USING ERRCODE = 'insufficient_privilege';
    END IF;
    PERFORM public.egoagentos_assert_mapped_tenant(p_tenant_id, 'validator');
    PERFORM public.egoagentos_assert_memory_transition(
        p_tenant_id, p_lineage_id, p_expected_revision,
        p_target_state, p_closure_digest, p_trusted_fact_digest, p_reason_code
    );
    RETURN QUERY SELECT * FROM public.egoagentos_append_memory_transition(
        p_tenant_id, p_lineage_id, p_expected_revision,
        p_target_state, p_closure_digest, p_trusted_fact_digest,
        p_reason_code, p_idempotency_key
    );
END;
$$;
```

Create the three named helper functions in the same migration as migration-owner-only `SECURITY DEFINER` functions with fixed `search_path`; revoke PUBLIC and grant only the outer transition function to validator. `egoagentos_assert_memory_transition` locks the current lineage and checks tenant, CAS, closure digest, Gate PASS, decision/outcome/source equality, byte-exact signed fact, lifecycle legality, and uniqueness of `(closure_digest, trusted_fact_digest, candidate_lineage_id)` for promotion. A closure is not globally consumed because it may contain plural facts. `egoagentos_append_memory_transition` appends revision/event/outbox and updates `memory_current` in the caller's transaction. Include `p_tenant_id = egoagentos_current_tenant()` in SQL; application checks are defense in depth, not authority.

- [ ] Add `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` plus explicit policies to every tenant table. Each store transaction sets a validated tenant with `SET LOCAL` and verifies it resets before pool reuse.
- [ ] Extend PolarDB preflight and both acceptance-manifest files with expected roles/tables/owners/RLS/procedures/triggers, finalizer/validator separation, login-to-tenant mapping proof, and fresh-replay roots. Update both API/PostgreSQL preflight tests. Run PostgreSQL tests until all pass.
- [ ] Commit.

```bash
git add apps/api/migrations/postgres/003_trusted_memory_core.sql deploy/postgres/security_roles.sql deploy/polardb/acceptance-manifest.example.json deploy/polardb/acceptance-manifest.schema.json tests/postgres/test_memory_authority.py tests/postgres/test_postgres_store.py apps/api/polardb_preflight.py tests/api/test_polardb_preflight.py tests/postgres/test_polardb_preflight.py
git commit -m "feat(postgres): enforce validator-only memory transitions"
```

## Task 6: Implement PostgreSQL stores, post-commit notification, and concurrency replay

**Files:**

- Create: `apps/api/trusted_memory/postgres_store.py`
- Create: `apps/api/trusted_memory/events.py`
- Create: `tests/postgres/test_memory_concurrency.py`
- Create: `tests/postgres/test_memory_notifications.py`
- Create: `tests/postgres/test_memory_replay.py`
- Modify: `apps/api/postgres_store.py`
- Modify: `apps/api/store_contract.py`

- [ ] Write tests for two validators racing promote/revoke, stale CAS, duplicate idempotency delivery, same key/different command, crash after event before client response, rollback with no outbox/notification, committed outbox notification, notification loss plus cursor catch-up, listener reconnect, cursor replay, and a rebuild whose root/current view equals the source database.
- [ ] Confirm tests fail against the schema-only implementation.
- [ ] Implement role-specific stores/connections; never expose a union credential object to the Agent RPC layer.

```python
@dataclass(frozen=True)
class TrustedMemoryStores:
    finalizer: ClosureInputStore
    curator: CuratorStore
    validator: ValidatorStore
    reader: MemoryReadStore
```

Each transaction begins, validates the caller's fixed service role and DBA-owned login-to-tenant mapping, sets `SET LOCAL egoagentos.tenant_id`, verifies `egoagentos_current_tenant()` returns that tenant, performs one narrow operation, and commits/rolls back. It cannot accept a role name from user input.

- [ ] Implement `ego_memory_events` notification on outbox insert. Rely on PostgreSQL's commit delivery; the listener treats NOTIFY only as a wake-up and always reads `memory_outbox WHERE sequence > cursor ORDER BY sequence`.
- [ ] Implement fresh replay: migrate an empty schema, replay closures/candidates/events/RXP inputs in sequence, rebuild current/cursors, and compare canonical state root and expected negative gate stops.
- [ ] Run disposable-PostgreSQL tests.

```bash
test -n "${EGO_TEST_POSTGRES_URL:-}"
uv run --python 3.9 --extra dev pytest -q \
  tests/postgres/test_memory_authority.py \
  tests/postgres/test_memory_concurrency.py \
  tests/postgres/test_memory_notifications.py \
  tests/postgres/test_memory_replay.py
```

- [ ] Commit.

```bash
git add apps/api/trusted_memory/postgres_store.py apps/api/trusted_memory/events.py apps/api/postgres_store.py apps/api/store_contract.py tests/postgres/test_memory_concurrency.py tests/postgres/test_memory_notifications.py tests/postgres/test_memory_replay.py
git commit -m "feat(memory): persist concurrent events and durable notifications"
```

## Task 7: Import and bind RXP ledgers atomically

**Files:**

- Create: `protocols/rxp/trust.py`
- Create: `apps/api/trusted_memory/rxp_import.py`
- Create: `apps/api/migrations/postgres/004_rxp_import_and_graph.sql`
- Create: `tests/memory/test_rxp_import.py`
- Modify: `apps/api/rxp_runtime.py`
- Modify: `tests/api/test_rxp_api.py`
- Modify: `tests/postgres/test_postgres_store.py`
- Modify: `apps/api/polardb_preflight.py`
- Modify: `deploy/postgres/security_roles.sql`
- Modify: `deploy/polardb/acceptance-manifest.example.json`
- Modify: `deploy/polardb/acceptance-manifest.schema.json`
- Modify: `tests/api/test_polardb_preflight.py`
- Modify: `tests/postgres/test_polardb_preflight.py`

- [ ] Write unit/integration tests for document mutation, duplicate JSON keys, canonical reserialization mismatch, ledger/Merkle/causal/root substitution, cross-task/generation graft, incomplete cells, missing Decisions, unknown/expired/revoked/bad-signature Grant, empty trust config, structure-only versus trusted policy, supplied-invalid document under `NOT_REQUIRED`, multiple matrices, favorable cherry-pick, partial import, duplicate idempotency, and policy downgrade after manifest freeze.
- [ ] Confirm tests fail.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory/test_rxp_import.py tests/api/test_rxp_api.py
```

- [ ] Implement a frozen resolver for the existing RXP Grant algorithm. It maps `(issuer_id, key_id, algorithm)` to a Control-VM verifier, validity window, revocation snapshot, and source receipt policy; unknown algorithms/keys fail. Do not let an uploaded ledger supply resolver configuration.
- [ ] Persist immutable, sanitized import revisions:

```python
class RXPImportRevision(StrictModel):
    import_id: str
    task_id: str
    generation: int = Field(ge=1)
    document_digest: str
    schema_version: str
    verifier_version: str
    importer_identity: str
    imported_at: datetime
    verification_status: str
    matrix_root: str | None
    complete_cell_count: int
    missing_decisions: tuple[str, ...]
    grant_trust_status: str
    source_receipt_handle: str
    trust_policy_digest: str
```

Add `rxp_import_revisions`, `task_rxp_selections`, and `memory_graph_edge_revisions` in migration 004. Import, audit event, and task-generation linkage commit in one transaction or not at all. Extend security roles, PolarDB preflight, both acceptance manifests, and both preflight tests to require migrations 001-004 plus every new owner/grant/RLS/table contract.

- [ ] Freeze the required matrix ID set and all-complete selection rule before execution. Bind exact selected roots and policy digest into the closure. A new selection/root/policy/key snapshot creates a new revision and requires revalidation.
- [ ] Keep `/rxp/verify` as an untrusted diagnostic endpoint with `signature_trust_verified=False`. Add trusted import only as a separate Control-service method authenticated by the internal Control channel/principal; do not register it on the public FastAPI router. Never turn diagnostic verification into implicit persistence.
- [ ] Run unit/protocol/PostgreSQL tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory/test_rxp_import.py tests/api/test_rxp_api.py tests/protocols
test -n "${EGO_TEST_POSTGRES_URL:-}"
uv run --python 3.9 --extra dev pytest -q tests/postgres/test_memory_replay.py tests/postgres/test_postgres_store.py tests/postgres/test_polardb_preflight.py tests/api/test_polardb_preflight.py
```

- [ ] Commit.

```bash
git add protocols/rxp/trust.py apps/api/trusted_memory/rxp_import.py apps/api/migrations/postgres/004_rxp_import_and_graph.sql tests/memory/test_rxp_import.py apps/api/rxp_runtime.py tests/api/test_rxp_api.py tests/postgres/test_postgres_store.py apps/api/polardb_preflight.py deploy/postgres/security_roles.sql deploy/polardb/acceptance-manifest.example.json deploy/polardb/acceptance-manifest.schema.json tests/api/test_polardb_preflight.py tests/postgres/test_polardb_preflight.py
git commit -m "feat(rxp): bind trusted imports to task generations"
```

## Task 8: Implement filtered retrieval and deterministic Attention Capsules

**Files:**

- Create: `apps/api/trusted_memory/retrieval.py`
- Create: `apps/api/trusted_memory/capsule.py`
- Create: `tests/memory/test_retrieval_and_capsule.py`

- [ ] Write retrieval tests for tenant/project/component/version/outcome/origin/lifecycle filters, cross-tenant attacks, stale/superseded/revoked/conflicted/unverified exclusions, explicit diagnostic failure/conflict mode, deterministic ties, lazy evidence loading, and exact explain provenance.
- [ ] Write capsule tests for active requirement supersession, current code/test state, unresolved risks/work, fixed safety boundary, 2,048-token memory slice, deterministic rebuild, relevant-item density, no append drift, and quoted-data rendering under prompt-injection content.
- [ ] Confirm tests fail.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory/test_retrieval_and_capsule.py
```

- [ ] Implement SQL-first filtering and a deterministic five-signal score compatible with the current weights:

```python
MEMORY_WEIGHTS = {
    "semantic": 0.45,
    "component": 0.20,
    "evidence": 0.15,
    "recency": 0.10,
    "failure": 0.10,
}
```

Normalize PostgreSQL full-text rank into `[0, 1]`; use exact structured fields for other signals. Sort by score descending, then revision ID ascending. Persist query/filter/policy digests, every component score, selected revision IDs, and retrieval digest.

- [ ] Implement an injected `TokenCounter` and conservative UTF-8 fallback. Pack objective/requirements/safety first, then memory items by deterministic rank, then unresolved work. Never truncate a citation/revision ID into ambiguity.
- [ ] Render the packet under a fixed lower-trust wrapper and reject authority-shaped fields at model validation. `explain(retrieval_id)` returns revision IDs, score components, query/policy/capsule digests, evidence refs, and lifecycle/origin.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory/test_retrieval_and_capsule.py
uv run --python 3.9 --extra dev ruff check apps/api/trusted_memory/retrieval.py apps/api/trusted_memory/capsule.py tests/memory/test_retrieval_and_capsule.py
uv run --python 3.9 --extra dev mypy apps/api/trusted_memory/retrieval.py apps/api/trusted_memory/capsule.py
```

- [ ] Commit.

```bash
git add apps/api/trusted_memory/retrieval.py apps/api/trusted_memory/capsule.py tests/memory/test_retrieval_and_capsule.py
git commit -m "feat(memory): build focused evidence capsules"
```

## Task 9: Bind every model-driven memory role to AgentTeams taskflow

**Files:**

- Create: `apps/api/trusted_memory/agentteams_tasks.py`
- Create: `tests/memory/test_agentteams_memory_tasks.py`

**Interfaces:**

- Consumes: `apps.agentteams_bridge.extensions.contracts.SignedTaskLease`, official AgentTeams task/artifact receipt digests, `ContextPacket`, and candidate schemas.
- Produces: `AgentTeamsMemoryTaskClient` and `validate_memory_task_result()` used by C/D/E plugins; it never exposes a provider transport.

- [ ] Write failing tests for correct C/D/E roles and for wrong configuration/project/task/worker/role/stage/ticket/context/packet digest, undeclared output artifact, modified artifact bytes, stale turn, pre-terminal D/E maintenance, post-terminal Navigator, extra role call, direct provider callback, and AgentTeams output that supplies Gate/Decision/origin/validated fields.

Use this only model-call interface:

```python
class MemoryTaskRole(str, Enum):
    SUMMARIZER = "summarizer"
    EXTRACTOR = "extractor"
    NAVIGATOR = "navigator"
    CURATOR = "curator"
    CRITIC = "critic"


class AgentTeamsMemoryTaskClient(Protocol):
    def dispatch(
        self,
        *,
        lease: SignedTaskLease,
        role: MemoryTaskRole,
        input_packet: ContextPacket,
        expected_output_schema: str,
    ) -> AgentTeamsTaskHandle: ...

    def result(self, handle: AgentTeamsTaskHandle) -> UntrustedMemoryTaskResult: ...
```

The production implementation delegates through the existing AgentTeams bridge/TeamHarness/Matrix path. The memory service can validate and consume a returned task artifact but cannot instantiate an HTTP/provider model client. Unit tests use a deterministic fake task client.

- [ ] Enforce the frozen cadence in deterministic code: C Summarizer once after each released turn; D Extractor once after each problem's sealed terminal Decision; E Navigator once before each turn and Extractor -> Curator -> Critic once after each terminal Decision. A/B dispatch no memory task. Every call needs a matching role/ticket lease.
- [ ] Convert accepted AgentTeams artifacts only to untrusted summary/candidate/graph proposals with exact source/task/output digests. Reject trust-shaped keys before persistence. A valid AgentTeams reviewer/tool/artifact receipt never upgrades origin.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory/test_agentteams_memory_tasks.py
uv run --python 3.9 --extra dev ruff check apps/api/trusted_memory/agentteams_tasks.py tests/memory/test_agentteams_memory_tasks.py
uv run --python 3.9 --extra dev mypy apps/api/trusted_memory/agentteams_tasks.py
```

- [ ] Commit.

```bash
git add apps/api/trusted_memory/agentteams_tasks.py tests/memory/test_agentteams_memory_tasks.py
git commit -m "feat(memory): bind maintenance roles to AgentTeams"
```

## Task 10: Implement A/B and C plugins

**Files:**

- Create: `apps/api/trusted_memory/plugins/__init__.py`
- Create: `apps/api/trusted_memory/plugins/noop.py`
- Create: `apps/api/trusted_memory/plugins/summary_search.py`
- Create: `tests/memory/test_plugins.py`

- [ ] Test A and B execute the entire no-op plugin boundary but never dispatch a memory role, persist, or inject memory and return `trust_label=NO_MEMORY`.
- [ ] Test C accepts a schema-checked summarizer proposal after each turn, stores immutable summary revisions plus one active pointer, deterministically searches tags then full text, lazy-loads details, and always renders `UNVERIFIED_SUMMARY`.
- [ ] Test summary drift, conflicting/superseded requirement, malicious instruction, false validation claim, and legacy record never become trusted or authorize a tool.
- [ ] Confirm tests fail.
- [ ] Implement A/B as the same true no-op with receipts so boundary overhead is measured; their safety profiles differ outside memory.
- [ ] Implement C with the Task-9 `AgentTeamsMemoryTaskClient`; no `SummaryGenerator`, provider callback, or campaign-broker model call is permitted in the memory package. The AgentTeams Summarizer artifact becomes an `UNVERIFIED_SUMMARY` proposal. `consolidate()` updates the within-task summary on turns 1-3; `after_decision()` may create the final summary revision after turn four but never a validated D/E revision.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory/test_plugins.py -k 'noop or summary'
uv run --python 3.9 --extra dev ruff check apps/api/trusted_memory/plugins tests/memory/test_plugins.py
uv run --python 3.9 --extra dev mypy apps/api/trusted_memory/plugins
```

- [ ] Commit.

```bash
git add apps/api/trusted_memory/plugins/__init__.py apps/api/trusted_memory/plugins/noop.py apps/api/trusted_memory/plugins/summary_search.py tests/memory/test_plugins.py
git commit -m "feat(memory): add no-memory and summary baselines"
```

## Task 11: Implement D evidence-layered memory

**Files:**

- Create: `apps/api/trusted_memory/plugins/evidence_layered.py`
- Create: `apps/api/trusted_memory/service.py`
- Modify: `tests/memory/test_plugins.py`

- [ ] Test candidate extraction occurs only after a terminal closure; model-produced candidates remain untrusted until deterministic promotion; turns 1-3 only rebuild working capsules. A candidate can promote only when its claimed fact ID, decoded canonical UTF-8 statement bytes, outcome, applicability scope, source refs, and support digests equal a signed evaluator `TrustedFactCore` named by the closure (or an exact external-attestation core); a generic passing receipt is insufficient.
- [ ] Test KEEP, DROP, and INCONCLUSIVE outcome semantics; verified failures/inconclusive observations may be retained with explicit labels but cannot masquerade as success.
- [ ] Test missing/failed Gate, foreign/stale closure, duplicate `(closure,fact,lineage)` use, cross-lineage replay, wrong outcome/source, synthetic/unverified origin, conflicting content, invalidating evidence, supersession, and duplicate candidate all fail closed with stable reason codes; a different signed fact from the same multi-fact closure remains promotable.
- [ ] Confirm tests fail.
- [ ] Implement `EvidenceLayeredMemory` using the common stores/retrieval/capsule services. Its leased AgentTeams Extractor task proposes candidates and signed fact IDs; `TrustedMemoryService` recomputes proposal identity, closure, origin, fact bytes, support digests, and quotas before curator insert. For trusted promotion it renders/compares the canonical evaluator fact instead of trusting AgentTeams/LLM prose. Unmatched procedural advice remains `ORIGIN_UNVERIFIED`. The deterministic validator procedure remains the only promotion path.
- [ ] Record only `CITATION_BOUND` when the final rationale cites an exact revision ID and the trace links it to a plan, patch, or test decision. This is a cited-use proxy, not causal attention/use; merely injecting a record does not count and actual attention remains `UNPROVEN`.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory/test_plugins.py -k 'evidence or layered or decision'
uv run --python 3.9 --extra dev ruff check apps/api/trusted_memory/plugins/evidence_layered.py apps/api/trusted_memory/service.py tests/memory/test_plugins.py
uv run --python 3.9 --extra dev mypy apps/api/trusted_memory/plugins/evidence_layered.py apps/api/trusted_memory/service.py
```

- [ ] Commit.

```bash
git add apps/api/trusted_memory/plugins/evidence_layered.py apps/api/trusted_memory/service.py tests/memory/test_plugins.py
git commit -m "feat(memory): add closure-validated two-layer memory"
```

## Task 12: Implement E multi-agent evidence graph

**Files:**

- Create: `apps/api/trusted_memory/plugins/evidence_graph.py`
- Modify: `tests/memory/test_plugins.py`

- [ ] Test leased AgentTeams Extractor, Curator, Critic, and Navigator outputs remain proposals. None can promote, alter closure/evidence, call a provider directly, or bypass the deterministic validator.
- [ ] Test node types fact/requirement/component/decision/failure/evidence and the sole frozen edge enum `SUPPORTED_BY`, `CONTRADICTS`, `SUPERSEDES`, `DEPENDS_ON`, `APPLIES_TO`, `CAUSED_BY`, `FAILED_UNDER`, `VERIFIED_BY`.
- [ ] Test 64-node/128-edge quotas, deterministic deduplication, max two hops, cycle handling, same 2,048-token memory allowance, unresolved conflict quarantine, and evidence invalidation cascading through validator-owned revocation events.
- [ ] Confirm tests fail.
- [ ] Implement graph revision proposals over migration 004 tables. A promoted node must match an exact signed fact revision. A promoted edge must separately match byte-for-byte a signed `TrustedRelationCore` binding its type, exact source/target fact digests, scope, source refs, and support digests; two trusted endpoints are insufficient. Unmatched Curator/LLM edges remain `ORIGIN_UNVERIFIED`, are excluded from trusted traversal/injection, and false-edge tests fail closed. Trusted edges carry relation/evidence/closure digests, lifecycle, tenant/scope, and validator event. Navigator produces a bounded traversal plan; deterministic code performs and packs it.
- [ ] Make batching explicit so campaign accounting can label the official AgentTeams Project/task/Worker/role/ticket for Navigator per turn and Extractor/Curator/Critic per problem. Unit tests use the Task-9 fake AgentTeams client and assert exact call counts; production code has no alternate role runner.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory/test_plugins.py -k 'graph or navigator or conflict'
uv run --python 3.9 --extra dev ruff check apps/api/trusted_memory/plugins/evidence_graph.py tests/memory/test_plugins.py
uv run --python 3.9 --extra dev mypy apps/api/trusted_memory/plugins/evidence_graph.py
```

- [ ] Commit.

```bash
git add apps/api/trusted_memory/plugins/evidence_graph.py tests/memory/test_plugins.py
git commit -m "feat(memory): add bounded evidence graph"
```

## Task 13: Integrate live finalization, APIs, replay, and recovery proof

**Files:**

- Modify: `apps/api/service.py`
- Create: `apps/api/internal_finalizer.py`
- Modify: `apps/api/main.py`
- Modify: `apps/api/memory.py`
- Modify: `apps/api/store.py`
- Modify: `apps/api/postgres_store.py`
- Modify: `apps/api/store_contract.py`
- Modify: `tests/api/test_api.py`
- Modify: `tests/api/test_live_finalization.py`
- Modify: `tests/api/test_atomicity.py`
- Modify: `tests/api/test_domain.py`
- Modify: `tests/api/test_rxp_api.py`
- Modify: `apps/agentteams_bridge/service.py`
- Modify: `apps/agentteams_bridge/clients.py`
- Modify: `tests/agentteams/test_bridge_strong_finalization.py`
- Modify: `README.md`
- Modify: `docs/postgres-recovery-runbook.md`

- [ ] Update old tests that expect synthetic auto-promotion. New expected behavior: synthetic creates `SYNTHETIC` candidates only; `validated_memories` does not increase.
- [ ] Test AgentTeams live artifacts remain `ORIGIN_UNVERIFIED` collaboration records and do not promote. Test evaluator-authenticated finalization atomically appends only the signed evaluator bytes, terminal event, Gate, Decision, origin inputs, closure, audit, and outbox; it permits no model candidate in that transaction. Implement the safety-plan protocol: bridge `StrongFinalizerClient` sends only stored evaluator-binding/checkpoint references over the authenticated internal Control channel; `internal_finalizer.py` re-reads/verifies the exact bytes and invokes `egoagentos_finalize_decision`.
- [ ] Prove `STRONG_CAMPAIGN` cannot invoke `EgoClient.finalize_live()` or public `/api/v1/tasks/{id}/finalize`, including after a Worker/reviewer PASS. Public finalization remains labeled legacy only and has no strong finalizer credential.
- [ ] Test any failure between evaluator binding, terminal event, Gate, Decision, closure, audit, or outbox rolls back all finalization rows and emits no notification. After commit, a separately leased AgentTeams maintenance task may create untrusted candidates in its own transaction; maintenance failure cannot rewrite/roll back the terminal closure and is recorded as `AFTER_DECISION_FAILED`.
- [ ] Test a third, validator-only transaction can promote only exact closure-bound facts. AgentTeams task success, Worker reviewer PASS, or presence of a passing evaluator receipt without exact fact-byte equality cannot promote.
- [ ] Test exactly one closure per task generation and immutable original closure after a sealed follow-up generation.
- [ ] Add read-only retrieve/explain endpoints. Implement trusted RXP import as a Control-only authenticated service method reachable only through the narrow internal Control channel, not a normal Agent/FastAPI route. Keep candidate RPC and validator transition on separate least-privilege internal interfaces; no Agent-facing process receives finalizer/validator credentials.
- [ ] Migrate every `require_validated_memory()` caller to the new service in this task, update `tests/api/test_domain.py`, and run `rg -n "require_validated_memory" apps tests` before deleting the deprecated wrapper. If a compatibility import remains, retain the fail-closed wrapper; never replace it with boolean trust.
- [ ] Run API tests and fix compatibility without restoring boolean-trust semantics.

```bash
uv run --python 3.9 --extra dev pytest -q \
  tests/api/test_api.py \
  tests/api/test_live_finalization.py \
  tests/api/test_atomicity.py \
  tests/api/test_domain.py \
  tests/api/test_rxp_api.py \
  tests/memory \
  tests/agentteams/test_bridge_strong_finalization.py
```

- [ ] Extend the recovery runbook with:
  - fresh-schema migrate/replay/compare commands;
  - a disposable PostgreSQL backup/restore drill with manifest and resulting state-root digests;
  - PolarDB PG PITR steps explicitly marked `NOT_EXECUTED` unless credentials and a target instance are supplied;
  - role/grant/owner/RLS dumps and notification/cursor verification;
  - failure rollback and replay evidence paths.
- [ ] Run the complete memory gate.

```bash
uv run --python 3.9 --extra dev pytest -q tests/memory tests/api tests/protocols
uv run --python 3.9 --extra dev ruff check apps/api/trusted_memory apps/api protocols/rxp tests/memory tests/api tests/protocols
uv run --python 3.9 --extra dev mypy apps/api/trusted_memory apps/api protocols/rxp

test -n "${EGO_TEST_POSTGRES_URL:-}"
uv run --python 3.9 --extra dev pytest -q tests/postgres
```

Expected: all commands exit 0 on a disposable PostgreSQL target. If no target exists, implementation is not campaign-ready; do not reinterpret skipped PostgreSQL tests as PASS.

- [ ] Commit.

```bash
git add apps/api/service.py apps/api/internal_finalizer.py apps/api/main.py apps/api/memory.py apps/api/store.py apps/api/postgres_store.py apps/api/store_contract.py apps/agentteams_bridge/service.py apps/agentteams_bridge/clients.py tests/api/test_api.py tests/api/test_live_finalization.py tests/api/test_atomicity.py tests/api/test_domain.py tests/api/test_rxp_api.py tests/agentteams/test_bridge_strong_finalization.py README.md docs/postgres-recovery-runbook.md
git commit -m "feat(api): finalize live memory with trusted provenance"
```

## Plan Exit Criteria

- Synthetic and legacy data cannot auto-promote; live completion creates candidates without claiming trust.
- Closure/origin/validator predicates are independently reconstructed and enforced in PostgreSQL.
- Runtime, curator, finalizer, validator, and auditor privileges are separate, forced-RLS tenant isolation passes, and no application role owns protected objects.
- Memory lifecycle, outbox, notification, crash replay, and concurrent CAS converge deterministically.
- RXP imports are all-or-nothing, task-generation bound, policy frozen, and cannot cherry-pick favorable matrices.
- AgentTeams A/B/C/D/E satisfy one plugin contract and one context budget; A/B are no-op, C is explicitly unverified, D is closure validated, and E adds only a bounded graph.
- Fresh PostgreSQL replay reproduces closures/current views/outbox roots and every named negative case fails at its intended gate.
