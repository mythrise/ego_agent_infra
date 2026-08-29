# Demo narrative (6–8 minutes)

## 0:00 — The operating problem

Open with one sentence: “An experiment is not a result until its plan, authorization,
execution, raw metrics, independent review, and decision are bound by evidence.” Show the
Research Cockpit truth labels first. The hosted EgoLite replay is synthetic; the real
Fashion-MNIST GPU path is implemented but has no trusted external run in this snapshot.

## 0:40 — The judge-feedback acceptance path

Open **Semifinal acceptance path**. In the AgentTeams + GPU tab, walk through
Plan → Review → Approve → Execute → Evaluate → Verify → Decision. In the PostgreSQL +
PolarDB tab, show that PostgreSQL is the production source of truth while SQLite is only a
developer fallback. State that PolarDB/PITR is `NOT RUN`, not implied by this diagram.

## 1:20 — Approval cannot be talked around

Reset the synthetic task and run to `APPROVAL`. Show the R2 scope, exact action digest,
expiry, budget, and rollback pointer. Advance controls stay locked until a human issues the
single-use Grant. Cite the replay, wrong-scope, expiry, and concurrent-consumption tests;
do not stage a fake external receipt.

## 2:20 — Multi-Agent collaboration has an evidence contract

Show the seven Agent identities and the AgentTeams bridge envelope. Explain conflict→replan,
timeout→cancel/replacement/reassign, R2 pause→Grant→resume, independent Reviewer, and restart
from a durable checkpoint. The bridge verifies the complete event hash chain and can persist
JSONB checkpoints, events, and receipts in PostgreSQL. If no official Controller/Team/Matrix
trace is available, say “contract-verified, live origin unverified” and keep the target
benchmark at `SKIP`.

## 3:20 — A real, bounded GPU workload replaces the vague promise

Open `experiments/fashion_mnist_amp/`. The workload compares TinyCNN FP32 and AMP on real
Fashion-MNIST using exactly one CUDA GPU, seed 42, at most 900 seconds, 0.25 GPU·hour, and
100 MiB of data. It freezes environment, approval, AgentTeams/Matrix receipts, raw
predictions, latency, GPU memory, dataset manifest, and reviewer input. Unless the official
same-run artifacts exist, present this as an executable adapter with 13 passing contract/
negative tests, never as a completed experiment or model improvement.

## 4:20 — Metrics are calculated, then independently accepted

In the replay, open baseline/candidate raw artifacts, paired delta/CI, evaluator version,
split, and digests. Explain that an LLM may summarize but cannot calculate or change them.
Then show the seven-kind Evidence Gate. `7/7` presence remains `HOLD` until the deterministic
gate and independent Reviewer both pass.

## 5:20 — Database boundaries survive a buggy Agent

Show the PostgreSQL role matrix: runtime, auditor, evidence writer, and Memory Curator. The
Curator can append `memory_candidates` but cannot insert validated memory; an independent
`memory-validator` performs promotion after the Evidence Gate. Database triggers reject
update/delete/truncate on evidence and memory ledgers. `LISTEN/NOTIFY` wakes consumers after
commit, while durable cursors preserve replay. Cite the local PostgreSQL 16.14 result,
32/32, and immediately separate it from unrun PolarDB backup/PITR claims.

## 6:25 — Recovery and Decision are one replayable object

Show a failed branch, its checkpoint/fencing/compensation events, recovery, and the final
Decision. Build or inspect the `semifinal_acceptance` bundle: Matrix messages, raw metrics,
Evidence Gate, recovery trace, receipts, and Decision are content-addressed together. The
offline verifier rejects missing cells, reused receipts, trace/Decision drift, resource
overruns, and origin promotion. Its current honest terminal label is
`CONTRACT_PASS_ORIGIN_UNVERIFIED`.

## 7:25 — What is proved, and what is next

Close with: “EgoAgentOS makes an AI experiment inspectable and fail-closed. This repository
proves the control, database, protocol, workload, and acceptance contracts. The next evidence
is one official AgentTeams + GPU run and one PolarDB backup/PITR drill, not another synthetic
slide.”

## Claim boundary to say aloud

The default suite has 242 passing local/contract tests. A separate disposable PostgreSQL
16.14 suite has 32 passing integration tests. The hosted browser replay is synthetic. The
official AgentTeams Controller/Team/Matrix run, trusted GPU origin, PolarDB deployment, managed
backup/PITR restore, measured RPO/RTO, Higress, Nacos, and Aliyun services remain unverified or
not run in this submission.
