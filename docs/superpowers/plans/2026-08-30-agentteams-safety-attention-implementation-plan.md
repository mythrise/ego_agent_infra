# AgentTeams Safety and Focused-Attention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the existing AgentTeams bridge with deterministic final-effect authorization, sole accepted workspace provenance, scoped AgentTeams roles, replayable attention packets, an independent internal Guardian review, and concise trace-derived user status without introducing another agent runtime.

**Architecture:** Official AgentTeams Controller/TeamHarness/Matrix/Workers remain the only collaboration runtime and stay untrusted. A Control-VM bridge issues role/task leases, validates final typed MCP arguments, runs a deterministic system classifier followed by the separate internal `EgoGuardian` rule engine when needed, and sends authorized effects to a separate Workspace VM; the evaluated source is never mounted in the AgentTeams VM. Pi-inspired focused context is built as a deterministic low-trust packet and delivered through the existing AgentTeams envelope. A trace projection renders only the current work scope and its direct children unless a double-HIGH approval or security incident requires full disclosure. Codex/Pi source is not copied or executed.

**Tech Stack:** Python 3.9 for bridge/root code, Python 3.12 for `mcp_servers`, FastAPI, Pydantic v2, official AgentTeams `main@223ddc2`, Matrix, TeamHarness, MCP, PostgreSQL/SQLite bridge stores, HMAC/Ed25519 receipts, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-30-agentteams-secure-memory-benchmark-design.md`

## Global Constraints

- Modify only `ego_agent_infra`; do not edit, import, build, or run the Pi/Codex repositories.
- Extend `apps/agentteams_bridge` and `integrations/agentteams`; do not create a parallel Pi/Codex/standalone agent adapter.
- Preserve existing `live`, `dry_run`, fixture, SQLite-development, and official-contract truth labels.
- Strong campaign mode treats Controller/TeamHarness/Matrix/Worker/reviewer/tool-result/artifact facts as `ORIGIN_UNVERIFIED` collaboration provenance.
- Only the substrate's signed Evaluator receipt can satisfy a strong Gate; AgentTeams Reviewer PASS cannot.
- The AgentTeams VM has no evaluated source disk. Native Worker shell/file tools cannot affect the accepted patch.
- The Control MCP server never executes locally and the Workspace executor never has a provider, Matrix, Controller, database, or general-network route.
- A uses the same typed effect transport in `compatibility` mode: it computes and journals `WOULD_DENY` but does not enforce the approval rule inside the disposable Workspace boundary. B-E/F use `enforcing` and alone may claim `EFFECT_AUTHORIZED/EFFECT_ENFORCED`. Canonical workspace scope/resource limits are common.
- A and B-E/F run byte-identical `SystemRiskClassifier` and `EgoGuardian` inputs and frozen rules. A records `WOULD_ALLOW`, `WOULD_REQUIRE_APPROVAL`, or `WOULD_DENY` without minting or consuming approval; B-E/F and an authorized GPU lane block every double-HIGH effect on exact-byte user approval.
- Ordinary user status is derived from admitted trace events and exposes only the selected scope plus direct children. `APPROVAL_REQUIRED` and `SECURITY_INCIDENT` override that depth rule, and every necessary specialist term is explained on first use in the message.
- Every schema change updates the canonical digest index. Never modify an applied SQL migration; add `002`.
- Strong-mode Controller/Matrix/database/approval secrets come from exact owner-only descriptors opened without symlink following, never inherited environment variables or model-visible tool arguments.
- Use exact file paths in commits; do not stage directories wholesale.

---

## File Map

Create:

```text
apps/agentteams_bridge/extensions/
  __init__.py
  contracts.py
  capability.py
  safety.py
  guardian.py
  attention.py
  user_status.py
apps/agentteams_bridge/migrations/postgres/002_campaign_safety_attention.sql
integrations/agentteams/campaign-envelope.schema.json
integrations/agentteams/safety-decision.schema.json
integrations/agentteams/attention-packet.schema.json
integrations/agentteams/guardian-decision.schema.json
integrations/agentteams/user-status-projection.schema.json
mcp_servers/src/egoagentos_mcp/workspace_contract.py
mcp_servers/src/egoagentos_mcp/workspace_server.py
mcp_servers/src/egoagentos_mcp/workspace_executor.py
tests/agentteams/test_campaign_contracts.py
tests/agentteams/test_bridge_safety.py
tests/agentteams/test_bridge_guardian.py
tests/agentteams/test_bridge_capability.py
tests/agentteams/test_bridge_attention.py
tests/agentteams/test_bridge_user_status.py
tests/agentteams/test_bridge_strong_finalization.py
tests/agentteams/test_bridge_extension_replay.py
tests/integration/test_agentteams_workspace_authorization.py
mcp_servers/tests/test_workspace_server.py
mcp_servers/tests/test_workspace_executor.py
```

Modify:

```text
apps/agentteams_bridge/models.py
apps/agentteams_bridge/service.py
apps/agentteams_bridge/clients.py
apps/agentteams_bridge/store.py
apps/agentteams_bridge/postgres_store.py
apps/agentteams_bridge/settings.py
apps/agentteams_bridge/main.py
apps/agentteams_bridge/cli.py
integrations/agentteams/message-envelope.schema.json
integrations/agentteams/result-envelope.schema.json
integrations/agentteams/blueprint.yaml
integrations/agentteams/agentteams-resources.yaml.tmpl
integrations/agentteams/render_resources.py
integrations/agentteams/benchmark_adapter.py
integrations/agentteams/README.md
mcp_servers/src/egoagentos_mcp/approval.py
mcp_servers/src/egoagentos_mcp/common.py
mcp_servers/pyproject.toml
mcp_servers/README.md
tests/agentteams/conftest.py
tests/agentteams/test_bridge_contract.py
tests/agentteams/test_bridge_live.py
tests/agentteams/test_bridge_store_selection.py
tests/agentteams/test_live_control_plane_e2e.py
tests/postgres/test_agentteams_bridge_postgres.py
Makefile
docs/contracts/secure-agent/v2/contract-digests.json
```

## Task 1: Freeze AgentTeams campaign, role-lease, safety, and attention contracts

**Files:**

- Create: `apps/agentteams_bridge/extensions/__init__.py`
- Create: `apps/agentteams_bridge/extensions/contracts.py`
- Create: `integrations/agentteams/campaign-envelope.schema.json`
- Create: `integrations/agentteams/safety-decision.schema.json`
- Create: `integrations/agentteams/attention-packet.schema.json`
- Create: `integrations/agentteams/guardian-decision.schema.json`
- Create: `integrations/agentteams/user-status-projection.schema.json`
- Modify: `apps/agentteams_bridge/models.py`
- Modify: `integrations/agentteams/message-envelope.schema.json`
- Modify: `integrations/agentteams/result-envelope.schema.json`
- Modify: `docs/contracts/secure-agent/v2/contract-digests.json`
- Create: `tests/agentteams/test_campaign_contracts.py`
- Modify: `tests/agentteams/test_bridge_contract.py`

**Interfaces:**

- Consumes: substrate `MeasuredConfigurationId`, `ExecutionPhaseOwner`, canonical JSON/domain-separated digest rules, `SignedTaskLease`, `IssuedBudgetTicket`, and manifest digest.
- Produces: `CampaignBinding`, signature-verified task-lease views, `RiskAssessment`, `GuardianDecision`, `SafetyDecision`, `UserStatusProjection`, `CapabilityObservation`, and `AttentionPacket` consumed by every later task.

- [ ] Write failing strict-model/schema tests for missing campaign/configuration/problem/turn/generation, wrong manifest/policy/requirement/checkpoint digest, stale context version, cross-project/task/worker/role lease, unknown tool, undeclared spawn, duplicate source digest, unordered attention source, approval fields inside memory context, and unknown envelope fields.

Use these exact contracts:

```python
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class TrustMode(str, Enum):
    LEGACY_LIVE = "legacy_live"
    STRONG_CAMPAIGN = "strong_campaign"


class SafetyProfile(str, Enum):
    COMPATIBILITY = "compatibility"
    ENFORCING = "enforcing"


class CampaignBinding(StrictModel):
    campaign_id: str
    configuration_id: MeasuredConfigurationId | None
    execution_phase_owner: ExecutionPhaseOwner
    problem_id: str
    turn: int = Field(ge=1, le=5)
    generation: int = Field(ge=1)
    manifest_sha256: Digest
    post_selection_extension_sha256: Digest | None
    policy_sha256: Digest
    requirement_ledger_sha256: Digest
    workspace_checkpoint_sha256: Digest
    memory_watermark: int = Field(ge=0)


class SafetyVerdict(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    PAUSE_REQUIRED = "PAUSE_REQUIRED"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RiskStage(str, Enum):
    SYSTEM = "SYSTEM"
    GUARDIAN = "GUARDIAN"


class EnforcementMode(str, Enum):
    COUNTERFACTUAL = "COUNTERFACTUAL"
    ENFORCING = "ENFORCING"


JsonValue = Union[None, bool, int, float, str, List["JsonValue"], Dict[str, "JsonValue"]]


class RiskDisposition(str, Enum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"
    WOULD_ALLOW = "WOULD_ALLOW"
    WOULD_REQUIRE_APPROVAL = "WOULD_REQUIRE_APPROVAL"
    WOULD_DENY = "WOULD_DENY"


class RiskAssessment(StrictModel):
    schema_version: Literal["risk-assessment/v1"]
    stage: RiskStage
    canonical_effect_sha256: Digest
    risk_level: RiskLevel
    rule_set_version: str
    rule_set_sha256: Digest
    reason_codes: tuple[str, ...]
    mandatory_constraint_ids: tuple[str, ...]
    assessed_sequence: int = Field(ge=0)


class GuardianDecision(StrictModel):
    schema_version: Literal["guardian-decision/v1"]
    enforcement_mode: EnforcementMode
    system_assessment: RiskAssessment
    guardian_assessment: RiskAssessment | None
    assessments_disagree: bool
    disposition: RiskDisposition
    decision_sha256: Digest


class ApprovalDisclosure(StrictModel):
    schema_version: Literal["approval-disclosure/v1"]
    effect_id: str
    canonical_effect_sha256: Digest
    operation: str
    safe_final_arguments: dict[str, JsonValue]
    target: str
    affected_scope: tuple[str, ...]
    risk_reason_codes: tuple[str, ...]
    reversibility: Literal["REVERSIBLE", "PARTIALLY_REVERSIBLE", "IRREVERSIBLE"]
    recovery_plan: str
    expires_at_sequence: int = Field(ge=0)
    choices: tuple[Literal["APPROVE"], Literal["DENY"]]


class SafetyDecision(StrictModel):
    schema_version: Literal["safety-decision/v1"]
    effect_id: str
    canonical_effect_sha256: Digest
    guardian_decision_sha256: Digest
    verdict: SafetyVerdict
    pending_effect_ref: str | None
    approval_disclosure_sha256: Digest | None
    decision_sha256: Digest


class WorkLevel(str, Enum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class UserMessageMode(str, Enum):
    PROGRESS = "PROGRESS"
    DETAIL_ON_DEMAND = "DETAIL_ON_DEMAND"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    SECURITY_INCIDENT = "SECURITY_INCIDENT"


class UserStatusProjection(StrictModel):
    schema_version: Literal["user-status-projection/v1"]
    mode: UserMessageMode
    locale: Literal["zh-CN", "en-US"]
    reporting_scope_id: str
    reporting_scope_level: WorkLevel
    direct_child_ids: tuple[str, ...]
    source_event_ids: tuple[str, ...]
    trace_root_sha256: Digest
    evidence_watermark: int = Field(ge=0)
    projection_policy_sha256: Digest
    guardian_decision_sha256: Digest | None
    safety_decision_sha256: Digest | None
    approval_disclosure_sha256: Digest | None
    status: Literal["PENDING", "IN_PROGRESS", "COMPLETED", "BLOCKED", "FAILED"]
    result_text: str
    current_state_text: str | None
    next_step_text: str | None
    approval_disclosure: ApprovalDisclosure | None
    explained_terms: tuple[tuple[str, str], ...]
    projection_sha256: Digest


class ProofLevel(str, Enum):
    DECLARED = "DECLARED"
    SPAWN_AUTHORIZED = "SPAWN_AUTHORIZED"
    TOOL_INVOKED = "TOOL_INVOKED"
    COMPATIBILITY_ACCEPTED = "COMPATIBILITY_ACCEPTED"
    EFFECT_AUTHORIZED = "EFFECT_AUTHORIZED"
    EFFECT_ENFORCED = "EFFECT_ENFORCED"
    UNPROVEN = "UNPROVEN"
```

`CampaignBinding` is a non-authoritative envelope view reconstructed from the
substrate-owned `SignedTaskLease.core`; every field must equal the corresponding
lease field, including the owner-specific initial/F/sealed/qualification/optimizer/GPU sentinel
rules. Validators require A-F for measured/sealed/GPU owners and `None` for
`QUALIFICATION`/`OPTIMIZER`; `policy_sha256` must equal the manifest's
recomputed `effect_policy_bundle_sha256`. `SignedTaskLease` and each `IssuedBudgetTicket` use
Control Ed25519 keys frozen in the manifest; broker and bridge verify
signature/issuer/key/sequence/expiry and retrieve the preloaded template before
trusting role or ticket data. A guest-provided digest is never authority.
`CollaborationEnvelope` retains its official bridge fields and adds one strict
`campaign: CampaignBinding`; its digest covers both.
`WorkerResultEnvelope.attention_packet_sha256` is optional Worker declaration
only and never proof of attention.

`RiskAssessment.reason_codes` and `mandatory_constraint_ids` are sorted and
duplicate-free. `guardian_assessment` is present if and only if the system
assessment is `HIGH`; both assessments bind the same final canonical effect.
The decision digest covers both independently versioned rule sets and the
enforcement mode. Mandatory credential exfiltration, host escape,
out-of-scope destructive write, and evidence-tampering constraints cannot be
downgraded. `UserStatusProjection.approval_disclosure` is forbidden outside
`APPROVAL_REQUIRED`, contains no approval bearer, and is required to bind the
safe-rendered final arguments, target/scope, reasons, recovery, expiry, and
literal `APPROVE`/`DENY` choices in that mode. Its Guardian, SafetyDecision,
and disclosure digests are all required in that mode, forbidden in other
modes, and must equal the three stored canonical records before the projection
is journaled or an approval can be granted.

- [ ] Run the focused tests and confirm model/schema failures before implementation.

```bash
uv run --python 3.9 --extra dev pytest -q tests/agentteams/test_campaign_contracts.py tests/agentteams/test_bridge_contract.py
```

- [ ] Implement the contracts by importing/re-exporting the substrate-generated signed lease/ticket validators, extend `EnvelopeKind` with `ROLE_LEASED`, `SYSTEM_RISK_CLASSIFIED`, `GUARDIAN_DECIDED`, `SAFETY_DECIDED`, `USER_STATUS_PROJECTED`, `USER_APPROVAL_REQUESTED`, `USER_DRILLDOWN_BOUND`, `CAPABILITY_OBSERVED`, `ATTENTION_SENT`, `ATTENTION_DECLARED`, `CITATION_BOUND`, `EFFECT_RECEIPTED`, and `SEALED_EVALUATION_BOUND`, and export deterministic schemas. Keep legacy envelopes accepted only under `LEGACY_LIVE`; strong mode requires all campaign fields.
- [ ] Update the common contract-digest generator/check from the substrate plan so the five new AgentTeams schemas and changed envelope schemas are indexed; duplicate schema implementations are forbidden.
- [ ] Run tests, schema checks, Ruff, and mypy.

```bash
uv run --python 3.9 --extra dev pytest -q tests/agentteams/test_campaign_contracts.py tests/agentteams/test_bridge_contract.py
uv run --python 3.9 --extra dev python -m benchmarks.secure_memory.manifest schema --check
uv run --python 3.9 --extra dev ruff check apps/agentteams_bridge/extensions/contracts.py apps/agentteams_bridge/models.py tests/agentteams/test_campaign_contracts.py
uv run --python 3.9 --extra dev mypy apps/agentteams_bridge/extensions/contracts.py apps/agentteams_bridge/models.py
```

- [ ] Commit.

```bash
git add apps/agentteams_bridge/extensions/__init__.py apps/agentteams_bridge/extensions/contracts.py apps/agentteams_bridge/models.py integrations/agentteams/campaign-envelope.schema.json integrations/agentteams/safety-decision.schema.json integrations/agentteams/attention-packet.schema.json integrations/agentteams/guardian-decision.schema.json integrations/agentteams/user-status-projection.schema.json integrations/agentteams/message-envelope.schema.json integrations/agentteams/result-envelope.schema.json docs/contracts/secure-agent/v2/contract-digests.json tests/agentteams/test_campaign_contracts.py tests/agentteams/test_bridge_contract.py
git commit -m "feat(agentteams): bind campaign role and attention contracts"
```

## Task 2: Persist campaign bindings and append-only extension events

**Files:**

- Create: `apps/agentteams_bridge/migrations/postgres/002_campaign_safety_attention.sql`
- Modify: `apps/agentteams_bridge/store.py`
- Modify: `apps/agentteams_bridge/postgres_store.py`
- Create: `tests/agentteams/test_bridge_extension_replay.py`
- Modify: `tests/agentteams/test_bridge_store_selection.py`
- Modify: `tests/postgres/test_agentteams_bridge_postgres.py`

**Interfaces:**

- Consumes: Task-1 contracts and existing `BridgeStoreContract` event/receipt chain.
- Produces: atomic `bind_campaign()`, `append_extension_event()`, `store_signed_task_lease()`, `bind_sealed_evaluation()`, and replay/query methods used by bridge safety/finalization.

- [ ] Write failing SQLite/PostgreSQL parity tests for campaign binding, immutable policy/manifest/checkpoint roots, exact task lease lookup, system/Guardian assessment ordering, double-HIGH decision replay, user-status projection trace/watermark binding, event hash replay, same-idempotency replay, changed-byte conflict, sealed Evaluator binding, restart recovery, cross-configuration lookup rejection, and legacy row readability.
- [ ] Add migration `002` without modifying `001`. It adds nullable legacy-compatible campaign columns to `bridge_runs`, append-only `bridge_task_leases`, and append-only `bridge_evaluator_bindings`. Strong rows enforce non-null fields through fixed write procedures/checks; no token, key, DSN, approval bearer, prompt, or raw secret column exists.

The lease/evaluator tables store canonical signed payload, signature/key/issuer, payload digest, previous stream digest, event sequence, idempotency key, and created time. Risk assessments, Guardian decisions, user projections, approval requests, and drill-down bindings use the same append-only extension-event chain; a Guardian event cannot precede its matching system-HIGH event and a projection cannot name an unadmitted event or future watermark. Signature verification and template/ticket lookup occur before insertion. Updates/deletes are revoked in PostgreSQL. SQLite enforces the same semantic immutability in its store methods.

- [ ] Implement store methods with one transaction and per-run advisory locks in PostgreSQL. Same idempotency plus same bytes returns the existing record; same idempotency plus different bytes raises `BridgeError("idempotency_conflict")`.
- [ ] Run focused tests against SQLite, then disposable PostgreSQL.

```bash
uv run --python 3.9 --extra dev pytest -q tests/agentteams/test_bridge_extension_replay.py tests/agentteams/test_bridge_store_selection.py
test -n "${EGO_TEST_POSTGRES_URL:-}"
make test-postgres
uv run --python 3.9 --extra dev pytest -q tests/postgres/test_agentteams_bridge_postgres.py
```

- [ ] Commit.

```bash
git add apps/agentteams_bridge/migrations/postgres/002_campaign_safety_attention.sql apps/agentteams_bridge/store.py apps/agentteams_bridge/postgres_store.py tests/agentteams/test_bridge_extension_replay.py tests/agentteams/test_bridge_store_selection.py tests/postgres/test_agentteams_bridge_postgres.py
git commit -m "feat(agentteams): persist campaign extension evidence"
```

## Task 3: Replace strong-mode environment secrets with scoped descriptors

**Files:**

- Modify: `apps/agentteams_bridge/settings.py`
- Modify: `apps/agentteams_bridge/clients.py`
- Modify: `apps/agentteams_bridge/main.py`
- Modify: `tests/agentteams/conftest.py`
- Create: `tests/agentteams/test_bridge_capability.py`
- Modify: `tests/agentteams/test_bridge_live.py`

**Interfaces:**

- Consumes: `TrustMode`, role-specific secret file/FD map, official AgentTeams clients.
- Produces: `BridgeSettings.from_secret_descriptors()`, paginated receipt-producing AgentTeams observation clients, and `CapabilityObservation` streams.

- [ ] Write failing tests proving `STRONG_CAMPAIGN` rejects secrets from environment variables, symlink/world-readable/wrong-owner secret files, inherited unrelated FDs, shared tokens across configurations/roles, tokens in repr/errors/receipts, missing pagination, unleased background requests, and unknown Controller endpoints. Preserve `from_env()` only for explicitly labeled legacy/dev mode.
- [ ] Implement a `SecretDescriptor` that, in strong mode, reads exactly one provisioner-opened FD, rechecks owner/mode/type/inode, returns immutable bytes once, zeroizes its mutable buffer after client construction, and never exposes the value through model dump/repr. A path constructor remains legacy/dev only. Strong settings require separate Controller auth, Matrix access, database runtime, and receipt-signing descriptors. Adapter/service users are isolated from Worker UIDs with `hidepid`, no ptrace/signals, closed FDs, dropped capabilities, immutable binaries, and exact socket ACLs; canary Workers cannot read service environment/memory/descriptors/keys.
- [ ] Extend `AgentTeamsClient.spawns_with_receipt()` and `spawn_messages_pages_with_receipts()` using only endpoints pinned in `official-contract.lock.json`. Admission-scan each observed page before the trusted journal. Preserve the digest/cursor/`has_more` only for admitted bytes; on credential/PII rejection store source class/reason/count and no page/body digest. Do not invent a pre-execution AgentTeams hook endpoint. Official Matrix's disposable internal persistence remains explicitly untrusted.
- [ ] Map evidence without overclaiming:

```text
Worker.skills -> DECLARED
SpawnRecord.subagent_allowed_tools/subagent_skills -> SPAWN_AUTHORIZED
successful spawn tool_result -> TOOL_INVOKED
Control authorization receipt -> EFFECT_AUTHORIZED
Control enforcing authorization + stopped Workspace + Evaluator agreement -> EFFECT_ENFORCED (B-E/F only)
A audit + stopped Workspace + Evaluator agreement -> COMPATIBILITY_ACCEPTED
```

- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/agentteams/test_bridge_capability.py tests/agentteams/test_bridge_live.py
uv run --python 3.9 --extra dev ruff check apps/agentteams_bridge/settings.py apps/agentteams_bridge/clients.py tests/agentteams/test_bridge_capability.py
uv run --python 3.9 --extra dev mypy apps/agentteams_bridge/settings.py apps/agentteams_bridge/clients.py
```

- [ ] Commit.

```bash
git add apps/agentteams_bridge/settings.py apps/agentteams_bridge/clients.py apps/agentteams_bridge/main.py tests/agentteams/conftest.py tests/agentteams/test_bridge_capability.py tests/agentteams/test_bridge_live.py
git commit -m "feat(agentteams): scope bridge secrets and observations"
```

## Task 4: Build the typed Workspace MCP gateway and separate executor

**Files:**

- Create: `mcp_servers/src/egoagentos_mcp/workspace_contract.py`
- Create: `mcp_servers/src/egoagentos_mcp/workspace_server.py`
- Create: `mcp_servers/src/egoagentos_mcp/workspace_executor.py`
- Modify: `mcp_servers/src/egoagentos_mcp/approval.py`
- Modify: `mcp_servers/src/egoagentos_mcp/common.py`
- Modify: `mcp_servers/pyproject.toml`
- Modify: `mcp_servers/README.md`
- Create: `mcp_servers/tests/test_workspace_server.py`
- Create: `mcp_servers/tests/test_workspace_executor.py`
- Create: `tests/integration/test_agentteams_workspace_authorization.py`

**Interfaces:**

- Consumes: substrate authenticated `WORKSPACE_EFFECT` channel, signature-verified `SignedTaskLease`, `SafetyProfile`, existing single-use approval records.
- Produces: five MCP tools, `authorize_effect(FinalEffectRequest) -> SafetyDecision`, and a no-network Workspace executor receipt.

- [ ] Write failing contract tests for the only accepted operations:

```python
JsonValue = Union[None, bool, int, float, str, List["JsonValue"], Dict[str, "JsonValue"]]


WorkspaceOperation = Literal[
    "workspace.list",
    "workspace.read",
    "workspace.search",
    "workspace.apply_patch",
    "workspace.run_allowlisted_test",
]


class FinalEffectRequest(StrictModel):
    signed_lease_sha256: Digest
    operation: WorkspaceOperation
    workspace_id: str
    relative_path: Optional[str]
    arguments: dict[str, JsonValue]
    expected_before_tree_sha256: Digest
    effect_id: str
    policy_sha256: Digest
    sequence: int
    idempotency_key: str
    pending_approval_ref: Optional[str]
```

Test absolute/parent/Unicode/symlink/hardlink escape, final-argument mutation, wrong expected-before root, invalid/offset/fuzzy patch, binary/device path, shell metacharacters, arbitrary command/env/cwd, unallowlisted test target/flag, timeout/output/process/disk limit, stale/cross-role or forged lease, wrong policy/config, missing/expired/replayed approval record, any approval bearer in Worker-visible bytes, same-sequence changed bytes, local-execution fallback, source mount in AgentTeams VM, and accepted patch not sourced from Workspace overlay. Inject crashes before write, after staged write/before receipt, and after receipt; restart must prove exactly one persistent before-to-after transition.

- [ ] Implement canonical path traversal using descriptor-relative opens and `O_NOFOLLOW`; re-check inode/type immediately before use. `workspace.apply_patch` accepts a bounded canonical unified patch, applies it with zero fuzz to a COW staging root, fsyncs intent and before/after digests, and atomically publishes tree plus durable receipt. On restart, reconcile an in-doubt intent by exact before/after roots; never blindly reapply. `workspace.run_allowlisted_test` clones the persistent tree into a throwaway COW root, maps a manifest test ID to a frozen argv tuple, and uses `shell=False`, fixed snapshot cwd, empty/minimal environment, separate unprivileged UID/PID/mount namespace, closed FDs, no ptrace/signals/service sockets, process group, timeout, output cap, CPU/RSS/process/disk limits, and no network. Destroy the snapshot and assert the persistent tree digest is unchanged; only `apply_patch` may mutate it.
- [ ] Extend approval claims to bind campaign/configuration/project/task/worker/operation/workspace/expected-before tree/effect ID/canonical final arguments/cwd/policy digest/manifest digest/owner-dependent post-selection extension digest/expiry/single-use nonce. Issuance and bearer storage remain Control-only and are never an MCP tool or Worker-visible field. The Worker sends only a non-secret pending-effect reference; Control atomically resolves/consumes the matching record after recomputing all bytes. PostgreSQL/Control replay state is authoritative in strong mode; file/in-memory stores remain tests/dev only.
- [ ] Implement the Control-side MCP server as authorize-then-forward only; it has no source mount and no local operation implementation. Implement the Workspace executor as receive-validate-execute-receipt only; it has no AgentTeams/model/database clients. Admission-scan Workspace output and patch bytes before receipt/export; secret rejection stores metadata only. Execution output is untrusted until sealed evaluation.
- [ ] Add `ego-workspace-mcp = "egoagentos_mcp.workspace_server:main"` and `ego-workspace-executor = "egoagentos_mcp.workspace_executor:main"` entry points. Document the two-process/VM boundary.
- [ ] Run focused and integration tests.

```bash
uv run --python 3.12 --project mcp_servers --extra dev pytest -q tests/test_workspace_server.py tests/test_workspace_executor.py
uv run --python 3.12 --project mcp_servers --extra dev ruff check src/egoagentos_mcp/workspace_contract.py src/egoagentos_mcp/workspace_server.py src/egoagentos_mcp/workspace_executor.py tests/test_workspace_server.py tests/test_workspace_executor.py
uv run --python 3.12 --project mcp_servers --extra dev --with mypy mypy src/egoagentos_mcp/workspace_contract.py src/egoagentos_mcp/workspace_server.py src/egoagentos_mcp/workspace_executor.py
uv run --python 3.9 --extra dev pytest -q tests/integration/test_agentteams_workspace_authorization.py
```

- [ ] Commit.

```bash
git add mcp_servers/src/egoagentos_mcp/workspace_contract.py mcp_servers/src/egoagentos_mcp/workspace_server.py mcp_servers/src/egoagentos_mcp/workspace_executor.py mcp_servers/src/egoagentos_mcp/approval.py mcp_servers/src/egoagentos_mcp/common.py mcp_servers/pyproject.toml mcp_servers/README.md mcp_servers/tests/test_workspace_server.py mcp_servers/tests/test_workspace_executor.py tests/integration/test_agentteams_workspace_authorization.py
git commit -m "feat(mcp): authorize typed AgentTeams workspace effects"
```

## Task 5: Enforce AgentTeams scope, R2 binding, and strong finalization

**Files:**

- Create: `apps/agentteams_bridge/extensions/capability.py`
- Create: `apps/agentteams_bridge/extensions/safety.py`
- Create: `apps/agentteams_bridge/extensions/guardian.py`
- Modify: `apps/agentteams_bridge/service.py`
- Modify: `apps/agentteams_bridge/clients.py`
- Modify: `apps/agentteams_bridge/main.py`
- Modify: `apps/agentteams_bridge/cli.py`
- Create: `tests/agentteams/test_bridge_safety.py`
- Create: `tests/agentteams/test_bridge_guardian.py`
- Create: `tests/agentteams/test_bridge_strong_finalization.py`
- Modify: `tests/agentteams/test_bridge_live.py`
- Modify: `tests/agentteams/test_live_control_plane_e2e.py`

**Interfaces:**

- Consumes: Tasks 1-4 contracts/store/capability observations/workspace decisions plus substrate signed Evaluator binding.
- Produces: strong `start_run/reconcile/grant_r2/finalize`, byte-replayable `SystemRiskClassifier -> EgoGuardian` decisions, fail-closed Controller pause, and acceptance inputs that distinguish collaboration from trust.

- [ ] Write failing service tests for undeclared Worker/role/stage/skill/tool/spawn, extra model request, task DAG replacement, stale context/policy/checkpoint, R2 token bound to earlier arguments, Matrix text pretending approval, Worker evaluator/reviewer PASS without sealed result, forged/cross-arm Evaluator binding, finalization replay, post-grant compensation, and pause failure recovery. In `test_bridge_guardian.py`, add table-driven cases for LOW/MEDIUM, system-HIGH plus Guardian downgrade, double-HIGH, mandatory no-downgrade constraints forcing system HIGH, missing/inconsistent mandatory rules failing closed, different effect bytes, disagreement journaling, replay byte identity, and A-versus-B identical-assessment parity.
- [ ] Implement `SystemRiskClassifier.assess(effect, rules) -> RiskAssessment` as a pure rule-table evaluation over the final canonical arguments, expected-before tree, target boundary, data sensitivity, reversibility, network/process scope, policy digest, and approval state. Invoke `EgoGuardian.assess(effect, separate_rules) -> RiskAssessment` only after system `HIGH`. `EgoGuardian` is an internal deterministic Control component inspired by Codex Guardian; it does not execute, embed, import, or call Codex and it cannot run an AgentTeams/model task.
- [ ] Freeze separate rule-set digests and a shared mandatory constraint registry for credential exfiltration, host-boundary escape, destructive out-of-scope writes, and evidence tampering. A mandatory match forces the system assessment to `HIGH`, therefore always invokes Guardian, and also forces Guardian `HIGH`; unavailable, missing, or inconsistent registry/rule bytes fail closed as `HIGH/DENY`. Any attempted downgrade fails closed. Journal both assessments, reason codes, rule versions, disagreement, and final disposition before authorization. Same canonical effect plus frozen rules must produce byte-identical assessments after restart.
- [ ] Apply enforcement after assessment without changing assessment bytes: A records `WOULD_ALLOW`, `WOULD_REQUIRE_APPROVAL`, or `WOULD_DENY`, never mints/consumes approval, and may continue only inside its disposable compatibility boundary; B-E/F and an authorized GPU lane record `ALLOW`, `REQUIRE_APPROVAL`, or `DENY`. A double-HIGH enforcing effect pauses before Workspace forwarding and remains blocked until the exact Control approval is consumed.
- [ ] Implement pure deterministic scope evaluation before project create/replan and after every reconcile observation. A deviation writes `SAFETY_DECIDED`, attempts official Controller pause, and enters `BLOCKED` or `COMPENSATION_REQUIRED`. A post-hoc `TOOL_INVOKED` observation is never described as a pre-execution block; accepted source safety comes from Task 4's sole Workspace path. In A, exact policy evaluation records `WOULD_DENY`/`COMPATIBILITY_ACCEPTED` without consuming approval and cannot claim enforcement; B-E/F fail closed and may produce enforcing proof.
- [ ] Extend `StartRunRequest`/`BridgeRun` with `trust_mode`, `safety_profile`, and `CampaignBinding`. `STRONG_CAMPAIGN` validates a frozen allowed role/task/tool/ticket graph and stores its digest before the official project is created. Legacy live behavior remains available but is excluded from the campaign.
- [ ] Bind `grant_r2` to current campaign, policy, workspace checkpoint, owner-dependent post-selection extension, exact pending effect digest, signer/fixture identity, expiry, and idempotency. Issuance is reachable only from a stored double-HIGH enforcing Guardian decision and its bound `APPROVAL_REQUIRED` projection. If any argument, target, affected scope, expected-before tree, policy, manifest, post-selection extension, expiry, or effect digest changes after pause, consume nothing, invalidate the projection, keep Controller paused, and require a new assessment/message/approval. Simulated fixtures remain labeled `SIMULATED_APPROVAL`; chat is never a grant.
- [ ] Split `_build_finalization_evidence()` and `_finalize_ego()` by trust mode. In strong mode:

```text
AgentTeams artifact/result/reviewer/skill/tool records -> ORIGIN_UNVERIFIED collaboration evidence
verified SignedEvaluatorResult + stopped workspace binding -> trusted evaluator evidence
trusted Control finalizer -> Gate, Decision, DecisionClosure
```

Strong finalization refuses to run without an exact stored Evaluator binding for current seed/patch/tree/suite/image/policy/generation/checkpoint. `WorkerResultEnvelope.independent_review` never satisfies a Gate.
- [ ] Add `StrongFinalizerClient.finalize_signed_evaluation(binding_ref, checkpoint_ref)` in `clients.py` over the authenticated internal Control channel. In `STRONG_CAMPAIGN`, `_finalize_ego()` sends only those stored identities; the API/DB finalizer re-reads the signed bytes and invokes `egoagentos_finalize_decision`. Prove `EgoClient.finalize_live()` and public `/api/v1/tasks/{id}/finalize` are unreachable in strong mode, even when Worker/reviewer evidence says PASS. Keep them explicitly legacy-only. The memory plan Task 13 implements the receiving DB finalizer; these protocol tests use a strict fake.
- [ ] Add read-only endpoints/CLI output for capability observations, safety decisions, attention packets, evaluator binding, and proof levels. Do not add an HTTP endpoint that mints approvals or claims AgentTeams has a nonexistent pre-tool hook.
- [ ] Run all AgentTeams tests and static checks.

```bash
uv run --python 3.9 --extra dev pytest -q tests/agentteams/test_bridge_safety.py tests/agentteams/test_bridge_guardian.py tests/agentteams/test_bridge_strong_finalization.py tests/agentteams/test_bridge_live.py tests/agentteams/test_live_control_plane_e2e.py
make test-agentteams
make check-agentteams
```

- [ ] Commit.

```bash
git add apps/agentteams_bridge/extensions/capability.py apps/agentteams_bridge/extensions/safety.py apps/agentteams_bridge/extensions/guardian.py apps/agentteams_bridge/service.py apps/agentteams_bridge/clients.py apps/agentteams_bridge/main.py apps/agentteams_bridge/cli.py tests/agentteams/test_bridge_safety.py tests/agentteams/test_bridge_guardian.py tests/agentteams/test_bridge_strong_finalization.py tests/agentteams/test_bridge_live.py tests/agentteams/test_live_control_plane_e2e.py
git commit -m "feat(agentteams): enforce campaign scope and sealed finalization"
```

## Task 6: Build deterministic attention packets and usage evidence

**Files:**

- Create: `apps/agentteams_bridge/extensions/attention.py`
- Modify: `apps/agentteams_bridge/service.py`
- Create: `tests/agentteams/test_bridge_attention.py`
- Modify: `tests/agentteams/test_bridge_live.py`

**Interfaces:**

- Consumes: `CampaignBinding`, accepted requirement/task state, trusted memory retrieval references, prior failure/checkpoint records, context/token budget.
- Produces: `build_attention_packet(sources, budget) -> AttentionPacket` and delivery/declaration/usage proof-level events.

- [ ] Write failing deterministic tests for trust/relevance/recency/digest ordering, duplicate source collapse, expired/superseded exclusion, current requirement preservation, prior failure preservation, fixed byte/token cap, exact-boundary behavior, injection text, secret scan failure, prompt instruction inside memory, stale context version, cross-tenant source, and repeated-build byte identity.
- [ ] Implement source tiers and stable ranking:

```python
TRUST_ORDER = {
    "CURRENT_REQUIREMENT": 0,
    "SIGNED_POLICY_REF": 1,
    "LOCAL_TRUSTED_FACT": 2,
    "ATTESTED_EXTERNAL_FACT": 3,
    "PRIOR_FAILURE": 4,
    "UNVERIFIED_SUMMARY": 5,
    "UNTRUSTED_CONTEXT": 6,
}

sort_key = (trust_order, -stage_relevance, -source_sequence, source_sha256)
```

Render external/Worker/Matrix/memory text under an explicit quoted `UNTRUSTED_CONTEXT` delimiter. The packet contains IDs/digests/trust/applicability/conflicts/reason codes, never approval token, capability, executable policy, or raw secret. If mandatory sources plus wrapper exceed the request class, return `BUDGET_LIMITED`; never drop the active requirement silently.
- [ ] Deliver the typed packet in `_task_request_body()` and bind its digest to task lease/envelope/result declaration. Record `ATTENTION_SENT` on admitted Matrix receipt, `ATTENTION_DECLARED` only when a Worker result repeats the digest, and `CITATION_BOUND` only when a trace binds exact cited packet/revision IDs to a plan/effect/test rationale. This is a cited-use proxy, not proof of attention or causality; actual attention stays `UNPROVEN` absent a separately preregistered intervention.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/agentteams/test_bridge_attention.py tests/agentteams/test_bridge_live.py
uv run --python 3.9 --extra dev ruff check apps/agentteams_bridge/extensions/attention.py tests/agentteams/test_bridge_attention.py
uv run --python 3.9 --extra dev mypy apps/agentteams_bridge/extensions/attention.py
```

- [ ] Commit.

```bash
git add apps/agentteams_bridge/extensions/attention.py apps/agentteams_bridge/service.py tests/agentteams/test_bridge_attention.py tests/agentteams/test_bridge_live.py
git commit -m "feat(agentteams): deliver focused attention packets"
```

## Task 7: Project concise user status and override it for risk

**Files:**

- Create: `apps/agentteams_bridge/extensions/user_status.py`
- Modify: `apps/agentteams_bridge/service.py`
- Modify: `apps/agentteams_bridge/main.py`
- Modify: `apps/agentteams_bridge/cli.py`
- Create: `tests/agentteams/test_bridge_user_status.py`
- Modify: `tests/agentteams/test_bridge_live.py`

**Interfaces:**

- Consumes: Task-1 `UserStatusProjection`, Tasks 2/5 append-only trace and Guardian decisions, Task-6 attention evidence, a frozen L0-L3 work hierarchy, and a versioned bilingual term glossary.
- Produces: `project_user_status(trace, reporting_scope_id, requested_mode, locale) -> UserStatusProjection`, a read-only status endpoint/CLI view, and projection measurements consumed by the campaign.

- [ ] Write failing table-driven tests in which each case first names the production break it catches: a grandchild/file/command/SQL/stack-trace leak; an omitted direct child; completion from Worker self-report instead of acceptance evidence; a future or rejected event; hidden failed gate/blocker/decision; drill-down by zero or two levels; wrong message order; full identifiers in normal progress; an unexplained glossary term/acronym; approval detail outside risk mode; missing double-HIGH detail; substituted Guardian/Safety/disclosure digest or mismatched reason/expiry; unsafe secret-bearing argument rendering; stale approval projection after any bound field changes; and a security incident suppressed by depth.
- [ ] Define a frozen hierarchy with `parent_id`, `WorkLevel`, canonical label, status-evidence predicate, and deterministic sibling order. `PROGRESS` renders the selected scope outcome plus direct children only. `DETAIL_ON_DEMAND` accepts exactly one direct-child scope and then applies the same rule. It never includes descendants, file-by-file edits, commands, SQL, stack traces, or raw diagnostics. `COMPLETED` requires the declared acceptance predicate to pass from admitted evidence; AgentTeams/model declarations never suffice.
- [ ] Implement fixed template order `result -> current state -> next step -> approval`, omitting empty sections. Normal modes abbreviate identifiers and digests; exact values appear only on explicit drill-down/evidence request or where they bind an approval. The renderer accepts no free-form model prose. Its versioned `zh-CN`/`en-US` glossary maps every allowed specialist term to a plain-language explanation and renders the first appearance as `term (plain-language meaning)`; an unknown declared term or unexplained acronym fails projection instead of leaking jargon.
- [ ] Build `APPROVAL_REQUIRED` only from a stored double-HIGH enforcing Guardian decision and its exact stored `SafetyDecision`/`ApprovalDisclosure`. Bind all three canonical digests explicitly in the projection and verify their cross-references before journaling or `grant_r2`. It overrides depth and includes safe-rendered exact operation/final arguments, target and affected scope, reason codes with plain-language explanations, reversibility, recovery plan, expiry, and literal approve/deny choices, but no credential, approval bearer, hidden path, or rejected byte. Any digest substitution or change to operation/arguments/target/scope/tree/policy/manifest/post-selection extension/expiry/reasons invalidates the projection and requires a new risk chain. A records the same counterfactual decision but stays in normal projection and never asks for an enforceable approval.
- [ ] Build `SECURITY_INCIDENT` from an admitted incident event and immediately override depth with the affected boundary, user impact, containment, recovery, and required decision. Never suppress a failed gate, uncertainty, incident, or required decision to satisfy concision.
- [ ] Bind every projection to exact source event IDs, trace root, evidence watermark, direct-child IDs, and projection-policy digest; append `USER_STATUS_PROJECTED`, `USER_APPROVAL_REQUESTED`, or `USER_DRILLDOWN_BOUND` before returning it. Rebuilding at the same watermark is byte-identical. Add only read-only `GET /runs/{run_id}/status?scope_id=...&mode=...&locale=...` and matching CLI output; no status endpoint mints approval or mutates execution.
- [ ] Emit projection measurements: visible UTF-8 bytes, deterministic estimated tokens, full-trace/visible ratio, direct-child coverage, forbidden-grandchild leakage, unexplained-term count, drill-down count, risk-override count/latency, and suppressed-required-decision count. Any nonzero leakage, unexplained term, or suppressed decision fails the strong gate.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/agentteams/test_bridge_user_status.py tests/agentteams/test_bridge_guardian.py tests/agentteams/test_bridge_live.py
uv run --python 3.9 --extra dev ruff check apps/agentteams_bridge/extensions/user_status.py tests/agentteams/test_bridge_user_status.py
uv run --python 3.9 --extra dev mypy apps/agentteams_bridge/extensions/user_status.py
```

- [ ] Commit.

```bash
git add apps/agentteams_bridge/extensions/user_status.py apps/agentteams_bridge/service.py apps/agentteams_bridge/main.py apps/agentteams_bridge/cli.py tests/agentteams/test_bridge_user_status.py tests/agentteams/test_bridge_live.py
git commit -m "feat(agentteams): project concise risk-aware user status"
```

## Task 8: Render the complete AgentTeams-only role topology

**Files:**

- Modify: `integrations/agentteams/agentteams-resources.yaml.tmpl`
- Modify: `integrations/agentteams/render_resources.py`
- Modify: `integrations/agentteams/blueprint.yaml`
- Modify: `integrations/agentteams/benchmark_adapter.py`
- Modify: `integrations/agentteams/README.md`
- Modify: `apps/agentteams_bridge/service.py`
- Modify: `tests/agentteams/test_bridge_capability.py`
- Modify: `tests/agentteams/test_bridge_contract.py`

**Interfaces:**

- Consumes: campaign roles, typed workspace MCP endpoint, conditional memory profiles, official CRD contract.
- Produces: same frozen AgentTeams topology for A-E/F, conditional task DAGs, separate blind-review/optimizer projects, and benchmark adapter with no alternate runtime.

- [ ] Extend contract tests to assert only official `agentteams.io/v1beta1` fields/endpoints appear; all A-E/F resources share the same image/model/base Worker topology; only manifest profile changes task creation/policy; no Pi/Codex runtime or adapter exists; and unused Workers make no provider call.
- [ ] Add declared Workers `ego-memory-summarizer`, `ego-memory-extractor`, `ego-memory-navigator`, `ego-memory-critic`, `ego-blind-reviewer`, and `ego-memory-optimizer`. Keep Curator existing. Only `ego-runtime` receives the `ego-workspace` MCP endpoint. Scout receives read-only repo context; Reviewer and memory roles receive no write/database/memory MCP. Control passes bounded retrieval/candidate inputs in the leased task body and admission-validates the declared output artifact over the existing bridge channel.
- [ ] Add only the non-secret `${WORKSPACE_MCP_URL}` placeholder to `render_resources.py::ALLOWED`; validate scheme/host/path and reject credentials/query/fragment/newline. Do not add a nonexistent memory MCP, unsupported CRD fields, or claim that a declaration proves enforcement.
- [ ] Replace fixed `ROLE_PLAN` with a frozen base DAG plus profile-conditional AgentTeams tasks:

```text
A/B: base collaboration DAG; no memory model task
C: Summarizer after every turn
D: Extractor after terminal Decision only
E: Navigator before every turn; Extractor -> Curator -> Critic after terminal Decision
blind review: fresh isolated review Team/Project/room
optimizer: fresh isolated optimizer Team/Project/room
```

Each model task receives a lease/ticket; no controller-side direct LLM call exists. At problem boundary create a fresh Project/Worker session/Matrix room and preserve only source checkpoint plus Control memory.
- [ ] Make `integrations.agentteams.benchmark_adapter.run_scenario()` accept only an AgentTeams configuration profile and require `STRONG_CAMPAIGN`. Remove any concept of alternate runtime probing. A PASS requires official workflow, at least three distinct Workers, admitted Matrix receipts, scoped tools/spawns, valid event chains, terminal Decision, and scenario-specific effect/recovery proof. A must show `COMPATIBILITY_ACCEPTED` plus complete Workspace/Evaluator provenance; B-E/F additionally require enforcing authorization. For the same canonical effect and frozen risk rules, A and B must have byte-identical system/Guardian assessments: A records counterfactual disposition without approval, while B-E/F block on double-HIGH and require exact approval. No profile may turn AgentTeams self-report into trust.
- [ ] Document the truth matrix: dispatch gate, Workspace authorization, post-hoc AgentTeams observation, Matrix delivery, Worker declaration, and sealed evaluation are different facts.
- [ ] Run official-contract/resource/adapter tests.

```bash
python integrations/agentteams/scripts/verify_official_contract.py --offline
make test-agentteams
make check-agentteams
```

- [ ] Commit.

```bash
git add integrations/agentteams/agentteams-resources.yaml.tmpl integrations/agentteams/render_resources.py integrations/agentteams/blueprint.yaml integrations/agentteams/benchmark_adapter.py integrations/agentteams/README.md apps/agentteams_bridge/service.py tests/agentteams/test_bridge_capability.py tests/agentteams/test_bridge_contract.py
git commit -m "feat(agentteams): render benchmark role topology"
```

## Task 9: Prove replay, recovery, and end-to-end strong-mode boundaries

**Files:**

- Modify: `tests/agentteams/test_live_control_plane_e2e.py`
- Modify: `tests/postgres/test_agentteams_bridge_postgres.py`
- Modify: `tests/agentteams/test_bridge_extension_replay.py`
- Modify: `Makefile`

**Interfaces:**

- Consumes: all prior tasks plus fake signed Evaluator and fake Workspace transport.
- Produces: one offline strong-mode acceptance trace and Make gate consumed by the campaign plan.

- [ ] Write one contract-only E2E that starts a strong run, creates official-shape project/workflow/Matrix receipts, leases roles/tickets, sends an attention packet, emits a direct-child-only progress projection, classifies a final effect HIGH twice, emits the complete risk override, pauses at R2, rejects a changed final effect/projection, grants the exact simulated approval, authorizes one Workspace patch/test, observes AgentTeams completion, refuses Worker-only finalization, binds a fake valid sealed Evaluator receipt, finalizes once, projects evidence-backed completion, replays after restart, and verifies every hash chain.
- [ ] Add negative branches for cross-configuration room/token/project, forged/expired lease or ticket, undeclared spawn/tool, native Worker artifact submitted as patch, stale attention/checkpoint, Controller pause failure/compensation, replay after crash, forged Evaluator, approval bearer in Worker bytes, secret in Matrix/tool/provider/patch/artifact/review output, test-run persistent-tree mutation, grandchild leakage, unexplained terms, suppressed failures/decisions, mandatory-risk downgrade, A claiming approval enforcement, and stale exact-effect approval. Assert secret rejection has no raw digest. Explicitly label fixtures `CONTRACT_ONLY/NO_LIVE_AGENTTEAMS/NO_PROVIDER_CALLS`.
- [ ] Add `.PHONY` target `test-agentteams-strong-offline` running exact files plus schema/contract checks; add it to the overall offline secure-memory gate, not the paid/live target.
- [ ] Run complete regression and static checks.

```bash
make test-agentteams-strong-offline
make test-agentteams
make check-agentteams
test -n "${EGO_TEST_POSTGRES_URL:-}"
make test-postgres
uv run --python 3.9 --extra dev ruff check apps/agentteams_bridge integrations/agentteams
uv run --python 3.9 --extra dev mypy apps/agentteams_bridge
uv run --python 3.12 --project mcp_servers --extra dev ruff check src/egoagentos_mcp tests
uv run --python 3.12 --project mcp_servers --extra dev --with mypy mypy src/egoagentos_mcp
```

- [ ] Commit.

```bash
git add Makefile tests/agentteams/test_live_control_plane_e2e.py tests/postgres/test_agentteams_bridge_postgres.py tests/agentteams/test_bridge_extension_replay.py
git commit -m "test(agentteams): prove strong campaign boundary"
```

## Plan Exit Criteria

- No Pi/Codex code or runtime path was added or modified.
- Every model-driven role is an AgentTeams Worker task with a Matrix/TeamHarness/ticket trace.
- The AgentTeams VM cannot change the evaluated source except through the final-argument-authorized Workspace path.
- A-E/F share one typed tool surface; A is compatibility/audit-only and B-E/F are deterministic enforcing.
- Strong finalization cannot use AgentTeams self-report as trusted Gate evidence.
- Attention packets are deterministic, budgeted, lower trust, and measured at delivery/declaration/use as separate proof levels.
- System and internal Guardian assessments are independently versioned, deterministic, replayable, and identical between A counterfactual and B-E/F enforcing inputs; mandatory high-risk constraints cannot be downgraded.
- Ordinary user status contains only its scope and direct children, explains every necessary specialist term, and never hides a failure or decision; double-HIGH approvals and security incidents disclose the complete required safety context.
- Existing legacy/live and fixture truth labels remain correct.
- All focused, official-contract, PostgreSQL, replay, and strong offline E2E gates pass before real VM/provider work.
