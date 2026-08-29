# RXP/1 — Reproducible eXperiment Protocol

Status: executable reference specification, version `1.0`
Scope: experiment commitment, authorization, evidence acceptance, and omission proof
Reference implementation: [`protocols/rxp/`](../../protocols/rxp/)

RXP is a transport-independent protocol for turning an experiment matrix into a set of
machine-verifiable commitments. It binds this chain:

```text
MatrixPlan → Intent → one-time Grant → Receipt → Evidence set → Decision
     │                                                        │
     └──────────── append-only MatrixLedger + missing list ───┘
```

The narrow goal is not to make stochastic science magically deterministic. RXP makes
the *claim boundary* deterministic: what was planned, what was authorized, which bytes
were produced, which evidence was reviewed, what remains missing, and exactly which
inputs justify a decision.

The words MUST, MUST NOT, SHOULD, and MAY below are normative.

## 1. What RXP is — and is not

| Adjacent system | Its job | RXP relationship |
|---|---|---|
| MCP | Model-facing tools, resources, prompts, and transport sessions | An MCP tool MAY accept or emit RXP documents. RXP does not discover or invoke tools. |
| A2A | Heterogeneous Agent message/task/artifact lifecycle | A2A MAY carry RXP documents and their digests. RXP does not define Agent messaging or delegation. |
| AgentTeams / Matrix | Multi-Agent collaboration and observation plane | Team roles MAY create RXP documents. RXP does not replace rooms, identities, or orchestration. |
| Skill | Reusable instructions and workflow knowledge | A Skill MAY explain how to produce evidence. It cannot waive an RXP state transition. |
| W3C PROV-O | General Entity/Activity/Agent provenance vocabulary | RXP documents have an optional, lossless-enough PROV mapping. RXP adds experiment-specific authorization and acceptance invariants rather than reinventing general provenance. |
| MLflow-style tracking | Log runs, parameters, metrics, and artifacts | A tracker MAY store RXP artifacts or URIs. RXP freezes a complete matrix, consumes per-cell authorization, gates independent evidence, and proves omitted decisions; it is not a tracking UI. |

RXP's distinct contribution is **executable experiment commitment and acceptance**:

1. freeze the complete Cartesian matrix before execution;
2. bind one Intent and one scoped, bounded, expiring Grant to each cell;
3. consume that Grant once when accepting the execution Receipt;
4. commit raw Evidence as a Merkle set;
5. require an independent review before Decision;
6. expose every expected cell without a Decision in the signed/hashable ledger view.

RXP does not standardize schedulers, model APIs, artifact storage, authentication
transport, distributed consensus, or scientific metric choice.

## 2. Normative documents

Every protocol document rejects undeclared fields. Committed Draft 2020-12 JSON Schemas
are generated from and checked against the executable models:

| Document | Contract | Required invariant |
|---|---|---|
| `MatrixPlan` | [`rxp-matrix-plan-v1.schema.json`](../../protocols/rxp/schemas/rxp-matrix-plan-v1.schema.json) | Sorted axes and cells enumerate the complete Cartesian product. |
| `Intent` | [`rxp-intent-v1.schema.json`](../../protocols/rxp/schemas/rxp-intent-v1.schema.json) | Exact cell coordinates, action/payload digest, manifest, resource request, and determinism floor. |
| `Grant` | [`rxp-grant-v1.schema.json`](../../protocols/rxp/schemas/rxp-grant-v1.schema.json) | Signed exact Intent digest, matrix/cell, action/scope, bounds, nonce, issue time, expiry, and minimum determinism. |
| `Receipt` | [`rxp-receipt-v1.schema.json`](../../protocols/rxp/schemas/rxp-receipt-v1.schema.json) | Exact Intent and Grant parents, actual bounded usage, output byte digest, and determinism evidence. |
| `Evidence` | [`rxp-evidence-v1.schema.json`](../../protocols/rxp/schemas/rxp-evidence-v1.schema.json) | Content-addressed artifact and exact Receipt parent. |
| `Decision` | [`rxp-decision-v1.schema.json`](../../protocols/rxp/schemas/rxp-decision-v1.schema.json) | Passing gate, full sorted evidence set and Merkle root, Receipt/Intent parents, and deterministic rationale code. |
| `MatrixLedger` | [`rxp-matrix-ledger-v1.schema.json`](../../protocols/rxp/schemas/rxp-matrix-ledger-v1.schema.json) | Append-only root, derived cell snapshots, expected/decided counts, and sorted missing decisions. |

`kind` and `rxp_version` are part of every top-level digest. An implementation MUST NOT
silently coerce another version into `1.0`.

JSON Schema validation is necessary but not sufficient: Cartesian completeness,
cross-document parent equality, state transitions, signatures, Merkle recomputation,
and append-only roots are enforced by the reference verifier.

## 3. RXP-CJ/1 canonical bytes

All document hashes and HMAC signatures use the same canonical form:

1. JSON object keys MUST be strings, normalized to Unicode NFC, and sorted by Unicode
   code point. A normalization collision is invalid.
2. String values are normalized to NFC.
3. Arrays preserve order. Semantically unordered fields such as `evidence_digests` MUST
   already be sorted by the document model.
4. Integers MUST fit signed 64-bit range.
5. Binary floating point is forbidden. Use scaled integers (for example
   `score_milli=741`) or explicitly typed decimal strings.
6. NaN and infinity are forbidden. Undeclared types and non-string map keys are invalid.
7. JSON is UTF-8, `ensure_ascii=false`, with no whitespace and separators `,` and `:`.
8. A canonical document has no trailing newline. CLI stream output adds one newline
   outside the document bytes.

Document digest:

```text
sha256( UTF8("RXP/1/document\\0") || RXP-CJ/1(document) )
```

Opaque artifact digest is ordinary `sha256(artifact_bytes)`. Both are serialized as
`sha256:<64 lowercase hex>`, but their meanings are not interchangeable.

The executable known vector is in
[`test_canonical_and_cli.py`](../../tests/protocols/test_canonical_and_cli.py).

## 4. Frozen matrix and omission proof

`MatrixPlan.axes` MUST be sorted by axis name. `MatrixPlan.cells` MUST be sorted by
`cell_id`, have unique coordinate maps, and enumerate exactly the Cartesian product of
all declared axis values. A hidden or cherry-picked cell is rejected.

The first ledger entry MUST be `MATRIX_FROZEN`. An `Intent` is accepted only when its
`cell_id` and coordinates equal a declared cell. The ledger derives:

- `expected_cell_count` from the frozen plan;
- `decided_cell_count` from cells in `DECIDED` state;
- `missing_decisions = expected cell IDs − decided cell IDs`;
- `completeness = COMPLETE` only when `missing_decisions` is empty.

This is a completeness commitment, not proof that the chosen matrix is scientifically
adequate. The PI still owns axis choice and statistical design.

## 5. Per-cell state machine

```text
MATRIX_FROZEN
      │
      ├─ cell A: INTENT_RECORDED → GRANTED → RECEIPT_RECORDED
      │                                      → EVIDENCE_READY → DECIDED
      └─ cell B: INTENT_RECORDED → GRANTED → RECEIPT_RECORDED
                                             → EVIDENCE_READY → DECIDED
```

For one cell:

| Current state | Accepted document | Next state | Guard |
|---|---|---|---|
| absent | `Intent` | `INTENT_RECORDED` | cell is declared; coordinates match; Intent ID is new |
| `INTENT_RECORDED` | `Grant` | `GRANTED` | valid signature; exact scope/digests; bounds cover request; not expired |
| `GRANTED` | `Receipt` | `RECEIPT_RECORDED` | exact parents; execution began before expiry; usage within bounds; atomic grant consumption succeeds |
| `RECEIPT_RECORDED` | one or more `Evidence` | same or `EVIDENCE_READY` | exact Receipt parent; unique evidence ID; gate becomes ready only on PASS |
| `EVIDENCE_READY` | `Decision` | `DECIDED` | complete evidence digests/root and gate are recomputed; determinism floor holds |

No transition can skip a parent. A Decision is not accepted on a failed gate, including
an `INCONCLUSIVE` research verdict. Operational cancellation is outside RXP/1; it MAY be
logged by the orchestrator but MUST NOT masquerade as a research Decision.

## 6. Grant semantics

The shipped profile signs canonical `GrantClaims` using:

```text
HMAC-SHA256(key, UTF8("RXP/1/grant-signature\\0")
            || RXP-CJ/1({algorithm, key_id, claims}))
```

A Grant is valid only for one `intent_digest`, `intent_id`, `matrix_id`, `cell_id`,
`action`, `scope`, and `action_payload_digest`. Its resource maxima cover GPU count,
wall time, GPU time, and artifact bytes. TTL is 1–3600 seconds. Execution MUST begin
before `expires_at`; completion MAY occur later, but actual usage remains bounded.

The `grant_id` MUST be atomically consumed when its Receipt is committed. The reference
package includes:

- `InMemoryReplayRegistry` for one-process tests;
- `SQLiteReplayRegistry` for durable local, cross-connection uniqueness.

A distributed deployment MUST replace these with a serializable/linearizable store. A
cache with eventual consistency is insufficient. HMAC is a local shared-secret profile
and does not provide non-repudiation between key holders.

## 7. Receipt, evidence, and Decision causality

The normative parents are:

```text
Grant   ← Intent digest
Receipt ← Intent digest + Grant digest + Grant ID
Evidence← Receipt digest
Decision← Intent digest + Receipt digest + sorted complete Evidence digests
```

RXP/1's core gate requires `code`, `config`, `dataset_manifest`, `log`, `metric`,
`trace`, and `review`. Metric evidence MUST declare deterministic evaluation, MUST NOT
be summary-only, and MUST reference a raw-data digest. A PASS review MUST:

- name its own producer as `reviewer_id`;
- set `independent=true` and `verdict=PASS`;
- be produced by an identity that created no non-review evidence;
- cover every non-review producer.

Evidence digests are sorted, domain-separated as leaves, and reduced by duplicate-last
binary Merkle nodes. This commits to the complete unordered evidence set. Ledger entries
use a separate append-only root:

```text
entry_digest = sha256("RXP/1/ledger-entry\\0" || canonical(entry_core))
root[n]      = sha256("RXP/1/ledger-root\\0" || root[n-1] || entry_digest)
```

The chain detects deletion, reorder, and mutation only when a trusted prior/final root
is retained elsewhere. RXP/1 is not a public transparency log and supplies no trusted
timestamp service.

## 8. Determinism levels

Levels are ordered and are evidence claims, not marketing labels:

| Level | Minimum meaning |
|---|---|
| `D0_UNVERIFIED` | A run was observed; replay guarantees are absent. |
| `D1_INPUTS_BOUND` | Commit, config, dataset manifest, environment lock, base model, seed, and artifacts are content-addressed; no claim is made that the executor honored them. |
| `D2_SEEDED_ENV_BOUND` | D1 plus an execution claim that the frozen seed and locked environment were applied. This still does not guarantee deterministic GPU kernels. |
| `D3_BYTE_REPLAY_VERIFIED` | At least two executions of the frozen transform produced the same output bytes; `replay_digest == output.sha256`. |

Every Intent contains a required level; Grant sets a minimum; Receipt reports achieved
level; Decision MUST use the Receipt level. Each downstream level MUST be at least the
upstream requirement. The synthetic CLI reaches D3 for a pure canonical transform only.
It does **not** claim byte-identical replay for arbitrary training workloads.

## 9. `approval-token-v1` migration

RXP does not reinterpret `egoap1` bytes as a Grant. The one-way adapter
`migrate_consumed_approval_v1` requires this sequence:

1. Validate and atomically consume the legacy token with the existing
   `HMACApprovalManager`.
2. Freeze its JTI, action digest, config digest, and token SHA-256 in the Intent's
   `approval_v1_binding` (never the raw token).
3. Call the adapter with the validated claims and a separate atomic migration registry.
4. Issue a freshly signed RXP Grant whose `legacy_approval_v1` binding exactly matches.
5. Consume that new RXP Grant once when its Receipt is accepted.

The adapter rejects action, scope, config, binding, and second-migration mismatches. A
deployment needs both atomic stores during migration; copying decoded claims without
first consuming a valid legacy token is unsafe.

## 10. Optional PROV-O projection

This projection is for interoperability, not the normative RXP verifier:

| RXP object | PROV-O shape |
|---|---|
| `MatrixPlan`, `Intent`, `Grant`, `Receipt`, `Evidence`, `Decision` | `prov:Entity` |
| physical execution named by a `Receipt` | `prov:Activity` |
| `actor_id`, `issuer_id`, `executor_id`, `producer_id`, `decided_by` | `prov:Agent` |
| Receipt → Intent/Grant | `prov:used` / `prov:wasInfluencedBy` |
| Evidence → Receipt | `prov:wasDerivedFrom` |
| Decision → Evidence | `prov:wasDerivedFrom` |
| actor fields | `prov:wasAttributedTo` or `prov:wasAssociatedWith` |

PROV tooling can query that graph. RXP verification MUST still use canonical RXP
documents, exact parent fields, state guards, signature policy, and evidence gate.

## 11. Stable Python and CLI surface

Python:

```python
from protocols.rxp import (
    GrantSigner,
    MatrixLedger,
    SQLiteReplayRegistry,
    canonical_bytes,
    digest_document,
    evidence_gate,
    verify_ledger_document,
)
```

The constructors and document models in `protocols.rxp.models` are the benchmark-facing
API. CLI commands are deliberately transport-free:

```bash
# Same fixed input emits byte-identical complete output.
python -m protocols.rxp demo -o /tmp/rxp-a.json
python -m protocols.rxp demo -o /tmp/rxp-b.json
cmp /tmp/rxp-a.json /tmp/rxp-b.json

# Structural verification; fixture flag additionally checks both Grant signatures.
python -m protocols.rxp verify /tmp/rxp-a.json --demo-key

# Canonical document digest and schema drift check.
python -m protocols.rxp hash path/to/document.json
python -m protocols.rxp schema --check
```

`verify` without a key verifies schemas, document/entry/root hashes, causal links, cell
transitions, gate recomputation, Merkle evidence commitments, and matrix completeness.
Signature trust is separate because key resolution is deployment policy.

## 12. Conformance and honest limits

An RXP/1 implementation is conformant only if it passes equivalent checks for:

- the canonical and Merkle known vectors;
- byte-identical fixed-input replay;
- complete Cartesian matrix validation and missing-cell reporting;
- scope, signature, expiry, resource, and determinism rejection;
- atomic replay rejection under concurrency;
- document, causal-parent, entry, and root tampering;
- all seven evidence kinds, raw metrics, and reviewer independence;
- one-way, single-use `approval-token-v1` migration;
- committed JSON Schema drift.

The shipped tests exercise those claims under `tests/protocols/`. They do not establish
distributed consensus, remote identity assurance, artifact availability, scheduler
sandboxing, hardware determinism, or scientific validity. Those remain deployment and
research-method responsibilities.
