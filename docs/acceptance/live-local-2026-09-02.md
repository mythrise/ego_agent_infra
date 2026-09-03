# LIVE_LOCAL acceptance — 2026-09-02

This note freezes the credential-free acceptance boundary for the local deployment. It is
not a physical-GPU receipt and does not turn the GitHub Pages static replay into a live
backend.

## Infrastructure result

- Official source: `agentscope-ai/AgentTeams`, tag `v1.2.3`, commit
  `223ddc2b8073e4c8b93bcbb15e1d717f196c04d9`.
- Controller and Manager phase: running. Manager runtime: `qwenpaw`; current configured
  model: `deepseek-v4-flash`. This is configuration evidence, not per-event provenance.
- Team `ego-researchops`: `Active`, `leaderReady=true`, subordinate count `3/3`.
- Worker resources: `ego-research-lead`, `ego-architect`, `ego-reviewer`, and
  `ego-memory-curator`; all four reported `Running` with Matrix identities.
- Matrix Human: `@ego-judge:matrix-local.agentteams.io:18080`, permission level 2, joined
  to the Team room. Raw access token and initial password are excluded.
- Project `egoagentos-gpu-gated-v1`: `paused`; reason
  `GPU Worker intentionally not attached yet`.
- PostgreSQL identity check: `egoagentos:egoagentos_owner`.
- EgoAgentOS API health: `ok`; database engine: PostgreSQL; immutable audit trigger:
  `trigger_immutable_predecessor_guarded_hash_chain`.
- AgentTeams Bridge live handshake: `true`.

The official local installer did not publish Controller port 8090. A repository-owned,
no-access-log proxy joins `agentteams-net` and exposes it only at `127.0.0.1:18090`; the
official checkout and official images remain unchanged. On Colima, a runtime-only installer
copy changes only the Docker socket bind source to `/var/run/docker.sock`.

## Matrix multi-Agent receipt

The L2 Human sent a read-only acceptance request with a real `m.mentions` entry. The first
request event was `$-laB7Nwa3W3PqUTiRXkoVxf7VwgCCM1JseD6AQ4Ki7w`. The room subsequently
contained 36 post-request Agent events from four distinct senders:

| Agent | Final observed event | Reported boundary |
|---|---|---|
| Research Lead | `$N_ZsZXv9MxD5_fCw6Q_UNDu8KNwbt0R2tOREFm8uQuk` | real Matrix mention sent; workflow remained paused |
| Experiment Architect | `$Ugp71JFkIjSBK3B4UsKOTLkboT-dhKRPgDclI-oS70M` | `READY`, `GPU=NOT_ATTACHED` |
| Independent Reviewer | `$D3otsN_gJddf5xujZ6bnrizn2cVaxTa1HRsGxCcqRWM` | `READY`, `GPU=NOT_ATTACHED` |
| Memory Curator | `$8PFburKNwscUynfPghk7ordiN3f0L7ifFNiFP3BQ0mo` | `READY`, `GPU=NOT_ATTACHED` |

Result: `LIVE_LOCAL PASS`, 4/4 distinct Agent identities. Agent prose about its own model
identity is not accepted as proof. `deepseek-v4-flash` is proven only as the current
Controller/Manager configuration; this smoke does not contain a provider-signed per-event
model receipt.

## 2026-09-03 re-verification

The local stack verifier passed again with PostgreSQL, API, Bridge, Web, Controller,
Manager, Active Team and four Running Worker resources. A sanitized proof was frozen at
`submission/evidence/agentteams-live-local-proof.json`. Project
`egoagentos-gpu-gated-v1` remains paused; all eight workflow nodes are still `PENDING` and
GPU remains `NOT_ATTACHED`. No scientific lifecycle or GPU result is inferred.

## Custom-input model-plane receipt

Input mode: `idea`.

```text
目标：验证一个成本受控的确定性科研规划链。Baseline 是同一训练代码在三个固定随机种子上的结果；
想法是只改变一种数据增强。要求冻结数据划分、基线、指标和种子，设计最小消融矩阵与失败判据；
本次只生成计划和独立审查，不执行 GPU。
```

Run `expert_2f342498149e478f98aaa47b604678f6` verified the live model catalog and selected
`agnes-2.5-pro`. The append-only event chain remained valid with final digest
`ee503007a760e9cef9e76e5a0d812caada0d077fec206490846cc11c1b30821b`.

| Role | Result | HTTP/model receipt | Compact receipt |
|---|---|---|---|
| Research PI | completed | HTTP 200, 9,110 ms, request/response SHA-256 present | compacted, digest present |
| Context Scout | completed | HTTP 200, 10,632 ms, request/response SHA-256 present | compacted, digest present |
| Experiment Architect | failed | two responses failed the exact JSON-object contract | none |
| Independent Reviewer | not started | upstream contract failed | none |

Final decision: `FAILED_CLOSED`; `execution_started=false`. No experiment matrix, GPU job,
or fabricated Reviewer verdict was produced. Increasing the Architect/Reviewer output budget
did not resolve this provider-compatibility failure, so the acceptance stopped instead of
continuing to consume model calls.

## Recovery evidence

An earlier run reached the first compact boundary and failed because the named Docker volume
was root-owned. The deployment now uses a one-shot root storage initializer to create/chown the
two volume roots, then runs the long-lived API as UID 100/GID 101. The rerun proved PI and Scout
compact writes succeed; the earlier failed event record was not rewritten.

## Browser acceptance

The rebuilt local Web UI was exercised in real Chrome at `1440x900` and `390x844`, in
English and Chinese. The three custom-input modes loaded their examples and cleared them;
without an in-memory operator key, the live-run action remained disabled. Page-level
horizontal overflow and browser console warnings/errors were both zero.

After the sticky-navigation regression was fixed, `#compose`, `#protocol`, and `#acceptance`
all landed 15.88–16.16 px below the combined topbar/operator boundary in both viewports.
The RXP and acceptance chains remained locally horizontally scrollable on mobile without
making the document wider than the viewport.

## Secret and claim boundary

- Tokens, passwords, API keys and operator keys are only in `.runtime/live-stack.env` (0600).
- `.runtime/live-stack-public.json` contains URLs, identities, status, room ID and credential
  hashes, but no raw credential value.
- Controller, Matrix, PostgreSQL, API and Bridge are bound to loopback on this host.
- GPU remains `NOT_ATTACHED`; TDSQL Nexa cloud/PITR and a public HTTPS backend remain unverified.
