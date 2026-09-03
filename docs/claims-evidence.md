# Claim ledger

This file is the presentation safety rail. A claim moves to “verified” only when its
evidence URI is produced by the current repository and replayed successfully.

## Verification snapshot, 2026-08-29

The default cross-component suite at this snapshot contains **242 tests**: API 69,
RXP 26, Skills 6, semifinal proof 3, Benchmark 29, Acceptance 16, AgentTeams 41,
Experiments 13, MCP 23, and Web 16. This count is a dated commit snapshot, not a
rolling promise. After any change, live `make test` and CI output are authoritative.
The 32-test PostgreSQL 16.14 suite is listed separately because it requires an explicit
real database URL and is not included in the default 242.

| Claim | Current state | Required evidence |
|---|---|---|
| deterministic ResearchOps state machine runs end to end | verified locally, synthetic workload | 69 API/domain tests at the dated snapshot + isolated replay to `COMPLETED` |
| R2 execution cannot bypass human approval | verified locally | API negative replay returned `approval_required`; consumed approval replay returned `approval_already_decided` |
| decision requires 7/7 verified evidence kinds | verified locally | happy path gate `pass` at 7/7; missing-trace path stopped at `VERIFY` with no Decision |
| included metric comparison is deterministic | verified against synthetic fixture | fixed-seed evaluator unit tests + replayed raw sample artifact |
| MCP path/shell/approval boundaries | verified locally | 23 MCP tests + Ruff; descriptor-relative no-follow scans; four servers and seven typed tools |
| Streamable HTTP MCP transport | verified on loopback | automated initialize + `tools/list` test for repo server; stdio remains default |
| API approval is accepted once by GPU MCP | verified as a cross-runtime contract test | shared `egoap1` HMAC contract; exact dry-run digest/scope; one fake-runner launch; replay rejected |
| RXP/1 canonical experiment-acceptance protocol is executable | verified locally against synthetic fixtures | 26 protocol tests; committed schemas; byte-identical CLI replay; canonical/Merkle known vectors |
| RXP detects omitted matrix decisions and rejects scope/expiry/replay/tampering | verified in the reference implementation | Cartesian-plan validation, `missing_decisions`, concurrent SQLite replay test, causal/root mutation tests |
| RXP schema/demo/verify HTTP API is executable | verified locally; synthetic demo and structural verifier only | `GET /api/v1/rxp/schemas`, `GET /api/v1/rxp/demo`, `POST /api/v1/rxp/verify`; API tests accept the fixture and reject a tampered ledger |
| RXP is persisted by the FastAPI task store or a distributed transparency service | not claimed | durable RXP document/artifact store, serializable distributed replay registry, task correlation, and externally checkpointed root required |
| Skill catalog, digest pinning, invocation trace, and lifecycle rules work | verified in the in-process reference runtime | 6 Skill tests; strict `x.y.z`; six packages discovered, exactly three allowlisted handlers; canary, activate, retire, rollback, pin mismatch, and fail-closed traces |
| Skill rollout state or invocation traces survive API restart | not claimed | durable shared registry state, transaction contract, multi-replica routing proof, and recovery test required |
| Web cockpit reflects backend gate truth | verified locally | 16 component/normalization/static-replay/operator-session tests + production build + desktop/mobile acceptance-path QA |
| PostgreSQL store preserves the control-plane and AgentTeams bridge contracts | verified on isolated local PostgreSQL 16, 38/38 PASS | real-database suite: full API, atomic rollback, optimistic concurrency, append-only ledgers, advisory-lock CAS, commit-only notify, migration replay, bridge restart/concurrency, least-privilege roles, and historical direct-GRANT cleanup |
| Memory Curator cannot directly publish validated memory | verified in application and PostgreSQL policy | Curator inserts `memory_candidates`; an independent deterministic validator promotes after the gate; RLS/GRANT and mutation triggers enforce the boundary |
| PolarDB-PG deployment or PITR completed | NOT RUN, not claimed | cloud endpoint handshake, backup policy, restore job, chain replay, measured RPO/RTO required |
| PolarDB-PG preflight contract is executable | verified locally against PostgreSQL fixtures; no cloud claim | fail-closed checks cover TLS/engine marker, writer/reader topology, JSONB/pgvector capability, four roles, RLS, append-only triggers, migration checksums, and LISTEN/NOTIFY |
| Docker Compose topology is syntactically valid | verified with `docker compose config` | rendered services, PostgreSQL healthcheck, dependency ordering, and non-empty local secret requirement |
| API/Web Docker image build succeeds on this host | NOT VERIFIED | the 2026-08-29 attempts timed out while fetching Docker Hub metadata; a later successful clean build and health check are required |
| API invokes MCP over HTTP in the default Web replay | not claimed | network client call trace + correlated tool artifact required |
| CPU hashing recovery branch works | synthetic control-flow fixture only | before/after fixture + trace sequence; requires physical-run evidence for a performance claim |
| bounded Fashion-MNIST FP32/AMP workload adapter is executable | contract-verified; external origin unverified | 13 experiment tests cover one-CUDA-GPU fail-closed execution, resource limits, raw artifacts, telemetry, manifests, and offline verification; no live run is bundled |
| 8×RTX 4090 experiment ran | not claimed | real scheduler logs + manifests + metric artifacts |
| AgentTeams bridge contract/state/fault behavior | contract-verified; live local connectivity verified | 41 AgentTeams tests plus the 2026-09-02 Controller/Team/Worker/Matrix/Bridge receipt; scenario-level replan/reassign/R2/compensation remains fixture-only |
| AgentTeams Matrix collaboration is live | `LIVE_LOCAL` infrastructure smoke: Active Team, four Running Worker resources, paused Project, and 36 post-request events from four Agent identities | [`acceptance/live-local-2026-09-02.md`](acceptance/live-local-2026-09-02.md); a release claim still requires official spawn/tool/artifact events, scoped R2 receipt, final trace hash, and GPU origin |
| semifinal acceptance bundle detects incomplete or forged evidence | verified locally; origin remains unverified | 16 tests cover eight MVP acceptance scenarios, Matrix/Decision closure, receipt uniqueness, raw metric policy, trace consistency, recovery checkpoints, resource limits, and negative origin promotion |
| committed RXP Bench artifact is reproducible infrastructure evidence | verified as a synthetic local artifact | 14 scenarios × 5 repetitions × 3 profiles = 210 trials; corpus digest `eed5d4e06adc4713a765b3961643cda538b393bf651a848a24077a77b15098a4`; semantic result digest `05cab481a525210026d07377bb841ca0cd73f27790e9856b3c29211320b6b996` |
| canonical 14-scenario AgentTeams target benchmark passes | not claimed; 70/70 target trials are honest `SKIP`, and live opt-in remains `UNIMPLEMENTED/SKIP` until the per-scenario fault/replay harness exists | benchmark tests enforce fail-closed opt-in plus the future trace contract; generic completion is never launched as pseudo release evidence and can never become PASS |
| Higress isolates upstream credentials | not configured | route export + positive/negative leak test |
| Nacos Skill is published | not configured; local registry proof is not publication | registry version response + package digest + online rollout status |
| official Aliyun SLS Skill queried a trace | not configured | redacted invocation + matching trace ID |

Never copy numeric claims from the unrelated legacy deck. Its old test, cache,
determinism, hash, and rollback claims refer to a different project and absent evidence paths.

## Live expert workflow addendum, 2026-09-02

| Claim | Current state | Required evidence |
|---|---|---|
| Judge input can drive visible expert roles through the server-side model gateway | live endpoint exercised: PI and Scout returned HTTP 200; Architect failed the exact JSON-object contract after two attempts; Reviewer was not started; run failed closed | authenticated `POST /api/v1/expert-runs`; persisted run `expert_2f342498149e478f98aaa47b604678f6`; request/response digests; valid event chain; no GPU dispatch |
| Role hand-offs are inspectable | verified locally | each role exposes its payload field names, upstream-role list, payload SHA-256, schema-validated output, request/response digests, latency, and focus-memory receipt |
| Architect output controls the compiled tree and matrix | verified locally | fake-gateway E2E compiles architect branches, metrics, folds, and seeds into a non-empty deterministic matrix |
| Independent review occurs before physical execution | verified locally | reviewer receives the exact compiled-plan digest; final state is planning-only and `execution_started=false` |
| Public GitHub Pages performs live model calls | not claimed unless `EGO_PUBLIC_API_ROOT` is configured to a separately deployed HTTPS API | deployed API health/config status, server-side secret injection, Pages CORS, operator authentication, and one browser-captured live run |
