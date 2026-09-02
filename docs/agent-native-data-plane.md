# Agent-native data plane and focus-memory protocol

## Architecture decision

The previous SQLite-only story is now split into three explicit layers:

| Layer | Production target | Responsibility | Current no-credential state |
|---|---|---|---|
| transactional authority | TDSQL Nexa SQL endpoint | tasks, state transitions, approvals, evidence, RXP tokens, immutable trace | `NOT_CONFIGURED` |
| contextual memory | TencentDB Agent Memory v3 | isolated L0 conversation, L1 atomic facts, L2 scenarios, L3 core memory, archive/compact | `NOT_CONFIGURED` |
| deterministic projection | one SQLite + one `FOCUS.md` per agent | offline replay, human-readable current attention, recovery projection | `LIVE_LOCAL` |

TDSQL Nexa and TencentDB Agent Memory are not synonyms. The first is the data
authority; the second is the memory engine. SQLite remains a development and
replay backend only, and the health response says so.

## Phase-commit protocol

`POST /api/v1/research/agents/{agent_id}/stages/commit` is the only phase close
operation. It requires explicit `team_id`, `agent_id` (path), `user_id`,
`session_id`, `task_id`, `stage_id`, messages, and at least one decision,
evidence item, blocker, or next action.

For every accepted phase:

1. append the full L0 messages and structured outcome into that agent's private
   SQLite transaction;
2. bind the payload digest to the previous phase receipt;
3. derive a bounded `FOCUS.md` containing only validated facts, decisions,
   evidence, blockers, and next actions;
4. atomically replace the Markdown projection and return both digests;
5. when TencentDB Agent Memory is configured, call its published v3 Skill
   `conversation/add`, then `conversation/force-archive` using all isolation
   coordinates and retain the provider response.

This is automatic when the provider is configured. `sync_remote=false` is an
operator-visible diagnostic override; it must not be used in production
acceptance.

The local file path is derived from a sanitized agent name plus a hash, so
path traversal and two names collapsing to the same slug do not merge agents.
Raw conversation remains in L0 SQLite; it is not copied into `FOCUS.md`. The
next prompt should consume the compact projection rather than the unbounded
chat log.

## Capability ladder

`POST /api/v1/research/compile` accepts one contract for all three levels:

1. `detailed_proposal`: proposal plus hierarchy/branches/core code is preserved
   and compiled;
2. `fuzzy_idea`: a deterministic falsification template first expands the idea;
3. `baseline_only`: baseline reproduction, identity/shuffle controls and V/C/P
   alternatives are created before compilation.

The offline expansions are marked `SYNTHETIC_FIXTURE` and `model_call=NOT_RUN`.
They are executable plans, not claims that a live research model produced a
novel hypothesis. Every runnable leaf is crossed with sorted folds and seeds;
the canonical cell digest becomes its `rxpi_` intent token. Recompiling the same
input produces byte-equivalent logical output.

## Independent resource gate

The resource reviewer is intentionally outside human approval. Its output says
`human_approval_can_override=false`. It blocks execution for fold-invariant
recomputation, one-CPU long cells, missing row shards/checkpoints, output
collisions, unnecessary global barriers, validation coupled to mutable compute,
and avoidable serialization. A veto is resolved only by submitting a revised
resource plan.

## Vendor contract sources

- TDSQL Nexa product and interfaces: <https://cloud.tencent.com/product/nexa>
- TDSQL Nexa architecture: <https://developer.cloud.tencent.com/article/2731443>
- TencentDB Agent Memory L0–L3 and isolation model: <https://cloud.tencent.cn/document/product/1813/132100>
- TencentDB Agent Memory SDK/source: <https://github.com/TencentCloud/TencentDB-Agent-Memory>
