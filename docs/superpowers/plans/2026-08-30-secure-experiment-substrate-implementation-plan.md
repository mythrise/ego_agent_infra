# Secure Experiment Substrate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the trusted, mock-testable substrate that isolates every AgentTeams configuration, authenticates all narrow channels, enforces non-transferable provider tickets, verifies sealed evaluator results, and freezes secret-free evidence before any paid request is allowed.

**Architecture:** A new `benchmarks.secure_memory` package owns canonical contracts and host-side orchestration. Its `substrate` subpackage launches independent AgentTeams, Workspace, Control, untrusted Candidate Runner, and sealed Evaluator VMs, communicates over direction-specific authenticated virtio channels, brokers only Control-signed task-leased AgentTeams model calls under hard reservations, and admits bounded stopped-VM artifacts through one shared scanner gate. Candidate bytes never execute beside hidden suites or the signing key. Existing `apps/agentteams_bridge`, `integrations/agentteams`, and `rxp-bench/v1` remain the integration foundations rather than being replaced.

**Tech Stack:** Python 3.9, Pydantic v2, httpx, psycopg 3, QEMU/HVF, macOS Seatbelt, HMAC-SHA256, Ed25519 via `cryptography`, canonical JSON, pytest, Ruff, mypy.

**Spec:** [`docs/superpowers/specs/2026-08-30-agentteams-secure-memory-benchmark-design.md`](../specs/2026-08-30-agentteams-secure-memory-benchmark-design.md)

## Global Constraints

- Work only in an isolated worktree of `/Users/aoisora/Desktop/个人文件/比赛/GOAI/ego_agent_infra`; do not edit, build, launch, or benchmark `codex` or `pi`.
- Do not read `key.txt`, launch QEMU, call the network, or build guest images while implementing Tasks 1-7. Those tasks use fake keys, byte streams, fake clocks, fake transports, and temporary files.
- Keep the existing `benchmarks/model.py`, `benchmarks/runner.py`, and `rxp-bench` CLI backward compatible.
- Use strict Pydantic models (`extra="forbid"`, strict validation), duplicate-key-rejecting JSON parsing, UTF-8 canonical JSON, lowercase SHA-256, and domain-separated digests.
- Use one host monotonic clock injected through a `Clock` protocol. Persist UTC only as display metadata; ordering and durations use controller sequence plus monotonic nanoseconds.
- Never log an authorization header, API key, database DSN, HMAC key, evaluator private key, rejected raw body, source excerpt that failed scanning, or provider response that failed scanning.
- The controller process cannot open `key.txt`. The broker process cannot open source worktrees. Tests must assert those allowlists before process launch.
- Every state mutation must append a canonical event first or atomically with the durable state it describes. Recovery replays events and idempotency receipts, never a mutable log summary.
- Bind runtime manifests to official AgentTeams `main@223ddc2b8073e4c8b93bcbb15e1d717f196c04d9`; Pi/Codex commits are non-executable design-reference digests only.
- PostgreSQL tests in this plan, if any, must target a disposable database; `tests/postgres/conftest.py` destroys and recreates `public`.

---

## File Map

Create:

```text
benchmarks/secure_memory/
  __init__.py
  canonical.py
  models.py
  manifest.py
  schemas/
    run-manifest-v2.schema.json
    channel-envelope-v2.schema.json
    model-request-v1.schema.json
    model-response-v1.schema.json
    ticket-template-v1.schema.json
    issued-budget-ticket-v1.schema.json
    signed-task-lease-v1.schema.json
    candidate-proposal-v1.schema.json
    trusted-fact-v1.schema.json
    trusted-relation-v1.schema.json
    checkpoint-v1.schema.json
    campaign-event-v1.schema.json
    evaluator-result-envelope-v1.schema.json
    sealed-requirement-release-v1.schema.json
  substrate/
    __init__.py
    clock.py
    channel.py
    candidate_rpc.py
    budget.py
    broker.py
    scanner.py
    admission.py
    artifact_ingest.py
    evaluator_channel.py
    runner_channel.py
    qemu.py
    seatbelt.py
    image_builder.py
    journal.py
    controller.py
    preflight.py
  cli.py
  broker_launcher.py
docs/contracts/secure-agent/v2/
  README.md
  contract-digests.json
deploy/secure_memory/
  README.md
  seatbelt/
    controller.sb.tmpl
    broker.sb.tmpl
    qemu.sb.tmpl
  guest/
    agentteams-vm-manifest.json
    workspace-vm-manifest.json
    control-vm-manifest.json
    candidate-runner-vm-manifest.json
    evaluator-vm-manifest.json
tests/secure_memory/
  test_canonical_and_manifest.py
  test_channel.py
  test_candidate_rpc.py
  test_budget_ledger.py
  test_broker.py
  test_scanner.py
  test_admission_gate.py
  test_artifact_ingest.py
  test_evaluator_channel.py
  test_candidate_runner_isolation.py
  test_qemu_and_seatbelt.py
  test_controller_recovery.py
  test_substrate_preflight.py
```

Modify:

```text
pyproject.toml
Makefile
.gitignore
```

## Task 1: Define canonical models, schemas, and the RunManifest freezer

**Files:**

- Create: `benchmarks/secure_memory/__init__.py`
- Create: `benchmarks/secure_memory/canonical.py`
- Create: `benchmarks/secure_memory/models.py`
- Create: `benchmarks/secure_memory/manifest.py`
- Create: `benchmarks/secure_memory/schemas/run-manifest-v2.schema.json`
- Create: `benchmarks/secure_memory/schemas/model-request-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/model-response-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/ticket-template-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/issued-budget-ticket-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/signed-task-lease-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/candidate-proposal-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/trusted-fact-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/trusted-relation-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/checkpoint-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/campaign-event-v1.schema.json`
- Create: `docs/contracts/secure-agent/v2/README.md`
- Create: `docs/contracts/secure-agent/v2/contract-digests.json`
- Create: `tests/secure_memory/test_canonical_and_manifest.py`
- Modify: `pyproject.toml`

- [ ] Write tests that reject duplicate JSON keys, NaN/Infinity, unknown fields, unsorted evidence digests, any initial arm tuple other than exactly A-E, a non-HTTPS provider URL, a model other than `agnes-2.5-pro`, an AgentTeams commit/resource mismatch, a runtime named Pi/Codex, a mismatched effect-policy bundle, invalid owner-specific lease sentinel/field combinations, template/manifest self-reference, incomplete schema digest index, and absolute/parent-traversing guest artifact paths.

Use these public types as the first frozen contract:

```python
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class MeasuredConfigurationId(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"


class ExecutionPhaseOwner(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"
    QUALIFICATION = "QUALIFICATION"
    OPTIMIZER = "OPTIMIZER"
    WINNER_SEALED = "WINNER_SEALED"
    F_SEALED = "F_SEALED"
    GPU_DEMO = "GPU_DEMO"


class RequestClass(str, Enum):
    MAIN = "main"
    AUXILIARY = "auxiliary"
    REVIEW = "review"


class ImageBinding(StrictModel):
    role: Literal["agentteams", "workspace", "control", "candidate_runner", "evaluator"]
    image_sha256: str
    policy_sha256: str


class RunManifestCore(StrictModel):
    schema_version: Literal["secure-memory-run-manifest/v2"]
    campaign_id: str
    campaign_nonce: str
    source_commit: str
    egoagentos_commit: str
    agentteams_repository: Literal["https://github.com/agentscope-ai/AgentTeams"]
    agentteams_commit: Literal["223ddc2b8073e4c8b93bcbb15e1d717f196c04d9"]
    agentteams_contract_lock_sha256: str
    agentteams_resources_sha256: str
    agentteams_role_dag_sha256: str
    workspace_tool_policy_sha256: str
    system_risk_rules_sha256: str
    guardian_rules_sha256: str
    user_projection_policy_sha256: str
    user_term_glossary_sha256: str
    effect_policy_bundle_sha256: str
    design_reference_digests: dict[Literal["pi", "codex"], str]
    provider_base_url: Literal["https://apihub.agnes-ai.com/v1"]
    provider_model: Literal["agnes-2.5-pro"]
    contract_sha256: str
    serializer_sha256: str
    scanner_sha256: str
    evaluator_public_key: str
    controller_checkpoint_public_key: str
    control_receipt_public_keys: dict[str, str]
    channel_key_schedule_sha256: str
    arms: tuple[MeasuredConfigurationId, ...]
    images: tuple[ImageBinding, ...]
    initial_configuration_profiles_sha256: str
    randomization_seed: str
    schedule_sha256: str
    budget_ticket_template_set_sha256: str
    prompt_context_policy_sha256: str
    scenario_rubric_relevance_sha256: str
    provider_qualification_matrix_sha256: str
    approval_fixture_set_sha256: str
    rxp_trust_snapshot_sha256: str
    absolute_request_cap: Literal[360]
    absolute_input_cap: Literal[4_000_000]
    absolute_output_cap: Literal[600_000]
    optimizer_grid_sha256: str
    reserved_request_cap: Literal[356]
    reserved_input_cap: Literal[3_306_000]
    reserved_output_cap: Literal[485_500]


class RunManifest(StrictModel):
    core: RunManifestCore
    manifest_sha256: str


class SignedTaskLeaseCore(StrictModel):
    schema_version: Literal["secure-memory-task-lease/v1"]
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
    project_id: str
    task_id: str
    worker: str
    matrix_user_id: str
    role: str
    stage: str
    allowed_skills: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    request_class: RequestClass
    issued_ticket_ids: tuple[str, ...]
    expires_at_sequence: int = Field(ge=0)
    issuer_id: str
    key_id: str
    issue_sequence: int = Field(ge=0)


class SignedTaskLease(StrictModel):
    core: SignedTaskLeaseCore
    core_sha256: Digest
    signature_base64: str
```

The policy bundle has one exact preimage and no caller-selected composition:

```python
effect_policy_bundle_sha256 = canonical_sha256(
    "effect-policy-bundle",
    {
        "workspace_tool_policy_sha256": workspace_tool_policy_sha256,
        "system_risk_rules_sha256": system_risk_rules_sha256,
        "guardian_rules_sha256": guardian_rules_sha256,
        "user_projection_policy_sha256": user_projection_policy_sha256,
        "user_term_glossary_sha256": user_term_glossary_sha256,
    },
)

NO_WORKSPACE_CHECKPOINT_SHA256 = canonical_sha256(
    "absence",
    {"schema_version": "absence/v1", "kind": "workspace_checkpoint"},
)
```

- [ ] Run the test and confirm it fails because `benchmarks.secure_memory` does not exist.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_canonical_and_manifest.py
```

Expected: import/collection failure naming `benchmarks.secure_memory`.

- [ ] Implement `canonical_bytes()`, `canonical_sha256(domain, value)`, duplicate-key-rejecting `parse_json_bytes()`, strict digest/path validators, and `freeze_manifest(core)`. This task freezes literal test fixtures and provides the production freezer; it does not freeze the real campaign manifest before campaign profiles, scenarios, ticket templates, optimizer grid, policies, resources, and images exist. Campaign Task 10 performs that one real freeze after all prerequisite digests are available.

Digest preimages must be explicit:

```python
def canonical_sha256(domain: str, value: Any) -> str:
    prefix = ("egoagentos:" + domain + ":v1\x00").encode("ascii")
    return hashlib.sha256(prefix + canonical_bytes(value)).hexdigest()
```

- [ ] Define strict `ModelRequest`, `ModelResponse`, `TicketTemplate`, `IssuedBudgetTicket`, the exact `SignedTaskLeaseCore`/`SignedTaskLease` above, `CandidateProposal`, `TrustedFactCore`, `TrustedRelationCore`, `CheckpointCore`, and `CampaignEventCore` beside `RunManifest`; do not leave later tasks to invent duplicate wire types. `policy_sha256` always equals the manifest's recomputed `effect_policy_bundle_sha256`. Lease validators apply these exact owner rules: initial A-E owners require their same configuration, real problem ID, released turn/generation, current requirement-ledger/checkpoint digest, memory watermark, and `post_selection_extension_sha256=None`; owner `F` requires configuration F and the exact verified signed extension/F-profile digest; `WINNER_SEALED` requires the selected original C/D/E winner plus that same extension; `F_SEALED` requires F plus that extension. `QUALIFICATION` requires `configuration_id=None`, `post_selection_extension_sha256=None`, `problem_id="__qualification__"`, `turn=1`, generation equal to case index 1-16, requirement digest equal to `provider_qualification_matrix_sha256`, `workspace_checkpoint_sha256=NO_WORKSPACE_CHECKPOINT_SHA256`, and watermark 0; `OPTIMIZER` requires `configuration_id=None`, `post_selection_extension_sha256=None`, `problem_id="__optimizer__"`, `turn=1`, generation equal to proposal index 1-6, requirement digest equal to the stored optimizer-input digest, the same no-workspace digest, and watermark 0; `GPU_DEMO` requires the selected C/D/E/F configuration, `problem_id="__gpu_demo__"`, `turn=1`, `generation=1`, requirement digest equal to the pre-lease signed GPU-lane authorization core digest, and the selected checkpoint/watermark; its extension digest is required only for selected F and forbidden otherwise. The post-approval GPU execution binding later references both authorization and lease digests, never the reverse, so no hash cycle exists. Validators also require sorted duplicate-free skills/tools/ticket IDs. The manifest binds the precomputed ticket-template-set digest one way; templates contain no manifest digest. An issued ticket is created only after official Project/task/Worker IDs exist and binds both the frozen manifest and one preloaded template ID, so neither object hashes itself. The RunManifest arms are exactly A-E; a concrete F is introduced only by campaign Task 7's signed post-selection extension. `randomization_seed` independently derives the schedule. Export every schema listed in the File Map with a deterministic `python -m benchmarks.secure_memory.manifest schema --check` path. Generate the complete canonical digest index in `docs/contracts/secure-agent/v2/contract-digests.json`; `--check` fails for an orphan, missing, changed, or extra schema. Document package schemas as the sole wire source of truth.
- [ ] Add exact `benchmarks.secure_memory` package discovery/data entries to `pyproject.toml` for public schemas and public corpus only. Do not glob `sealed`, `hidden`, or Evaluator source into the Worker distribution. Add `tests/secure_memory` and `tests/memory` to the explicit pytest target configuration without changing the existing `rxp-bench` entry point.
- [ ] Run focused tests, schema check, Ruff, and mypy.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_canonical_and_manifest.py
uv run --python 3.9 --extra dev python -m benchmarks.secure_memory.manifest schema --check
uv run --python 3.9 --extra dev ruff check benchmarks/secure_memory tests/secure_memory/test_canonical_and_manifest.py
uv run --python 3.9 --extra dev mypy benchmarks/secure_memory/canonical.py benchmarks/secure_memory/models.py benchmarks/secure_memory/manifest.py
```

Expected: all commands exit 0.

- [ ] Commit.

```bash
git add pyproject.toml benchmarks/secure_memory/__init__.py benchmarks/secure_memory/canonical.py benchmarks/secure_memory/models.py benchmarks/secure_memory/manifest.py benchmarks/secure_memory/schemas/run-manifest-v2.schema.json benchmarks/secure_memory/schemas/model-request-v1.schema.json benchmarks/secure_memory/schemas/model-response-v1.schema.json benchmarks/secure_memory/schemas/ticket-template-v1.schema.json benchmarks/secure_memory/schemas/issued-budget-ticket-v1.schema.json benchmarks/secure_memory/schemas/signed-task-lease-v1.schema.json benchmarks/secure_memory/schemas/candidate-proposal-v1.schema.json benchmarks/secure_memory/schemas/trusted-fact-v1.schema.json benchmarks/secure_memory/schemas/trusted-relation-v1.schema.json benchmarks/secure_memory/schemas/checkpoint-v1.schema.json benchmarks/secure_memory/schemas/campaign-event-v1.schema.json docs/contracts/secure-agent/v2/README.md docs/contracts/secure-agent/v2/contract-digests.json tests/secure_memory/test_canonical_and_manifest.py
git commit -m "feat(benchmark): freeze secure campaign contracts"
```

## Task 2: Implement authenticated, replay-safe channel framing

**Files:**

- Create: `benchmarks/secure_memory/substrate/__init__.py`
- Create: `benchmarks/secure_memory/substrate/channel.py`
- Create: `benchmarks/secure_memory/substrate/candidate_rpc.py`
- Create: `benchmarks/secure_memory/schemas/channel-envelope-v2.schema.json`
- Create: `tests/secure_memory/test_channel.py`
- Create: `tests/secure_memory/test_candidate_rpc.py`

- [ ] Write table-driven channel tests for a valid frame plus wrong HMAC, wrong configuration, wrong nonce/epoch, wrong sender/recipient/direction/key ID, reflection into the reverse direction, same-sequence/same-bytes idempotent replay, same-sequence/different-bytes rejection, gap/reordering, recovery with a new epoch, unknown method, invalid UTF-8, duplicate JSON keys, declared-length mismatch, a frame over 1 MiB, and trailing bytes.
- [ ] Write candidate RPC tests for canonical duplicate idempotency, cross-arm/tenant forgery, caller-supplied Gate/Decision/origin/validator fields, overlong statement, more than 16 evidence refs, per-turn/per-problem/per-campaign quota exhaustion, queue depth 33, rate flooding, and a retry that attempts to regain a proposal opportunity.
- [ ] Run the tests and confirm import failures.

```bash
uv run --python 3.9 --extra dev pytest -q \
  tests/secure_memory/test_channel.py \
  tests/secure_memory/test_candidate_rpc.py
```

- [ ] Implement a transport-neutral codec and one independent receive state per complete direction/identity tuple.

```python
class ChannelKind(str, Enum):
    MODEL = "model"
    CANDIDATE = "candidate"
    AGENTTEAMS_CONTROL = "agentteams-control"
    WORKSPACE_EFFECT = "workspace-effect"
    CONTROL_RESULT = "control-result"
    EVALUATOR = "evaluator"


class ChannelEnvelope(StrictModel):
    schema_version: Literal["secure-memory-channel/v2"]
    channel: ChannelKind
    configuration_id: MeasuredConfigurationId
    sender_role: Literal["agentteams", "workspace", "control", "evaluator", "broker", "controller"]
    recipient_role: Literal["agentteams", "workspace", "control", "evaluator", "broker", "controller"]
    direction: Literal["request", "response", "receipt"]
    key_id: str
    campaign_nonce: str
    epoch: int = Field(ge=1)
    sequence: int = Field(ge=1)
    method: str
    idempotency_key: str
    payload_sha256: str
    payload: dict[str, Any]


class CachedReceipt(StrictModel):
    request_frame_sha256: str
    receipt_frame: bytes


class ReceiveWindow:
    def accept(self, frame: bytes, envelope: ChannelEnvelope) -> CachedReceipt | None:
        if envelope.sequence == self.last_sequence:
            if canonical_sha256("channel-frame", frame) == self.last_frame_sha256:
                return self.last_receipt
            raise ChannelRejected("sequence_reuse_with_different_bytes")
        if envelope.sequence != self.last_sequence + 1:
            raise ChannelRejected("sequence_mismatch")
        return None
```

The codec must MAC the version, channel, arm, sender, recipient, direction, key ID, nonce, epoch, sequence, idempotency key, method, and canonical payload. Maintain independent receive windows keyed by `(channel, arm, sender, recipient, direction, key_id, epoch)`; never reuse a window or HMAC key in the reverse direction. It must not advance the window until parsing, MAC verification, identity/direction checks, method checks, payload digest checks, and durable idempotency receipt creation all succeed. Exact replay returns the cached receipt and never repeats a side effect.

The trusted provisioner generates independent random keys per campaign/arm/channel/direction, writes each key only to its sending endpoint and trusted receiving service, and destroys secret disks after final verification. An Agent-held request key authenticates channel/arm accounting only; proposal/model content remains untrusted. Trusted Control/broker receipts used as evidence are Ed25519-signed and journaled at their trusted source, so no trust claim depends on giving an untrusted Agent a symmetric response-verification key.

- [ ] Consume, rather than redefine, Task-1's sole-wire-source `CandidateProposal` and implement `CandidateQuotaLedger` with these immutable limits: 16 proposals/turn, 32/problem, 128/campaign, 64 KiB canonical payload, 2,048 decoded UTF-8 statement bytes, 16 refs, 32 queued frames. Schema/quota rejection may store the canonical non-secret proposal digest; a scanner/credential/PII rejection stores only source class, reason code, and count, never raw bytes or their content digest.

```python
class CandidateProposal(StrictModel):
    schema_version: Literal["secure-memory-candidate/v1"]
    proposal_id: str
    task_id: str
    generation: int = Field(ge=1)
    claimed_fact_id: str | None
    statement_utf8_base64: str
    memory_type: Literal["semantic", "episodic", "procedural"]
    component: str
    outcome_claim: Literal["KEEP", "DROP", "INCONCLUSIVE"]
    applicability_scope: FactScope
    source_refs: tuple[SourceRef, ...]
    support_digest_claims: tuple[str, ...]


FORBIDDEN_TRUST_KEYS = frozenset({
    "gate", "decision", "closure", "origin", "validator", "validated",
    "tenant_id", "audit_head", "rxp_root",
})
```

- [ ] Add deterministic schema export/check and run focused tests plus static checks.

```bash
uv run --python 3.9 --extra dev pytest -q \
  tests/secure_memory/test_channel.py \
  tests/secure_memory/test_candidate_rpc.py
uv run --python 3.9 --extra dev ruff check benchmarks/secure_memory/substrate/channel.py benchmarks/secure_memory/substrate/candidate_rpc.py tests/secure_memory/test_channel.py tests/secure_memory/test_candidate_rpc.py
uv run --python 3.9 --extra dev mypy benchmarks/secure_memory/substrate/channel.py benchmarks/secure_memory/substrate/candidate_rpc.py
```

- [ ] Commit.

```bash
git add benchmarks/secure_memory/substrate/__init__.py benchmarks/secure_memory/substrate/channel.py benchmarks/secure_memory/substrate/candidate_rpc.py benchmarks/secure_memory/schemas/channel-envelope-v2.schema.json docs/contracts/secure-agent/v2/contract-digests.json tests/secure_memory/test_channel.py tests/secure_memory/test_candidate_rpc.py
git commit -m "feat(benchmark): authenticate campaign channels"
```

## Task 3: Build the fail-closed budget ledger and mockable API broker

**Files:**

- Create: `benchmarks/secure_memory/substrate/clock.py`
- Create: `benchmarks/secure_memory/substrate/budget.py`
- Create: `benchmarks/secure_memory/substrate/broker.py`
- Create: `tests/secure_memory/test_budget_ledger.py`
- Create: `tests/secure_memory/test_broker.py`

- [ ] Write budget tests for exact 356-template reservation arithmetic, the non-dispatchable four-request margin, 360th/361st absolute boundaries, input/output cap boundaries, atomic concurrent reservation, one issued ticket per template, failed original-slot consumption, owned retry accounting, no cross-row/configuration/role transfer, duplicate settlement, contradictory usage, cache/reasoning double-count prevention, unattributed AgentTeams calls, and campaign freeze after a provider-capability breach.

Use the frozen class limits:

```python
REQUEST_LIMITS = {
    RequestClass.MAIN: RequestLimit(max_input=10_000, max_output=1_500),
    RequestClass.AUXILIARY: RequestLimit(max_input=6_000, max_output=750),
    RequestClass.REVIEW: RequestLimit(max_input=8_000, max_output=1_000),
}
CAMPAIGN_RESERVATION = BudgetTriple(requests=356, input=3_306_000, output=485_500)
CAMPAIGN_ABSOLUTE = BudgetTriple(requests=360, input=4_000_000, output=600_000)
```

- [ ] Define and test immutable `TicketTemplate` fields `purpose`, `execution_phase_owner`, optional problem/turn, allowed role, request class, slot ID, attempt group, retry owner, and input/output ceiling. After Project/task/Worker creation, Control signs an `IssuedBudgetTicket` binding one unused template ID, live IDs, effective request class/usage phase, frozen manifest digest, issuer/key/sequence/expiry, and signature. Retry reservation uses a main worst-case envelope but cannot raise the effective original class. A broker request without an unused signature-valid task lease and issued ticket already present in its trusted ledger is denied before transport; guest-supplied role/ticket/digest fields are never authority.
- [ ] Write broker tests with an injected `ProviderTransport`: only the versioned endpoint/method/body shape learned by the pinned AgentTeams capability probe, exact model, hard output ceiling, no cross-host redirect, valid TLS flag, bounded owned retry, scanned model-visible bytes, sanitized error, and authoritative terminal usage. The broker admits or rejects the exact qualified AgentTeams request; it never rewrites role prompts/tools or silently swaps API operations. Include first-stream and first-content timestamps as distinct nullable fields.
- [ ] Confirm failures before implementation.

```bash
uv run --python 3.9 --extra dev pytest -q \
  tests/secure_memory/test_budget_ledger.py \
  tests/secure_memory/test_broker.py
```

- [ ] Implement reservation and settlement as an append-only state machine.

```python
class ReservationState(str, Enum):
    RESERVED = "RESERVED"
    DISPATCHED = "DISPATCHED"
    SETTLED = "SETTLED"
    RETAINED = "RETAINED"


class RawUsage(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class SettledUsage(StrictModel):
    raw_usage: RawUsage
    budget_input: int
    budget_output: int
    comparable_input: int
    comparable_output: int
```

`reasoning_tokens` must be `<= output_tokens`; cache fields must be documented subsets of input or zero/absent. They remain subtotals and are never added to input/output again.

- [ ] Implement conservative pre-dispatch input reservation:

```python
estimated = tokenizer_estimate + calibrated_positive_error + 512
byte_bound = len(serialized_model_visible_bytes) + 1_024
reserved_input = max(estimated, byte_bound)
```

Reject or prune before transport when `reserved_input` exceeds the request-class maximum. Reserve the entire output maximum. A failed or timed-out dispatch retains its reservation and request count.

- [ ] Implement `ProviderCapabilityRecord` and a broker state that is `LOCKED` until a dedicated official AgentTeams calibration project proves request shape, per-task role attribution, hard output limiting, authoritative usage, streaming semantics, and zero background/unbudgeted calls. The provisioner opens the provider key exactly once with `O_RDONLY|O_NOFOLLOW|O_CLOEXEC`, verifies the regular-file owner/mode/inode with `fstat`, uses `fchmod` on that same descriptor only after explicit user authorization, and passes only the already-open descriptor to the confined broker with every other descriptor closed. The broker re-`fstat`s and reads that descriptor; neither broker nor controller resolves a key path later. Unit tests use a fake transport and fake secret descriptor only.
- [ ] Run tests and static checks.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_budget_ledger.py tests/secure_memory/test_broker.py
uv run --python 3.9 --extra dev ruff check benchmarks/secure_memory/substrate/budget.py benchmarks/secure_memory/substrate/broker.py tests/secure_memory/test_budget_ledger.py tests/secure_memory/test_broker.py
uv run --python 3.9 --extra dev mypy benchmarks/secure_memory/substrate/budget.py benchmarks/secure_memory/substrate/broker.py
```

- [ ] Commit.

```bash
git add benchmarks/secure_memory/substrate/clock.py benchmarks/secure_memory/substrate/budget.py benchmarks/secure_memory/substrate/broker.py tests/secure_memory/test_budget_ledger.py tests/secure_memory/test_broker.py
git commit -m "feat(benchmark): enforce provider budget reservations"
```

## Task 4: Reject secrets and safely ingest the stopped-VM artifact image

**Files:**

- Create: `benchmarks/secure_memory/substrate/scanner.py`
- Create: `benchmarks/secure_memory/substrate/admission.py`
- Create: `benchmarks/secure_memory/substrate/artifact_ingest.py`
- Create: `tests/secure_memory/test_scanner.py`
- Create: `tests/secure_memory/test_admission_gate.py`
- Create: `tests/secure_memory/test_artifact_ingest.py`

- [ ] Write scanner tests for API-key formats, private keys, bearer tokens, authorization headers, database URLs, evaluator/HMAC key fields, credential canaries, invalid UTF-8, bidirectional-control characters, and oversized text. Assert rejected bytes do not appear in the scanner result, logs, temporary directory, or evidence journal.
- [ ] Write a shared-admission matrix proving provider request/response, bridge/Matrix observation, Workspace output/patch, artifact member, memory/review text, and bundle record are scanned before forwarding or trusted persistence. Write raw artifact-image tests for a valid diff/JSONL set plus parent traversal, absolute path, Unicode separator ambiguity, duplicate canonical path, duplicate digest, overlapping offsets, sparse/overflow offsets, bad digest, too many members, depth overflow, expanded-size overflow, executable/SVG/media-type admission, symlink/hardlink/device entry types, nested archive, tar/zip bomb signature, and trailing undeclared bytes.
- [ ] Confirm tests fail.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_scanner.py tests/secure_memory/test_admission_gate.py tests/secure_memory/test_artifact_ingest.py
```

- [ ] Implement one scanner result that never carries rejected content.

```python
class ScanDecision(StrictModel):
    accepted: bool
    sanitized_text: str | None
    reason_codes: tuple[str, ...]
    source_class: str
    finding_count: int
    accepted_content_sha256: str | None


def rejected_scan(source_class: str, reasons: Sequence[str], raw: bytes) -> ScanDecision:
    return ScanDecision(
        accepted=False,
        sanitized_text=None,
        reason_codes=tuple(sorted(set(reasons))),
        source_class=source_class,
        finding_count=len(reasons),
        accepted_content_sha256=None,
    )
```

The implementation must not retain `raw`, its digest, a reversible token, or a temporary copy after a credential/PII rejection. Accepted redaction is allowed only for unsigned display text with deterministic placeholders, and its digest covers the accepted sanitized bytes. A signed/content-addressed object that would change after scanning is rejected instead.

- [ ] Implement one `EvidenceAdmissionGate` used by broker response-before-forward/log, bridge/Matrix observation-before-trusted-journal, Workspace output/patch-before-receipt/export, every artifact member-before-write, memory/review-before-store, and bundle-before-packaging. Official Matrix may retain untrusted bytes inside its disposable VM before observation; those are not trusted evidence. A secret rejection stores only source class/reason/count, never body/page/member bytes or their digest.

- [ ] Implement `artifact-disk/v1` as a raw bounded container, not a mounted guest filesystem:

```text
16-byte magic | u64-be manifest length | canonical manifest JSON | concatenated blobs
```

The manifest declares each member's canonical relative path, entry type `regular`, media type, byte offset, byte length, and SHA-256. Accept only UTF-8 text, canonical JSON/JSONL, unified diff, and Markdown. Reject SVG, archives, and executable types rather than unpacking/rendering them. Controller-only charts are generated later from normalized admitted metrics.

- [ ] Ensure ingestion opens the stopped image read-only, admission-scans each member before any member write, writes accepted members with `O_CREAT|O_EXCL|O_NOFOLLOW` beneath a newly created `0700` directory, fsyncs files and directory, and returns digests of admitted bytes. It must never execute, import, render, or recursively inspect guest bytes during admission.
- [ ] Run tests/static checks.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_scanner.py tests/secure_memory/test_admission_gate.py tests/secure_memory/test_artifact_ingest.py
uv run --python 3.9 --extra dev ruff check benchmarks/secure_memory/substrate/scanner.py benchmarks/secure_memory/substrate/admission.py benchmarks/secure_memory/substrate/artifact_ingest.py tests/secure_memory/test_scanner.py tests/secure_memory/test_admission_gate.py tests/secure_memory/test_artifact_ingest.py
uv run --python 3.9 --extra dev mypy benchmarks/secure_memory/substrate/scanner.py benchmarks/secure_memory/substrate/admission.py benchmarks/secure_memory/substrate/artifact_ingest.py
```

- [ ] Commit.

```bash
git add benchmarks/secure_memory/substrate/scanner.py benchmarks/secure_memory/substrate/admission.py benchmarks/secure_memory/substrate/artifact_ingest.py tests/secure_memory/test_scanner.py tests/secure_memory/test_admission_gate.py tests/secure_memory/test_artifact_ingest.py
git commit -m "feat(benchmark): harden artifact and secret admission"
```

## Task 5: Authenticate sealed evaluator results

**Files:**

- Create: `benchmarks/secure_memory/substrate/evaluator_channel.py`
- Create: `benchmarks/secure_memory/substrate/runner_channel.py`
- Create: `benchmarks/secure_memory/schemas/evaluator-result-envelope-v1.schema.json`
- Create: `benchmarks/secure_memory/schemas/sealed-requirement-release-v1.schema.json`
- Create: `tests/secure_memory/test_evaluator_channel.py`
- Create: `tests/secure_memory/test_candidate_runner_isolation.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] Add tests using ephemeral Ed25519 keys for valid receipt, wrong evaluator identity, unknown key ID, wrong signature, changed seed/patch/tree/test-suite/image/policy digest, modified/duplicated/unbound verified fact or relation, false edge between valid facts, duplicate result, out-of-order result, broken previous-result link, cross-configuration substitution, and idempotent retry of the exact signed bytes. Add a signed `SEALED_REQUIREMENT_RELEASE` contract that verifies frozen F digest, problem/generation, ciphertext/plaintext digest, prior checkpoint, sequence, and release key before emitting only admitted requirement bytes plus an authenticated receipt.
- [ ] Add Candidate Runner canary tests proving candidate code cannot read `/proc/*/fd`, service process memory/environment, hidden paths, key disks, evaluator/result sockets, ptrace/signal the harness, access another database or PG superuser, enable dangerous extensions/`COPY PROGRAM`, or forge a result. The Evaluator must communicate only through a fixed black-box candidate protocol, rebuild/reset a campaign-marked throwaway cluster, collect records independently, tear the Runner down, and sign afterward; it never imports/executes candidate bytes.
- [ ] Confirm the tests fail.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_evaluator_channel.py tests/secure_memory/test_candidate_runner_isolation.py
```

- [ ] Add `cryptography` as the only new runtime crypto dependency and lock it. Use its Ed25519 primitive directly; do not design a custom signature algorithm.

```bash
uv add 'cryptography>=42,<47'
```

- [ ] Implement the exact signed core:

```python
class EvaluatorResultCore(StrictModel):
    schema_version: Literal["secure-memory-evaluator-result/v1"]
    campaign_id: str
    run_id: str
    configuration_id: MeasuredConfigurationId
    problem_id: str
    generation: int = Field(ge=1)
    agentteams_project_id: str
    workspace_checkpoint_sha256: str
    evaluator_id: str
    key_id: str
    sequence: int = Field(ge=1)
    seed_sha256: str
    patch_sha256: str
    tree_sha256: str
    test_suite_sha256: str
    evaluator_image_sha256: str
    policy_sha256: str
    previous_result_sha256: str
    tests: tuple[TestRecord, ...]
    verified_facts: tuple[TrustedFact, ...]
    verified_relations: tuple[TrustedRelation, ...]


class SignedEvaluatorResult(StrictModel):
    core: EvaluatorResultCore
    core_sha256: str
    signature_base64: str
```

Task 1 is the only source of the exact wire cores. `TrustedFactCore` contains schema version, stable fact ID/kind, `statement_utf8_base64`, explicit outcome, applicability scope, sorted source references/support digests, and domain digest. `TrustedRelationCore` contains schema version, stable relation ID/type, exact source/target fact digests, scope, sorted source references/support digests, and domain digest. The evaluator emits only facts/relations derived by deterministic suite logic. A later LLM proposal can claim their IDs/bytes but cannot invent promotable wording or a promotable edge between valid endpoints.

Sign domain-separated canonical `core` bytes. Freeze only the public key in the host manifest. Generate the campaign-scoped private key after base-image sealing, place it only on that campaign's read-only Evaluator secret disk, and never copy it into the reusable base image, AgentTeams/Workspace/Control disks, host journal, artifact image, or bundle.

- [ ] Make verifier state advance only after admission, signature, identity, sequence, all bound digests, previous link, and idempotency checks pass. The accepted receipt stores canonical signed bytes and digest. A credential/PII scanner rejection stores source class/reason/count only; other non-secret cryptographic rejection may retain the canonical presented digest.
- [ ] Export/check schema and run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_evaluator_channel.py tests/secure_memory/test_candidate_runner_isolation.py
uv run --python 3.9 --extra dev ruff check benchmarks/secure_memory/substrate/evaluator_channel.py benchmarks/secure_memory/substrate/runner_channel.py tests/secure_memory/test_evaluator_channel.py tests/secure_memory/test_candidate_runner_isolation.py
uv run --python 3.9 --extra dev mypy benchmarks/secure_memory/substrate/evaluator_channel.py benchmarks/secure_memory/substrate/runner_channel.py
```

- [ ] Commit.

```bash
git add pyproject.toml uv.lock benchmarks/secure_memory/substrate/evaluator_channel.py benchmarks/secure_memory/substrate/runner_channel.py benchmarks/secure_memory/schemas/evaluator-result-envelope-v1.schema.json benchmarks/secure_memory/schemas/sealed-requirement-release-v1.schema.json docs/contracts/secure-agent/v2/contract-digests.json tests/secure_memory/test_evaluator_channel.py tests/secure_memory/test_candidate_runner_isolation.py
git commit -m "feat(benchmark): verify sealed evaluator receipts"
```

## Task 6: Generate fixed QEMU commands and deny-by-default Seatbelt profiles

**Files:**

- Create: `benchmarks/secure_memory/substrate/qemu.py`
- Create: `benchmarks/secure_memory/substrate/seatbelt.py`
- Create: `benchmarks/secure_memory/substrate/image_builder.py`
- Create: `deploy/secure_memory/seatbelt/controller.sb.tmpl`
- Create: `deploy/secure_memory/seatbelt/broker.sb.tmpl`
- Create: `deploy/secure_memory/seatbelt/qemu.sb.tmpl`
- Create: `deploy/secure_memory/guest/agentteams-vm-manifest.json`
- Create: `deploy/secure_memory/guest/workspace-vm-manifest.json`
- Create: `deploy/secure_memory/guest/control-vm-manifest.json`
- Create: `deploy/secure_memory/guest/candidate-runner-vm-manifest.json`
- Create: `deploy/secure_memory/guest/evaluator-vm-manifest.json`
- Create: `deploy/secure_memory/README.md`
- Create: `tests/secure_memory/test_qemu_and_seatbelt.py`

- [ ] Test command construction without launching QEMU. Assert `shell=False`, empty environment, closed inherited descriptors except exact role secret FDs, `-nodefaults`, `-no-user-config`, `-display none`, no shared folders, no USB/clipboard, no `-nic`/`-net`, read-only base/seed disks, one explicit role-owned writable overlay, role-specific virtio sockets, explicit RAM/CPU limits, and exact resolved campaign paths. AgentTeams VM has no source seed/overlay; Workspace VM alone owns the persistent engineering overlay; Candidate Runner alone receives reconstructed candidate bytes and a campaign-marked throwaway PG disk; Evaluator has hidden suites/key but no candidate disk.
- [ ] Test profile rendering rejects a campaign root containing newline/NUL, a path outside the campaign root, a symlinked disk/socket, extra readable file, extra writable file, inherited key path, source-worktree path, or network permission in controller/QEMU profiles. Broker profile may allow outbound network and the frozen runtime closure needed for TLS (broker executable/libraries, system CA roots, and resolver configuration), plus the already-open key FD and sanitized log paths; it must still deny source worktrees, user documents, SSH/browser/Keychain material, and all other campaign files.
- [ ] Test in-guest service isolation: Worker/candidate UIDs are non-root with dropped capabilities; adapters/executor/harness use distinct service UIDs, PID/user/mount namespaces, `hidepid`, ptrace/signal denial, immutable binaries, closed descriptors, exact socket ACLs, and no readable service environment/key/FD. Canary Worker/test code must fail to inspect or control each service.
- [ ] Confirm tests fail.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_qemu_and_seatbelt.py
```

- [ ] Implement immutable launch specifications rather than free-form argv.

```python
class VMRole(str, Enum):
    AGENTTEAMS = "agentteams"
    WORKSPACE = "workspace"
    CONTROL = "control"
    CANDIDATE_RUNNER = "candidate_runner"
    EVALUATOR = "evaluator"


class QemuLaunchSpec(StrictModel):
    role: VMRole
    configuration_id: MeasuredConfigurationId
    qemu_binary: Path
    base_image: Path
    overlay_image: Path
    seed_image: Path | None
    artifact_image: Path | None
    serial_channels: tuple[SerialChannel, ...]
    memory_mib: int = Field(ge=512, le=16_384)
    vcpus: int = Field(ge=1, le=8)
```

`build_qemu_argv()` must synthesize every argument from typed fields. It must refuse an unresolved path, symlink, wrong owner/mode, writable base image, or channel belonging to another configuration. AgentTeams has model, AgentTeams-control, candidate, and workspace-effect request channels but no source disk; Workspace has workspace-effect request/result channels and the source overlay but no model/provider/database route; Control has AgentTeams-control, candidate, workspace decision/result, sealed-release, and finalizer channels; Candidate Runner has only the black-box test/reset channel and throwaway source/PG disks; Evaluator has hidden suites, runner-control, sealed-release, and evaluator-result channels but no candidate source mount. No guest has general networking. Controller/Matrix remain inside the AgentTeams VM and loopback adapters bridge only the typed channels.

- [ ] Implement three separate Seatbelt renderers and a `SandboxedProcessSpec(argv, env={}, pass_fds=())`. Build the launch list exactly as `["/usr/bin/sandbox-exec", "-f", str(rendered_profile), "--", *fixed_argv]` with no shell. If `sandbox-exec`, QEMU/HVF, or required virtio devices are unavailable, preflight returns a named blocking capability result; it must not silently run unsandboxed or host-native.
- [ ] Implement `image_builder.py` as a fixed-manifest wrapper around resolved `qemu-img`/guest provisioning argv, with no shell, no host source mount, exact input/output roots, reproducible package manifests/SBOM, and digest verification. Its dry-run is unit-tested; real build is `uv run --python 3.9 --extra dev python -m benchmarks.secure_memory.substrate.image_builder build --manifest-root deploy/secure_memory/guest --output-root /absolute/campaign/images` and is gated to the later VM phase.
- [ ] Document five role-minimized base-image allowlists as a separate trusted provisioning step. AgentTeams image contains only the pinned official runtime, Matrix/Controller dependencies, rendered Worker resources, and isolated loopback adapters; Workspace contains frozen build/test dependencies and typed executor; Control contains bridge/policy/memory/PostgreSQL clients but no model shell; Candidate Runner contains only public candidate adapters/dependencies and throwaway PG; Evaluator contains hidden suites and signer but no candidate interpreter/import path, AgentTeams, provider, or PG candidate credentials. Dependencies are downloaded before sealing, image digests are frozen, guest networking is absent during campaign execution, and no host source mount is used.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_qemu_and_seatbelt.py
uv run --python 3.9 --extra dev ruff check benchmarks/secure_memory/substrate/qemu.py benchmarks/secure_memory/substrate/seatbelt.py benchmarks/secure_memory/substrate/image_builder.py tests/secure_memory/test_qemu_and_seatbelt.py
uv run --python 3.9 --extra dev mypy benchmarks/secure_memory/substrate/qemu.py benchmarks/secure_memory/substrate/seatbelt.py benchmarks/secure_memory/substrate/image_builder.py
```

- [ ] Commit.

```bash
git add benchmarks/secure_memory/substrate/qemu.py benchmarks/secure_memory/substrate/seatbelt.py benchmarks/secure_memory/substrate/image_builder.py deploy/secure_memory/README.md deploy/secure_memory/seatbelt/controller.sb.tmpl deploy/secure_memory/seatbelt/broker.sb.tmpl deploy/secure_memory/seatbelt/qemu.sb.tmpl deploy/secure_memory/guest/agentteams-vm-manifest.json deploy/secure_memory/guest/workspace-vm-manifest.json deploy/secure_memory/guest/control-vm-manifest.json deploy/secure_memory/guest/candidate-runner-vm-manifest.json deploy/secure_memory/guest/evaluator-vm-manifest.json tests/secure_memory/test_qemu_and_seatbelt.py
git commit -m "feat(benchmark): define isolated qemu launch boundary"
```

## Task 7: Implement the append-only journal and recoverable controller

**Files:**

- Create: `benchmarks/secure_memory/substrate/journal.py`
- Create: `benchmarks/secure_memory/substrate/controller.py`
- Create: `tests/secure_memory/test_controller_recovery.py`

- [ ] Write journal tests for genesis, exact predecessor, duplicate event ID, sequence gap, modified historical bytes, truncated tail, injected unknown event, secret-bearing payload, crash between fsync and state materialization, and deterministic replay to the same state/root.
- [ ] Write controller tests for legal state transitions, coordinated AgentTeams/Workspace/Control/Candidate-Runner/Evaluator lifecycle, Runner only after AgentTeams quiescence plus a stopped/reconstructed Workspace patch, Evaluator signing only after independent black-box collection and Runner teardown, one restore after VM crash, second crash failure, budget-limited stop before dispatch, channel failure retry idempotency/new epoch, no cross-configuration Team/room/token/database/disk/channel reuse, and no terminal Decision on turns 1-3.
- [ ] Confirm tests fail.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_controller_recovery.py
```

- [ ] Implement journal records with a complete hash preimage.

```python
class CampaignEventCore(StrictModel):
    schema_version: Literal["secure-memory-campaign-event/v1"]
    sequence: int = Field(ge=1)
    event_id: str
    campaign_id: str
    run_id: str | None
    configuration_id: MeasuredConfigurationId | None
    event_type: str
    monotonic_ns: int = Field(ge=0)
    payload: dict[str, Any]
    previous_sha256: str


class CampaignEvent(StrictModel):
    core: CampaignEventCore
    event_sha256: str
```

Write newline-delimited canonical JSON to an owner-only file, flush and fsync each accepted event, and update a rebuildable state snapshot only after the durable event write. Replay rejects a non-canonical line or any mismatch.

- [ ] Implement controller states:

```text
CREATED -> PREFLIGHTED -> QUALIFIED -> RUNNING -> EVALUATING -> REVIEWING
-> DECIDED -> PACKAGED -> VERIFIED

Any nonterminal state -> BUDGET_LIMITED | CAPABILITY_UNAVAILABLE | FAILED
```

Configuration state, AgentTeams Project/room roots, problem state, turn checkpoint, five VM identities, channel windows/epochs, ticket receipts, evaluator/release sequence, and restore count must all be replay-derived. Controller dependencies are protocols (`VMBackend`, `AgentTeamsControlClient`, `Broker`, `CandidateRunnerClient`, `EvaluatorClient`, `ArtifactIngestor`, `Clock`) so unit tests remain local.

- [ ] Ensure the controller never receives a broker secret or database validator DSN. It sends sanitized typed commands to broker/Control services and stores only receipts.
- [ ] Run focused/static tests.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_controller_recovery.py
uv run --python 3.9 --extra dev ruff check benchmarks/secure_memory/substrate/journal.py benchmarks/secure_memory/substrate/controller.py tests/secure_memory/test_controller_recovery.py
uv run --python 3.9 --extra dev mypy benchmarks/secure_memory/substrate/journal.py benchmarks/secure_memory/substrate/controller.py
```

- [ ] Commit.

```bash
git add benchmarks/secure_memory/substrate/journal.py benchmarks/secure_memory/substrate/controller.py tests/secure_memory/test_controller_recovery.py
git commit -m "feat(benchmark): recover campaign state from journal"
```

## Task 8: Assemble the no-secret substrate preflight and CLI

**Files:**

- Create: `benchmarks/secure_memory/substrate/preflight.py`
- Create: `benchmarks/secure_memory/cli.py`
- Create: `benchmarks/secure_memory/broker_launcher.py`
- Create: `tests/secure_memory/test_substrate_preflight.py`
- Modify: `pyproject.toml`
- Modify: `Makefile`
- Modify: `.gitignore`

- [ ] Write a preflight test that runs with a deterministic mock broker and fake VM backend, exercises the negative cases from Tasks 2-7, emits one record per named subcheck, and refuses PASS when a subcheck is missing, skipped, unsigned, stale, or from the wrong manifest digest.
- [ ] Add a filesystem test proving the campaign root is outside source package directories, mode `0700`, children `0600`, no symlink component, and no normal worktree path appears in a guest launch spec.
- [ ] Add CLI tests proving `init` materializes a mode-checked campaign root and deterministic manifest/template/schedule files without a hash cycle, and `preflight --offline` never instantiates the production secret reader or provider transport. Add launcher tests for one `O_NOFOLLOW|O_CLOEXEC` open, same-FD `fstat`/optional authorized `fchmod`, inode stability, all-other-FD closure, and path-swap/symlink rejection.
- [ ] Confirm tests fail.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory/test_substrate_preflight.py
```

- [ ] Implement these commands:

```text
secure-memory-bench schema --check
secure-memory-bench init --config /absolute/campaign-config.json --campaign-root /absolute/campaign
secure-memory-bench preflight --offline --manifest /absolute/campaign/run-manifest.json --output /absolute/campaign/preflight
secure-memory-bench capability-probe --manifest /absolute/campaign/run-manifest.json --output /absolute/campaign/qualification
secure-memory-bench verify-journal --journal /absolute/campaign/events.jsonl --manifest /absolute/campaign/run-manifest.json
```

`init` is the sole owner of campaign-root creation and manifest materialization.
It accepts only the owner-only frozen configuration produced by campaign Task 10,
recomputes and verifies the manifest, template-set, and derived-schedule digests,
then copies those bytes into a new empty campaign root. It must never regenerate a
seed, schedule, template set, or alternate manifest; issued tickets occur later.
`capability-probe` must require all mock/VM preflight receipts and exact
campaign-root modes before it may launch the broker. The CLI/controller may
convey the configured key path only to the sandboxed
`secure-memory-broker-launcher`; that minimal launcher performs the single safe
open and passes the already-open FD. Broker/controller never resolve it later.

- [ ] Add the package entry point, explicit package data, pytest paths, and `.PHONY` Make targets. The AgentTeams/Workspace distribution includes no `sealed` corpus or Evaluator suite:

```toml
secure-memory-bench = "benchmarks.secure_memory.cli:main"
secure-memory-broker-launcher = "benchmarks.secure_memory.broker_launcher:main"
```

```make
.PHONY: test-secure-memory-substrate secure-memory-preflight-offline

test-secure-memory-substrate:
	$(UV) run --python 3.9 --extra dev pytest -q tests/secure_memory
	$(UV) run --python 3.9 --extra dev ruff check benchmarks/secure_memory tests/secure_memory
	$(UV) run --python 3.9 --extra dev mypy benchmarks/secure_memory

secure-memory-preflight-offline:
	$(UV) run --python 3.9 --extra dev secure-memory-bench preflight --offline --manifest "$(MANIFEST)" --output "$(OUTPUT_DIR)"
```

Do not add `capability-probe` or paid campaign execution to the default `make test` target.

- [ ] Ignore local campaign roots and disk formats without ignoring source fixtures:

```gitignore
/.secure-memory-campaigns/
*.qcow2
*.raw.artifact
```

- [ ] Run the complete plan gate.

```bash
uv run --python 3.9 --extra dev pytest -q tests/secure_memory
uv run --python 3.9 --extra dev ruff check benchmarks/secure_memory tests/secure_memory
uv run --python 3.9 --extra dev mypy benchmarks/secure_memory
uv run --python 3.9 --extra dev python -m benchmarks.secure_memory.cli schema --check
uv run --python 3.9 --extra dev python -m benchmarks.runner --repetitions 2 --strict --output-json /tmp/rxp-bench-compat.json --output-md /tmp/rxp-bench-compat.md
```

Expected: all commands exit 0; the last command proves the existing benchmark remains compatible. No QEMU process, network request, or key read occurs.

- [ ] Commit.

```bash
git add pyproject.toml Makefile .gitignore benchmarks/secure_memory/substrate/preflight.py benchmarks/secure_memory/cli.py benchmarks/secure_memory/broker_launcher.py tests/secure_memory/test_substrate_preflight.py
git commit -m "feat(benchmark): gate secure substrate preflight"
```

## Plan Exit Criteria

- Every focused test and the full Task 8 gate passes.
- No production provider call was made and `key.txt` was never read.
- A fake campaign can replay its journal, reject malformed channels/artifacts/evaluator results, and stop before overspending.
- QEMU and Seatbelt launch specifications are deterministic and fail closed when a required host capability is absent.
- The old `rxp-bench/v1` output and strict local run still pass.
- The implementation is ready for the AgentTeams safety/attention contract and later real VM preflight; it is not yet authorized for paid execution.
