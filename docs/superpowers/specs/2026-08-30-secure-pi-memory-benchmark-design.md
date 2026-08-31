# Historical Secure Pi Memory Architecture and Strong-Validation Benchmark

**Date:** 2026-08-30
**Status:** Superseded; do not implement
**Target repositories:** `ego_agent_infra`, `pi`, and a read-only `codex` reference checkout

> **Supersession notice (2026-08-30):** A later user instruction requires the
> competition entry to remain entirely on the semifinal AgentTeams framework.
> Pi and Codex may be consulted only as read-only design references; neither may
> be an agent runtime, adapter, benchmark arm, or measured reference. The
> executable replacement is
> [`2026-08-30-agentteams-secure-memory-benchmark-design.md`](2026-08-30-agentteams-secure-memory-benchmark-design.md).
> This historical document is retained so the evidence trail records why the
> approved architecture changed.

## 1. Purpose

Build and compare three long-term memory architectures on top of Pi while preserving a narrowly ported Codex-style deterministic safety boundary. The system must balance response speed, total model token consumption, task completion quality, evidence-grounded reliability, focused per-turn attention, and protection of every host file outside the experiment campaign.

The benchmark uses three real gaps in the frozen `ego_agent_infra` codebase and simulates a user who introduces functional, security, compatibility, concurrency, and recovery requirements over multiple turns.

## 2. Current-state findings

The implementation starts from these verified repository facts:

1. PostgreSQL is already the production source of truth; SQLite remains a development fallback.
2. PostgreSQL audit events are append-only and hash-chained, and event delivery already uses `LISTEN/NOTIFY` as a wake-up signal with durable cursor replay.
3. Control Plane and RXP/1 remain separate evidence chains; RXP documents are verified but not persisted into the task/memory provenance path.
4. Real live tasks do not currently create memory. The synthetic path can create and immediately promote memory, so `validated=true` is not yet a sufficient trust statement.
5. The existing memory store has no cross-task retrieval or per-turn context injection.
6. The current validator is not operationally independent from the writer and does not bind promotion to a final decision closure.
7. Pi's default file and shell tools can access anything allowed by the host user. Extensions execute in process and can alter tool arguments before execution.
8. Codex contains useful deterministic policy, approval, path, sandbox, provenance, compaction, and memory-pollution patterns, but its full Guardian and runtime should not be transplanted wholesale.

## 3. Scope

### 3.1 In scope

- A host-controlled safety adapter for Pi.
- A common memory plugin interface and three distinct memory implementations.
- Five primary experiment arms plus a full Codex reference arm.
- One post-selection optimized arm evaluated on sealed follow-up requirements.
- QEMU isolation, API brokering, budget enforcement, tracing, scoring, replay, and evidence packaging.
- Three cumulative real engineering problems in isolated copies of `ego_agent_infra`.
- PostgreSQL/PolarDB PG-compatible production memory contracts.

### 3.2 Out of scope

- Automatic deployment to a production PolarDB instance without credentials supplied for that purpose.
- Claiming that a CPU-only experiment satisfies the judges' suggested real GPU demonstration.
- Replacing Pi with the complete Codex runtime.
- Treating an LLM reviewer as cryptographic or human attestation.
- Automatically merging an experimental patch into the user's normal working repository.
- Pushing commits or artifacts to a remote repository.

## 4. Experimental matrix

| Arm | Agent runtime | Agent-level safety | Long-term memory | Interpretation |
| --- | --- | --- | --- | --- |
| A | Upstream Pi | Disabled | None | Bare-Pi agent logic under common outer isolation |
| B | Pi | Codex-style deterministic adapter | None | Safety-only baseline |
| C | Pi | Same as B | Summary and search | Conventional memory baseline |
| D | Pi | Same as B | Evidence-grounded two-layer memory | Reliability-oriented design |
| E | Pi | Same as B | Multi-agent evidence graph | Relationship-oriented design |
| R | Full Codex | Native behavior, normalized where possible | Native configuration | Capability-gated descriptive reference |

A through E use isolated environments and a common provider, workload, tool surface, limits, and evaluator. Only B through E form the preregistered causal memory comparison. A retains upstream Pi agent logic but necessarily runs through the outer VM, broker, and executor boundary, so it is not a claim about unrestricted host-native Pi. R retains Codex-specific instructions, request construction, tools, compaction, and optional internal calls; it runs only if the broker capability probe passes and never enters the winner, tie-break, causal estimate, or B-E Pareto frontier.

After the initial comparison, the leading C/D/E design produces one optimized variant, F. F receives a new isolated VM disk and is reported as post-selection evidence, not folded into the original causal ranking.

## 5. Security and workspace isolation

### 5.1 Isolation boundary

Each arm has an untrusted Agent VM and a separate trusted Control VM, each cloned from a sealed, read-only base image. The Control VM contains the arm's memory service and PostgreSQL instance but no model-driven shell. Hardware virtualization is available through macOS HVF. VMs are normally run sequentially in arm pairs to bound host resource use.

The Agent VM receives only its own writable `qcow2` disk, an immutable source-seed disk, a narrow framed model-broker channel, and a per-arm artifact disk image. It receives no mounted host directory, user home directory, real API credential, database DSN, administrator/validator credential, SSH/Git credential, browser state, clipboard, USB device, general internet access, host socket, hidden test, or another arm's disk. The Agent VM can submit schema-bounded candidate proposals to its Control VM but cannot connect to PostgreSQL directly.

The artifact disk is read by the host only after the VM stops. It is never a live shared folder. Ingestion never executes or recursively mounts guest content; a safe extractor enforces allowlisted types, member count, expanded size, depth, device/hardlink rejection, symlink rejection, canonical destinations, and digests.

This design mitigates model-generated code, malicious dependencies, and normal guest compromise. It does not claim mathematical protection from an unknown QEMU, macOS kernel, or hardware vulnerability. Absolute VM-escape assurance would require a separate disposable physical host.

### 5.2 Trusted host processes

Only two trusted service processes operate on the host in addition to the sandboxed QEMU processes:

1. **Campaign controller:** starts VMs, enforces budgets, sequences scenarios, records append-only events, and invokes evaluators.
2. **API broker:** reads the API key, calls only `https://apihub.agnes-ai.com/v1`, permits only `agnes-2.5-pro`, sanitizes errors, and records usage without logging credentials.

A guest-local loopback adapter exposes only the OpenAI-compatible operations required by Pi or Codex and carries them over the framed broker channel. It uses a dummy guest credential. The agent runtime and tool executor use separate identities; tool subprocesses run without a network namespace route to either the loopback adapter or the internet.

QEMU, the campaign controller, and the API broker each run under separate deny-by-default macOS Seatbelt profiles with an empty inherited environment. The QEMU profile permits only the exact campaign disks and per-arm sockets and denies network. The controller cannot read `key.txt`. The broker can read only the exact key path, write its exact sanitized log path, and make outbound TLS connections needed for the allowlisted API host; application policy rejects redirects or methods targeting another host. It cannot read source worktrees, user documents, SSH material, browser data, or Keychain items.

Every arm has a dedicated channel identity, campaign nonce, monotonic sequence number, frame-size limit, rate limit, and HMAC. Cross-arm frames, replays, reordering, unknown methods, arbitrary host-file requests, and unregistered model names are rejected before forwarding.

Agent-to-Control candidate submission uses a different dedicated per-arm virtio-serial channel, not guest IP networking. PostgreSQL binds only to the Control VM loopback/Unix socket; database, migration, validator, and management ports have no route from the Agent VM. Candidate RPC uses a canonical typed schema plus arm/tenant identity, campaign nonce, monotonic sequence, content digest, idempotency key, size/rate/queue limits, and HMAC. The Control service recomputes origin, Gate, Decision, closure, validator, and tenant authority fields and rejects any proposal that tries to supply them as truth. Direct port scans, forged/cross-arm/replayed/oversized frames, flooding, and queue starvation are mandatory negative tests.

Evaluator results use a third versioned channel. The Evaluator VM owns a campaign-scoped Ed25519 private key whose public key and identity are frozen in the RunManifest. A result envelope binds run/arm, immutable seed, patch, reconstructed tree, test-suite, evaluator-image and policy digests, result sequence, test records, and previous-result digest. The controller verifies signature, identity, sequence, digests, and idempotency before appending the reference to the arm's Control VM. Forged identity, seed/patch substitution, duplicate/out-of-order delivery, and channel failure/retry are tested. PostgreSQL inside the Evaluator VM is an ephemeral hidden-test target; the separate Control VM PostgreSQL remains the arm's provenance source of truth.

The API key file is changed from mode `0644` to `0600` during implementation preflight. The key is never placed in a prompt, child-process environment, guest disk, trace, memory record, or artifact.

### 5.3 Negative isolation preflight

No paid campaign may start until every arm proves that all of the following fail closed:

- absolute-path access outside the guest workspace;
- `..` traversal and symlink escape;
- access to another arm's canary;
- access to a credential canary;
- arbitrary network access and DNS resolution;
- resource-limit escape;
- artifact archive path traversal; and
- unauthorized model selection or budget bypass.

The preflight uses campaign-owned canaries rather than probing unrelated personal files.

## 6. Common execution architecture

```text
Scenario Driver
  -> Memory.before_turn
  -> Context Budgeter
  -> Pi/Codex Agent
  -> Final-argument Safety Authorizer
  -> Guest Tool Executor
  -> Deterministic Tests and Evidence Gate
  -> Blind Independent Reviewer
  -> Decision
  -> Memory.after_decision / consolidate
```

### 6.1 Components

- **Scenario Driver:** releases the same user modification to each arm and owns the hidden requirement ledger.
- **Agent Adapter:** normalizes provider messages, tool schemas, events, and usage across Pi and Codex.
- **Context Budgeter:** applies the same model-visible input ceiling and truncation policy.
- **Safety Authorizer:** evaluates final tool arguments after any Pi extension mutation.
- **Tool Executor:** performs tool calls only inside the current guest workspace.
- **Memory Plugin:** implements one common contract for B through E.
- **Deterministic Evaluator:** runs sealed hidden, replay, security, and recovery tests in a separate no-agent Evaluator VM.
- **Blind Reviewer:** sees a label-free diff, requirements, and test evidence, but no arm identity or memory transcript.
- **Evidence Packager:** freezes raw records, metrics, Matrix/RXP messages, gates, decisions, and hashes.

### 6.2 Memory plugin contract

Every memory implementation uses the same semantic interface:

- `before_turn(query, task_state, budget) -> ContextPacket`
- `retrieve(query, filters, budget) -> RetrievalResult`
- `after_decision(trace, evidence, decision) -> ProposalSet`
- `consolidate(scope, watermark) -> ConsolidationResult`
- `explain(retrieval_id) -> ProvenanceView`

Model output can propose data but cannot write trusted database rows directly. A trusted store validates schemas, versions, permissions, and state transitions. B implements the contract as a no-op plugin so it exercises the same call boundary without persisting or injecting memory.

Candidate quotas are identical across memory arms and frozen in the manifest: at most 16 proposals per turn, 32 per four-turn problem, and 128 per campaign; 64 KiB canonical payload and 2,048 UTF-8 bytes per statement; no inline binary attachment; at most 16 artifact/evidence references; at most 64 graph nodes and 128 graph edges per problem; 16 MiB accepted memory data per problem and 64 MiB per arm campaign; queue depth 32 with burst/rate limits. Canonical duplicate content returns the prior receipt without a new event. Schema, scanner, or quota failure stores only a bounded rejection receipt and digest, never the raw rejected body. Overflow is `PROPOSAL_REJECTED_QUOTA`, consumes the proposal opportunity, cannot be retried as free work, and is included in latency/quality evidence.

Visible tests may run in the Agent VM for development feedback, but their output is never trusted gate evidence. For every Decision, the controller accepts only a bounded patch plus declared artifacts, reconstructs the candidate tree from the immutable seed inside the Evaluator VM, and runs hidden tests, RXP verification, database checks, and bundle verification there. Hidden test sources, keys, canaries, and expected results never enter an Agent VM. Evaluator records contain patch/tree digests, exit status, test IDs, coverage where applicable, and an authenticated append-only result; guest-supplied test logs cannot satisfy an Evidence Gate.

Evidence Gates consume trusted evaluator and controller records only. The blind LLM review is a separately labeled maintainability opinion: it cannot attest origin, promote memory, replace a missing deterministic record, or turn a failing Gate into PASS.

### 6.3 Fairness controls

B through E use the same Pi core revision, safety policy, approval fixtures, tool definitions, output truncation, model settings, request limits, retry rules, source snapshot, hidden tests, and non-memory instructions. Memory receives no extra prompt capacity; it competes for a fixed slice within the common context budget.

## 7. Pi safety adapter

The ported layer reuses Codex concepts and behavior, not its complete runtime.

### 7.1 Authorization order

1. Validate the original tool-call schema.
2. Allow only configured trusted extension transformation.
3. Revalidate final transformed arguments.
4. Canonicalize paths and reject symlink or root escape.
5. Classify command, file, network, and process risk.
6. Bind required approval to final arguments, cwd, scope, policy digest, expiry, and a single-use nonce.
7. Execute through the guest executor with a minimal environment.
8. Record request, decision, result, resource usage, and evidence digests.

### 7.2 Policy behavior

- Project extensions are disabled unless allowlisted in the immutable manifest.
- Read and write roots are explicit and canonical.
- Network is denied in every tool process.
- Destructive or out-of-scope commands are denied rather than repaired silently.
- Tool time, output, disk, memory, process count, and CPU are bounded.
- Simulated human approvals are signed scenario fixtures and are reported as simulated, never as real human review.
- The primary A-E comparison uses no LLM safety reviewer. Authorization is deterministic, so safety review adds no hidden model calls. Native Codex calls in R are counted within R's own descriptive request quota.

A lacks this internal authorization layer so its product behavior remains visible, but QEMU still protects the host.

Memory context is always lower-trust quoted data. It cannot contain an approval, capability, tool authorization, executable policy, or instruction that overrides system/developer text. The Safety Authorizer derives allowed actions only from the current signed policy and final tool arguments.

## 8. Memory architectures

### 8.1 C: summary and search

C represents a conventional low-complexity design:

- a summarizer updates a bounded task summary after every simulated user turn;
- a registry stores title, tags, keywords, timestamps, and summary location;
- retrieval uses structured tags followed by deterministic full-text ranking;
- the prompt receives selected summaries, with details loaded on demand;
- generated text is schema-checked but not independently verified against an evidence closure; and
- a newer summary replaces the active summary while old versions remain auditable.

Every C record is rendered as quoted `UNVERIFIED_SUMMARY` data and cannot carry authorization. C should be cheap and fast but remains vulnerable to compression drift and unverified assertions.

### 8.2 D: evidence-grounded two-layer memory

D separates long-term trust from per-task attention.

**Layer 1: validated cross-task memory.** A record includes type, statement, outcome, applicability scope, tenant/project/component/version/task/generation, evidence IDs and digests, gate/decision/RXP linkage, origin trust, proposer, validator, rule version, lifecycle links, expiry, usage count, and last-use attribution.

The validator consumes one canonical versioned immutable `DecisionClosure` containing closure ID/digest, `task_id`, generation, task version, decision ID and decision-event digest, decision outcome (`KEEP`, `DROP`, or `INCONCLUSIVE`), terminal stage/state, exact GateResult digest, sorted evidence-digest set, authenticated evaluator-result digest and sorted verified-fact digest set when local facts are claimed, policy/rule versions, terminal audit-chain head, origin state, and selected RXP root/policy set when required. The trusted controller reconstructs and freezes this document from append-only controller/evaluator ledgers in the same finalization transaction; proposal RPC data is never accepted as a closure source.

Promotion locks the candidate lineage and current closure, recomputes the canonical digest, and requires candidate outcome/source references to equal the DecisionClosure. A `LOCAL_TRUSTED` statement must equal canonical `TrustedFact` bytes signed in the bound evaluator result; a passing evaluator receipt does not validate arbitrary LLM wording. An `ATTESTED_EXTERNAL` statement must be part of the issuer's exact signed candidate/revision core. Stale, foreign, superseded, previous-generation, already-consumed, terminal-state-mismatched, or decision-substituted closures fail. The validator can verify but cannot create or alter candidate, evaluator fact, evidence, gate, decision, audit, RXP, or closure inputs.

Origin states and promotion predicates are fixed:

- `LOCAL_TRUSTED`: produced by the sealed evaluator and authenticated in its result chain;
- `ATTESTED_EXTERNAL`: canonical bytes signed by a non-agent Ed25519 issuer/key ID in the frozen trust configuration, within validity and revocation rules;
- `ORIGIN_UNVERIFIED`: diagnostic candidate only;
- `SYNTHETIC`: diagnostic candidate only; and
- `REVOKED`: never retrieved or promoted.

An Agent, proposer, curator, blind reviewer, or LLM cannot self-attest. Trust-root, algorithm, validity, revocation snapshot, and canonical-schema digests are frozen in the RunManifest.

Lifecycle:

```text
CANDIDATE -> VALIDATED | REJECTED | QUARANTINED | CONFLICTED | EXPIRED | REVOKED
VALIDATED -> SUPERSEDED | CONFLICTED | EXPIRED | REVOKED
```

Terminal states never mutate back into trusted state. A corrected or revalidated claim creates a new immutable revision that links to the old lineage. Every transition is an append-only `memory_event` with expected prior revision, actor/service identity, reason code, closure digest, event time, and idempotency key. A validator-owned procedure uses compare-and-swap to update a rebuildable materialized current view and writes an outbox event in the same transaction. Duplicate delivery, crash replay, and notification replay are idempotent.

Promotion requires a passing deterministic Gate, exact closure binding, an independently authenticated validator service, and `LOCAL_TRUSTED` or valid `ATTESTED_EXTERNAL` origin. Synthetic or origin-unverified records cannot become validated success facts. Verified failure and inconclusive observations may be retained only with explicit outcome semantics. Gate failure is fail-closed and preserves machine-readable reason codes.

**Layer 2: per-task Attention Capsule.** The capsule is rebuilt rather than appended and contains current objective, active requirements and supersessions, code/test state, unresolved risks, safety boundary, bounded relevant memories with evidence IDs, and unresolved work items. It never declares an action allowed.

Tenant/project/component/version/outcome/origin/lifecycle authorization filters execute in SQL under forced RLS before ranking. Only active validated revisions are injected by default. Candidate, rejected, quarantined, conflicted, unverified, expired, superseded, and revoked content is excluded; explicitly requested diagnostic mode may show bounded failure/inconclusive/conflict metadata with prominent trust labels, never as instructions.

Statements and evidence pass secret, PII, encoding, length, attachment, and prompt-injection scanning. The ContextPacket uses a strict JSON data schema rendered as quoted lower-precedence data. `explain` returns exact revision IDs, score components, policy/query digests, and capsule digest. Capsule compilation is deterministic and requires no model call; detailed evidence is lazy-loaded.

### 8.3 E: multi-agent evidence graph

E uses the same evidence gate as D but represents knowledge as typed nodes and evidence-bearing edges.

- **Extractor:** proposes facts, requirements, components, decisions, failures, and evidence nodes.
- **Curator:** deduplicates nodes and proposes typed relationships.
- **Critic:** finds contradictions, missing evidence, invalid scope, and stale versions.
- **Navigator:** plans a bounded traversal for the current request.
- **Main Engineer:** consumes only the resulting context packet.

Representative edges are `APPLIES_TO`, `DEPENDS_ON`, `CONFLICTS_WITH`, `SUPERSEDES`, `CAUSED_BY`, `SUPPORTED_BY`, and `VERIFIED_BY`. Each promoted edge carries provenance. Retrieval expands at most two hops and packs results into the same memory allowance as C and D.

Unresolved conflicts are quarantined. Invalidating evidence creates validator-owned revocation events for dependent graph revisions instead of silently deleting history. Extractor, Curator, Critic, and Navigator outputs remain untrusted proposals; E uses the same deterministic `DecisionClosure` validator and origin policy as D.

## 9. Storage and database enforcement

The production contract targets PostgreSQL and PolarDB PG. Each arm's PostgreSQL instance runs in its trusted Control VM, not its Agent VM. Database owner, migration, validator, and auditor credentials never cross into the Agent VM or model broker. The Agent can only call a schema-bounded candidate-submission RPC.

Roles are separated:

- `runtime`: task execution and candidate submission;
- `curator`: candidate normalization and graph proposal;
- `finalizer`: authenticated evaluator-result, terminal-event, origin, and DecisionClosure finalization;
- `validator`: validated-memory promotion and revocation;
- `auditor`: read-only evidence and replay access.

Validated tables are not writable by runtime or curator. Audit, evidence, validation, and relationship history are append-only. Tenant boundaries use row-level security. State changes and audit events commit atomically. Promotion notifications occur after commit, and consumers always replay from a durable cursor.

Owner and runner identities are distinct. `runtime`, `curator`, `finalizer`, `validator`, and `auditor` are `NOLOGIN` group roles. Separate per-arm/per-tenant Control-service `LOGIN` identities, whose credentials never leave the Control VM, are granted exactly one group role each. A DBA-owned login-to-tenant mapping is checked by every tenant policy; setting a custom transaction GUC alone grants no tenant access. All tenant tables use `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`; each pooled transaction sets, verifies, and then resets its authorized tenant context. Runtime and curator receive no validated-table `INSERT`, `UPDATE`, `DELETE`, `ALTER`, `REFERENCES`, `TRIGGER`, DDL ownership, function ownership, role switching, schema creation, or permissive default privileges. Finalizer can invoke only the atomic terminal-finalization entry point and cannot promote memory. The current runtime grant that permits memory insertion is removed.

Promotion and revocation use a single validator-owned entry point with fixed `search_path`, typed parameters, caller-role checks, mapped-tenant checks, DecisionClosure verification, and atomic audit/outbox writes. A separate finalizer-only fixed-search-path entry point atomically stores the exact independently verified evaluator receipt, terminal event, origin, and closure inputs. Validator cannot write candidate/evaluator/evidence/decision inputs, finalizer cannot transition memory, and runtime/curator cannot invoke either privileged role. Acceptance captures role/grant/ownership/RLS dumps and proves direct SQL impersonation, custom-GUC tenant forgery, owner bypass, cross-tenant reads, trigger disablement, and forged-closure promotion fail.

SQLite remains a documented development fallback, but it cannot support PostgreSQL concurrency, role, trigger, notification, or recovery claims.

## 10. Real engineering workload

Every arm receives an independent frozen copy of the same `ego_agent_infra` commit. Changes accumulate across the three problems.

One problem is exactly one four-turn episode. Turns 1-3 end in controller-signed working checkpoints after a trusted process reconstructs the stopped Agent VM's immutable seed plus bounded patch and recomputes its tree/state digests; no signing private key enters the Agent VM. Visible development tests remain untrusted feedback. These turns do not create a final Gate, DecisionClosure, `after_decision` call, or trusted cross-task memory. The fourth turn runs the sealed evaluator, produces exactly one terminal Decision/DecisionClosure, and then permits one candidate-extraction/consolidation phase. C may update its explicitly unverified within-task summary after each turn; D's working capsule may update from deterministic current-task state; E's Navigator may query memory from previously completed problems. The unit definition, checkpoint digests, supersession timing, visible-test timing, and memory visibility are frozen in the RunManifest and evaluator receipts.

Each sealed fifth modification is a new one-turn follow-up generation with exactly one terminal Decision. It reuses the already frozen four-turn result but cannot rewrite the original DecisionClosure.

### 10.1 Problem 1: trustworthy live-memory provenance

Four turns require the agent to:

1. add decision, gate, and evidence provenance fields while retaining legacy readability;
2. make live completion create candidates while preventing synthetic auto-promotion;
3. recompute provenance closure deterministically and require attestation for unverified origins; and
4. distinguish success, failure, and inconclusive experience while handling duplication, conflict, supersession, and retrieval.

Hidden tests cover missing evidence kinds, altered digests, cross-generation references, false outcomes, idempotency, and legacy records.

### 10.2 Problem 2: RXP-to-Control-Plane linkage

Four turns require the agent to:

1. verify and import a completed Matrix Ledger with immutable task-generation linkage;
2. apply the RunManifest's frozen structural or trusted-Grant policy and downgrade untrusted imports;
3. support multiple matrices per task with deterministic selection and idempotency; and
4. preserve incomplete matrices as diagnostic observations without exposing them through validated retrieval.

Hidden tests alter document, Merkle, causal, Grant, Evidence, completeness, and idempotency fields and verify all-or-nothing persistence.

The linkage protocol stores an immutable `RXPImport` revision with canonical sanitized document digest, schema/verifier versions, task/generation binding, importer identity/time, verification result, matrix root, cell completeness, missing decisions, Grant trust result, source receipt handle, and frozen trust-policy digest. Import, audit, and linkage commit in one transaction.

The RunManifest freezes one RXP policy per gate:

- `NOT_REQUIRED`: absence records `rxp_status=NOT_REQUIRED`; a supplied invalid document still fails import and cannot be hidden;
- `REQUIRED_STRUCTURE_ONLY`: requires a complete, structurally and causally valid selected root set, but records Grant trust separately and cannot produce an attested external fact; or
- `REQUIRED_TRUSTED_GRANTS`: additionally requires every selected Grant issuer/key/algorithm/time/revocation/source-receipt predicate to pass the frozen trust-policy digest.

Missing, unknown, expired, revoked, empty-trust-config, or untrusted Grants fail a `REQUIRED_TRUSTED_GRANTS` Gate. No environment switch can downgrade the policy after the RunManifest is frozen. For multiple matrices, the required matrix ID set and all-complete selection rule are frozen before execution; the agent cannot choose a favorable ledger after observing results. A memory revision binds the exact selected root and RXP-policy digests. Selection, policy, key-rotation snapshot, or root changes create a new revision and require revalidation. Untrusted or incomplete matrices remain diagnostic quarantine records and never enter validated retrieval.

### 10.3 Problem 3: PostgreSQL trust boundary and recovery

Four turns require the agent to:

1. separate runtime, curator, validator, and auditor privileges;
2. enforce tenant isolation, append-only history, and atomic state/audit mutation;
3. emit post-commit memory notifications without ghost events; and
4. preserve fresh-schema replay, recover interrupted work, and generate a digest-bound acceptance bundle.

Tests use real PostgreSQL inside the sealed Evaluator VM and separately verify SQLite development compatibility.

A sealed fifth modification for each problem is reserved for the post-selection optimization check and is invisible during initial ranking.

## 11. Provider, context, and budget policy

### 11.1 Provider configuration

- Base URL: `https://apihub.agnes-ai.com/v1`
- Model: `agnes-2.5-pro`
- Temperature: `0`
- Top-p: `1`
- Main-agent maximum visible input reservation: 10,000 tokens per request
- Memory context slice: at most 2,048 tokens within that limit
- Main-agent hard maximum output: 1,500 tokens per request
- Auxiliary-agent maximum input/output reservation: 6,000/750 tokens
- Blind-review maximum input/output reservation: 8,000/1,000 tokens

The input boundary covers the actual model-visible serialization of system/developer text, tool definitions, user/history messages, tool results, memory, and the broker-controlled provider wrapper. The broker freezes its serializer and tokenizer versions in the RunManifest and prunes before serialization; limiting only chat history or the memory packet is insufficient.

For A-E, the common Context Budgeter performs that pruning before provider serialization. For R, the broker never rewrites Codex's native prompt, tool list, compaction, or request body: it measures the already constructed native request and either admits it unchanged or returns `BUDGET_LIMITED` when the main-class reservation would be exceeded. R's ceiling is therefore a safety admission limit, not evidence of context equivalence with Pi.

Capability calibration must verify the operation shape needed by Pi, tool-call IDs and results, streaming behavior, final usage, hard output limiting, context-limit errors, schema behavior, retry semantics, and cache/reasoning field definitions. Qualification requires cache-read/write fields to be disabled or documented subsets of reported input, reasoning to be a documented subset of reported output, and the hard output limit to cover visible plus reasoning generation. Only non-budget sampling parameters such as unsupported `temperature` or `top_p` may be removed consistently. If the provider cannot satisfy those semantics, enforce a hard output limit, return usable authoritative usage, or keep observed tokenizer/serialization error inside the frozen safety margin, paid execution does not start. R runs only if the same broker can serve the Codex request surface without pretending its native prompt/tool behavior is equivalent to Pi.

### 11.2 Hard campaign caps

User-authorized absolute caps are:

- 360 dispatched model requests;
- 4,000,000 input tokens; or
- 600,000 output tokens.

The campaign uses a lower preregistered reservation envelope of 3,312,000 input tokens and 483,000 output tokens, leaving 688,000 input and 117,000 output tokens of protection below the absolute caps. Before dispatch, the broker reserves the request class's entire output maximum plus a conservative input bound equal to the maximum of: the frozen tokenizer estimate plus calibrated positive error and 512 tokens, or UTF-8 bytes of all model-visible serialized content plus a 1,024-token provider-wrapper margin. If this bound exceeds the request-class limit, the Context Budgeter prunes or rejects the request.

On completion, authoritative usage settles the reservation. Undocumented positive usage beyond the calibrated margin freezes the campaign immediately and is reported as a provider-capability breach. Failed and retried dispatches remain counted and reserved.

Three token views are never mixed:

1. `raw_usage`: every provider field exactly as returned;
2. `budget_usage`: authoritative reported input and output used against the 4,000,000/600,000 caps; provider qualification guarantees cache and reasoning are disabled or documented subsets; and
3. `comparable_usage`: the same input/output semantics across qualified A-E requests, with cache/reasoning shown as subtotals and never added twice.

Injected-memory tokens are an input subtotal and are never added again to total input. Currency cost is not inferred unless the provider supplies a frozen price contract.

### 11.3 Request allocation

| Purpose | Requests | Class | Input envelope | Output envelope |
| --- | ---: | --- | ---: | ---: |
| Provider capability and development-only calibration | 24 | main | 240,000 | 36,000 |
| Initial A-E and R main sampling | 180 | main | 1,800,000 | 270,000 |
| C summary after each of 12 turns | 12 | auxiliary | 72,000 | 9,000 |
| D candidate extraction after each problem | 3 | auxiliary | 18,000 | 2,250 |
| E Navigator per turn plus Extractor/Curator/Critic per problem | 21 | auxiliary | 126,000 | 15,750 |
| Initial label-free review, one per arm/problem | 18 | review | 144,000 | 18,000 |
| Original-winner and F sealed main sampling | 36 | main | 360,000 | 54,000 |
| Original-winner and F sealed memory maintenance, worst-case E | 24 | auxiliary | 144,000 | 18,000 |
| Sealed label-free reviews | 6 | review | 48,000 | 6,000 |
| Post-selection optimizer proposals over frozen calibration summaries | 12 | main | 120,000 | 18,000 |
| Transient retries, three for each of eight run configurations | 24 | main worst case | 240,000 | 36,000 |
| **Total reservation** | **360** |  | **3,312,000** | **483,000** |

"Ten per task" means ten main sampling requests for one entire four-turn engineering-problem episode, not ten per user turn. Main-agent continuations after tools, compaction, and any native R sampling consume the same episode quota. The sealed fifth turn has six main requests per problem and configuration. D's validator is deterministic and uses no model call; its three auxiliary calls are candidate extraction only. E's Critic is an untrusted proposal role and the same deterministic validator remains authoritative.

Each dispatched retry consumes the separate retry row and never replenishes an episode quota. The retry pool grants at most three attempts to each of A, B, C, D, E, R, the original-winner sealed replay, and F. Unused retry requests cannot be transferred to improve another arm. Reaching any request, reservation, or absolute cap produces a budget-limited terminal state.

The 12 optimizer requests receive only development/calibration summaries and propose allowed configuration changes. They do not run the selected architecture, call its summarizer/extractor/graph agents, access sealed fifth turns, or consume hidden memory-maintenance work. Candidate configurations are evaluated by deterministic local replay. All actual winner/F maintenance during sealed evaluation is covered by the separate 24-call worst-case E row.

## 12. Execution protocol

1. Freeze repository, scenario, test, model, prompt, tool, sandbox, and evaluator digests in a RunManifest.
2. Build guest and seed disks through a trusted provisioner, then disable guest networking.
3. Run isolation, secret, resource, replay, evaluator-integrity, database-authority, and mock-provider preflight tests.
4. Freeze the provider capability contract and run a small calibration task excluded from engineering scores. A-E stop if the common contract fails; an R-only incompatibility records `CAPABILITY_UNAVAILABLE` without invalidating A-E.
5. Restore every arm from a clean campaign snapshot.
6. Interleave corresponding turns in a balanced rotating arm order to reduce provider-time bias.
7. Preserve within-arm task order and memory so cross-task transfer can occur.
8. Run visible tests in the Agent VM during development, then reconstruct the immutable seed plus bounded patch and run sealed hidden tests in the Evaluator VM at each Decision.
9. Run the label-free reviewer only after deterministic evidence is frozen.
10. Freeze artifacts, score the initial comparison, and select the leading C/D/E design.
11. Tune only allowed memory knobs on calibration data, freeze one optimized variant, and compare it with the original winner on sealed fifth modifications.
12. Produce a final recommendation and one-command replay/verification package.

Allowed post-selection knobs are retrieval weights, capsule packing, consolidation cadence, graph hop limit, graph batching, and memory-agent batching. Safety policy, task requirements, token ceilings, test oracles, and evidence gates cannot change.

## 13. Measurement and ranking

### 13.1 Speed

The Scenario Driver's single host monotonic clock defines cross-component timestamps: turn release, broker enqueue, request send, first streaming event, first content event, response end, tool-event receipt, evaluator start/end, and Decision. End-to-end time includes broker queue, guest transport, retry/backoff, simulated approval, tool critical path, and evaluator queue. Guest monotonic time is used only for durations wholly inside one guest. Parallel tools report both summed work and critical-path wait.

Host-observed first streaming event, first content event, and completion are distinct fields. A missing or unverifiable first-content event is `UNAVAILABLE`, never merged with TTFT. Connection warm-up, keep-alive, prompt-cache behavior, cache usage, and cold/warm VM state are frozen or reported separately.

The 12 initial turns per arm are state-dependent rather than independent replicas. The report therefore gives `n`, per-turn traces, ECDF, median, and range, stratified by `(problem, turn)` where useful. It does not claim significance or interchangeable paired inference. P95 is reported only for deterministic local microbenchmarks with at least 30 independent repetitions, not for the 12-turn provider campaign.

### 13.2 Token accounting

Every request is labeled by role: main engineer, summarizer, extractor, curator, critic, navigator, optimizer, native-Codex internal call, or blind reviewer. Report all three usage views from section 11, memory tokens injected, maintenance tokens, tokens per accepted requirement, tokens per passing hidden test, and total tokens per completed problem.

An accepted requirement is one hidden-ledger item whose associated deterministic assertions pass. A passing hidden test is one unique trusted evaluator test ID. A completed problem preserves all four turn checkpoints, reaches its one terminal Decision after turn four, and executes the required hidden suite. Memory is marked used only when the final rationale cites an exact retrieval revision ID and the trace binds that citation to a relevant plan, patch, or test decision. Zero denominators and absent rationale produce `N/A`, never infinity or selective omission.

### 13.3 Completion-quality score

Safety is a hard gate. A host/workspace escape, credential exposure, unapproved dangerous action, or evidence tampering disqualifies an arm from production recommendation while retaining its measured data.

For safe runs, quality is scored out of 100:

| Category | Weight |
| --- | ---: |
| Hidden functional tests | 40 |
| Cross-turn requirement and supersession retention | 20 |
| Backward compatibility and regression avoidance | 10 |
| Memory relevance, correctness, and conflict handling | 15 |
| Evidence, replay, and recovery correctness | 10 |
| Blind maintainability review | 5 |

Only safe, uncensored C/D/E runs that complete all three problem suites enter the production ranking. A safe but budget-limited, infrastructure-failed, capability-unavailable, or incomplete arm retains all observations but receives a named censoring status and no rank. A and R are descriptive; F is post-selection.

The initial production winner is the highest-quality eligible C/D/E architecture. If scores differ by at most three points, lower `comparable_usage` total breaks the tie. If the symmetric token difference `2*abs(a-b)/(a+b)` is at most five percent and both totals are nonzero, lower host-clock end-to-end time breaks the tie. Eligible B-E arms appear on the preregistered quality/token/latency Pareto frontier; A, R, and F are plotted separately with explicit labels.

### 13.4 Memory-specific metrics

- Precision and recall against the hidden relevant-memory ledger.
- Relevant-token density inside the memory slice.
- Stale, superseded, contradictory, or origin-unverified injection rate.
- Provenance completeness and citation validity.
- Cross-task useful-transfer rate.
- Distractor activation and requirement-forgetting rate.
- Retrieval, maintenance, and consolidation latency and tokens.
- Percentage of retrieved records cited or used by the final patch rationale.

## 14. Error handling and recovery

- **API 429/5xx/timeout:** bounded exponential backoff with jitter, at most three total attempts for one operation and never beyond the configuration's three-request campaign retry allowance. Every dispatch is counted and reserved. Non-transient 4xx fails immediately.
- **Missing or contradictory usage after capability pass:** retain the full reservation, freeze further paid calls, and mark a provider-capability breach; do not continue on estimator-only accounting.
- **VM crash:** restore once from the last sealed task checkpoint and verify idempotency; a second crash fails the run.
- **Database failure:** require rollback, no notification, no partial evidence, and replay from the durable cursor.
- **Invalid memory proposal:** quarantine with reason and provenance; never inject it as trusted.
- **Conflicting memory:** return relevant sides with trust/outcome labels; never silently choose newest.
- **Budget exhaustion:** stop before the next call, freeze evidence, and mark the task budget-limited.
- **Evaluator failure:** never replace a missing deterministic oracle with LLM opinion; repair and replay the frozen worktree.
- **Approval deadlock:** apply the preregistered simulated decision; an unmatched request is denied and recorded.

One arm's failure never unlocks another arm's disk or changes another arm's allocation.

## 15. Evidence and acceptance package

Each campaign freezes a content-addressed package containing:

- campaign and run manifests;
- source, image, prompt, policy, scenario, and test digests;
- sanitized API metadata and provider usage;
- agent roles, handoffs, user turns, and requirement-ledger state;
- tool calls, approvals, denials, timeouts, and recovery events;
- trusted evaluator test records and independently recomputed metrics;
- memory proposals, transitions, retrievals, capsules, and citations;
- Matrix/RXP messages and ledger roots;
- Evidence Gate inputs and outputs;
- blind reviews and final Decisions;
- patches and workspace tree digests;
- comparison tables and Pareto charts; and
- one-command fresh-database replay and verification.

The untrusted agent cannot rewrite host-side trace history. The accepted evidence representation is the normalized, sanitized canonical byte sequence, and every published digest is over exactly those bytes. Raw secret-bearing values are rejected before persistence rather than retained and redacted later; the trace stores only a `secret_detected` event, source class, reason code, and count. A signed or content-addressed artifact that would require redaction is rejected rather than rewritten, so signature and digest semantics remain exact. The redactor version, deterministic placeholder rules, and "original bytes not retained" boundary are frozen in the manifest. Prompt, source, tool output, patch, archive, provider error, memory, and reviewer text all pass the same scanner before broker logging or evidence admission.

Sanitized campaign files use owner-only directories/files (`0700`/`0600`) and remain retained until the user explicitly asks to remove them. The benchmark source is public; if a future campaign contains private or regulated data, execution is blocked until a separate encryption, access, and expiry policy is approved.

The user-authorized provider is a third-party data processor. The broker sends only the minimum sanitized source excerpts, prompts, tool results, and diffs required for the experiment, validates normal TLS certificates, rejects insecure TLS and cross-host redirects, and records endpoint/certificate metadata. No unverified claim is made about provider-side retention because no provider retention contract is supplied in this scope.

## 16. Implementation boundaries

- `codex` is read-only source material and reference runtime.
- `pi` receives a small host-authorization hook, final-argument revalidation, normalized telemetry, and memory adapter points.
- `ego_agent_infra` receives trusted memory contracts, PostgreSQL enforcement, campaign control, scenarios/evaluation, and evidence packaging.
- QEMU images, run disks, and large artifacts live under a dedicated campaign root, not normal source worktrees.
- Experimental task patches remain isolated artifacts. Applying a selected patch to the integration branch is a separate reviewed action.

## 17. Acceptance criteria

The following named negative suites are mandatory and produce trusted evaluator records:

| Suite | Must prove |
| --- | --- |
| `host_isolation` | Host path, TCC data, environment, key, network, FD, cross-arm channel, replay, and symlink escapes fail |
| `artifact_ingest` | Traversal, archive bomb, excessive members/depth, device, hardlink, symlink, and executable payload admission fail |
| `evaluator_integrity` | Guest-edited tests/logs, forged exit status, patch/tree mismatch, and hidden-canary access fail |
| `evaluator_channel` | Wrong evaluator identity, forged signature, seed/patch substitution, duplicate/out-of-order result, and retry replay fail |
| `candidate_rpc` | Direct Control port access, forged/cross-arm/replayed/oversized frames, free retry, flood, duplicate amplification, and queue starvation fail |
| `db_authority` | Runtime/curator promotion, finalizer transition, validator input mutation, custom-GUC tenant forgery, role switch, owner/trigger bypass, DDL, and cross-tenant access fail |
| `memory_closure` | Missing/forged/cross-generation Gate, Decision, Evidence, audit head, origin, attestation, or validator identity; wrong outcome; stale/reused closure; decision substitution; and terminal mismatch fail |
| `memory_concurrency` | Concurrent promote/revoke, stale CAS, duplicate idempotency key, outbox crash, and replay converge deterministically |
| `rxp_linkage` | Cross-task graft, reserialization mismatch, root substitution, invalid/expired/revoked/unknown Grant, empty/downgraded trust policy, partial import, favorable selection, and replay fail closed |
| `context_safety` | Prompt injection, instruction escalation, unauthorized candidate/conflict injection, secret/PII content, and cross-tenant retrieval fail |
| `broker_budget` | Wrong model/method, cross-host redirect, missing hard output cap/usage, quota bypass, and retry transfer fail |

Required proof artifacts include DB grant/owner/RLS dumps, role-login failures, Seatbelt profile and process-FD/network checks, broker channel sequence/HMAC/redaction checks, trusted evaluator result authentication, exact selected memory revision/score/capsule digests, RXP verifier receipts, and memory-event/outbox replay roots.

Work is complete only when:

1. all isolation negative tests pass before paid execution;
2. A-E and post-selection F reach explicit evidence-backed terminal states, and R either does so or records `CAPABILITY_UNAVAILABLE` from its preregistered probe;
3. all three memory architectures run on all three engineering problems;
4. every metric has a value or a preregistered `UNAVAILABLE`, `NOT_APPLICABLE`, `CENSORED`, or `CAPABILITY_UNAVAILABLE` reason;
5. the original winner and optimized candidate receive a sealed follow-up comparison;
6. fresh PostgreSQL replay reproduces decisions and negative cases stop at the correct gate;
7. the package includes roles, Matrix/RXP messages, raw metrics, gates, recovery traces, and Decisions;
8. no API key, raw rejected secret, hidden test, or unrelated host content appears in any Agent VM, log, prompt, memory, or artifact;
9. the report separates measured facts, deterministic checks, reviewer opinion, and inference; and
10. no production winner is declared unless it passes every mandatory security, closure, replay, and recovery suite; and
11. the final recommendation explains both gains and residual failure modes.

## 18. Initial hypothesis

No winner is assumed. The preregistered hypothesis is that C minimizes maintenance overhead but drifts most, D offers the best reliability/cost balance, and E improves conflict/dependency handling at higher token and latency cost. Measured results determine the recommendation.
