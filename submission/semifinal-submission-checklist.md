# GOAI Agent Infra semifinal submission checklist

The DingTalk rules page is live; reopen it before upload and treat the portal's current
file/type/size fields as authoritative.

## Mandatory-rule gate

- [x] At least three functional Agents: seven explicit principals with independent review.
- [x] Real AgentTeams integration path: official API contract lock plus executable bridge.
- [x] Dynamic collaboration contract/fault evidence: create/decompose/delegate/accept/
  execute/verify, conflict, timeout, reassignment, R2 recovery, compensation, restart,
  terminal state, and full trace-chain verification.
- [x] Runnable Skills: discover/load/invoke trace plus SemVer, digest pin, canary,
  activation, rollback, and retirement; unsafe generic runner fails closed.
- [x] Approval, rollback, audit, idempotency, observability, and tenant controls have code
  and tests rather than diagram-only claims.
- [x] PostgreSQL is the production path: control plane and AgentTeams bridge persistence,
  four least-privilege roles, RLS, candidate-only Memory Curator, append-only ledgers,
  migration verification, and LISTEN/NOTIFY are covered by local 16.14 integration tests.
- [x] Real Fashion-MNIST one-GPU FP32/AMP adapter and content-addressed acceptance bundle
  fail closed on missing Matrix, receipt, raw metrics, recovery, trace, or Decision evidence.
- [x] Initial-feedback changes are marked in red on slide 2 and mapped to evidence.
- [x] Risks, portability, closure diagram, and explicit SKIP boundaries are present.
- [x] Official AgentTeams v1.2.3 local infrastructure and four-sender Matrix connectivity
  proof frozen without credentials.
- [ ] Full official AgentTeams scientific workflow. All eight nodes remain PENDING until
  a GPU Worker and scoped approval are available.
- [ ] Live GPU origin proof. Keep `CONTRACT_PASS_ORIGIN_UNVERIFIED` unless the same-run
  scheduler/GPU receipt and raw artifacts are captured and independently replayed.
- [ ] TDSQL Nexa / TencentDB Agent Memory provider acceptance plus managed backup/PITR.

## Final artifacts

- [x] `EgoAgentOS_GOAI_Agent_Infra_复赛方案.pptx` (16 inherited/editable slides).
- [x] `EgoAgentOS_GOAI_Agent_Infra_复赛方案.pdf` (16 pages).
- [x] `project-summary-zh.txt` (verify the portal's character counter remains ≤500).
- [x] `demo-script-8min.md`.
- [x] `semifinal-evidence-index.md`.
- [x] deterministic `semifinal-local-proof.json` plus checksum.
- [x] sanitized `agentteams-live-local-proof.json` plus checksum.
- [x] `final-acceptance-20260903.md` truth ledger.
- [x] `experiments/fashion_mnist_amp/` and `semifinal_acceptance/` source, schemas,
  runbooks, and negative tests.
- [ ] optional ≤8 minute public/unlisted demo video and captions.
- [ ] final deterministic ZIP and `.sha256` generated after the last commit.

## Before upload

```bash
make demo-proof
make test
make verify
make package
```

- [ ] Reopen the ZIP and confirm PPTX, PDF, code, docs, proof, benchmark artifacts, and
  lock files are included.
- [ ] Confirm no `.env`, credentials, local database, private data, or production key is
  inside the ZIP.
- [ ] Confirm PPTX/PDF/proof/package hashes match their sidecars/index.
- [x] Isolated PostgreSQL suite rerun: 38/38 PASS; not relabeled as Nexa evidence.
- [ ] If live AgentTeams/GPU or PolarDB/PITR was not run, keep every external origin
  field `UNVERIFIED`/`NOT RUN` in the portal, deck, video, and spoken demo.
- [ ] Open GitHub repository and static Demo URL in a signed-out/incognito browser.
- [ ] Confirm GitHub Pages was built from the final commit; otherwise describe it as an
  earlier static fixture, not this revision.
- [ ] Reopen the DingTalk semifinal rule page and the submission portal immediately
  before submission; record the successful upload state separately from a saved draft.
