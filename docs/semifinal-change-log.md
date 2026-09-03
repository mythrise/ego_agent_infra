# 复赛迭代变更记录

比较基线：初版公开实现（`545b6a6` 至 `435bbb9`）与当前复赛集成分支。本文记录代码与
证据的变化，不把设计完成写成外部部署完成。

## 一页结论

初版的核心价值是可运行的 synthetic ResearchOps 控制面与静态 judge replay；复赛迭代
把它扩展为六个可独立验收的基础设施层：RXP 实验协议、fail-closed benchmark、可执行
Skill runtime、可持久恢复的 AgentTeams bridge、PostgreSQL 生产数据路径，以及成本受控的
真实 Fashion-MNIST GPU adapter + content-addressed 一键验收包。

最大剩余缺口没有被文档掩盖：官方 AgentTeams Controller/Manager、Active Team、四个
Worker 资源和 Matrix 四 Agent smoke 已在本地运行；但完整委派/Skill/R2/Decision、逐场景
fault/replay harness、GPU receipt 和 Nexa/PITR 演练尚缺，所以实验 origin 仍只达到
`CONTRACT_PASS_ORIGIN_UNVERIFIED`，canonical target benchmark 为 `SKIP`。

## 初版 → 复赛差异

| 领域 | 初版 | 复赛新增/修改 | 可复核证据 | 尚未证明 |
|---|---|---|---|---|
| 实验确定性 | approval token、RunManifest、七类 EvidenceGate；一个任务内的控制语义 | RXP/1 将完整实验矩阵拆成 per-cell Intent → Grant → Receipt → Evidence → Decision，并记录 missing decisions | `protocols/rxp/`、committed schemas、canonical/Merkle vectors、protocol tests | 随机模型结果的科学正确性、任意 GPU 的字节确定性 |
| Benchmark | 静态 judge replay 与普通单元测试 | 14 个版本化场景、负对照、deterministic core、AgentTeams target、独立 oracle、置信区间、persistent evidence bundle、release replay | `benchmarks/`、committed raw JSON/Markdown/hash、benchmark tests | AgentTeams target 尚未真实通过；逐场景 harness 未实现，committed target 是 70/70 `SKIP` |
| AgentTeams | CRD/resource template 与 message envelope；没有 runtime bridge | 官方 commit 契约锁；Project/TeamHarness/Matrix bridge；动态 replan；timeout reassign；restart/resume；compensation；R2 恢复；artifact digest 验收 | `apps/agentteams_bridge/`、`integrations/agentteams/`、`tests/agentteams/`、live runbook、`LIVE_LOCAL` receipt | 完整 task lifecycle、Skill tool、R2/Decision 与场景 fault injection |
| Trace 真值 | 可读 audit/event 模型 | `egoagentos.agentteams-trace/v1` schema；project/task/correlation/context 绑定；3+ Worker、Skill、HITL、review、Decision、RXP 五链与 official response 校验 | benchmark-owned schema/verifier；adapter 自报值不作为真值 | 外部系统实际产生的合格 trace |
| Skill 工程化 | 6 个 `SKILL.md` 合同 | 文件系统 discovery、package digest/version pin、typed invocation、idempotent correlation、failure trace、canary/retire/rollback、FastAPI endpoints | `skill_runtime/`、`apps/api/skill_runtime_api.py`、`tests/skills/` | 真实 AgentTeams Worker 已安装并成功调用这些 Skill |
| 数据持久化 | SQLite 开发状态 | PostgreSQL 生产路径；控制面和 bridge 分库 URL；MVCC/row lock/CAS；四类最小权限角色；candidate→validator→validated memory；append-only trigger；commit-only LISTEN/NOTIFY；校验和迁移与 fail-closed PolarDB preflight | `apps/api/migrations/postgres/`、`apps/agentteams_bridge/migrations/postgres/`、`deploy/postgres/`、`tests/postgres/`、recovery runbook | PolarDB-PG 云实例、只读节点、备份/PITR、跨区容灾和实测 RPO/RTO |
| 真实 GPU 验收链 | 无真实 workload | Fashion-MNIST 单 GPU FP32/AMP adapter；900 秒/0.25 GPU·hour/100 MiB 上限；raw prediction/latency/memory；独立 reviewer；离线 verifier | `experiments/fashion_mnist_amp/`、13 个 experiment tests | 官方 GPU/AgentTeams origin、真实指标与模型改进 |
| 一键验收包 | 静态 proof | 8 个 MVP 场景；Matrix、receipt、raw metric、Evidence Gate、恢复 checkpoint、Trace、Decision 全链内容寻址与负例重验 | `semifinal_acceptance/`、16 个 acceptance tests | 外部运行来源；v1 只允许 `UNVERIFIED_OPERATOR_ASSERTION` |
| Judge 体验 | GitHub Pages 静态回放 | RXP protocol API 与 judge-facing cockpit，新增 AgentTeams+GPU 和 PostgreSQL+PolarDB 验收路径；仍保留清晰 truth label | API/Web tests、production build、openapi/docs | 页面是 live AgentTeams、MCP、GPU 或云服务 |
| Claim 管理 | README 中的 target/current 区分 | claim ledger、scorecard、live runbook、target trace schema 和 `SKIP` release semantics | `docs/claims-evidence.md` 与本组复赛文档 | 任何缺证据的部署或性能主张 |

## AgentTeams 核心链的具体升级

### 以前

- Agent 身份和资源配置说明“谁应该做什么”；
- envelope 说明“消息应该长什么样”；
- deterministic local handler 可以演示业务状态，但不是 AgentTeams；
- 没有官方 Project/task 的可恢复映射，也没有 live evidence gate。

### 现在

- bridge 只通过官方 Project Workflow/TeamHarness/Matrix surface 观察协作事实，不自行伪写
  Worker ACK、submit、accept 或 terminal state；
- 所有消息绑定 `ego_task_id`、`project_id`、`task_id`、`trace_id`、`correlation_id`、
  `context_version`、attempt 与 causation；
- conflict、stale context 和 revision mismatch 进入 replan；ACK/execute timeout 进入
  cancel + `replacementTaskId` + bounded reassignment；
- R2 先 pause，消费 scope-bound Ego token 后才 resume/replan；token 不进入 Matrix 或数据库；
- 上游已修改、下游通知失败时进入 durable `COMPENSATION_REQUIRED`，而不是假装回滚成功；
- declared result envelope 与 primary artifact 都重新计算 SHA-256；Reviewer 必须独立且 PASS；
- Skill 证据分为 `DECLARED`、`SPAWN_AUTHORIZED`、`TOOL_INVOKED`，不把资源声明叫作调用；
- bridge checkpoint、event 与 receipt 可落 PostgreSQL JSONB；CAS、per-run advisory lock、
  append-only trigger 与 receipt uniqueness 防止并发重试伪造第二次 effect；
- verifier 重算完整 event hash chain、head、v2 envelope identity、生命周期顺序与 Decision；
- benchmark adapter 在逐场景 fault/replay harness 未实现前始终返回小写 `skip`；live opt-in
  明确标记 `UNIMPLEMENTED`，不启动 generic run、不制造事件。

### 仍需 live 验收

1. 已完成：在官方 AgentTeams `v1.2.3` pin 上部署 Manager、Team 和四个 Worker；
2. 已完成基础 smoke：真实 Matrix room 收到四个 Agent 身份的响应；
3. 待完成：非 synthetic Project 的委派、ACK、Skill/tool、R2、review 与终态 Decision；
4. 待完成：为 14 个 canonical scenario 分别执行 fault driver，不能复用一条 generic trace；
5. 待完成：持久化每个 trial 的 trace/manifest/artifacts 并执行 `make benchmark-release`；
6. 只有 release gate 无 `FAIL`、`ERROR`、`SKIP` 后，才更新完整实验 live claim。

## 与评分权重的变化关系

| 权重项 | 复赛迭代带来的增量 | 当前最重要的下一条证据 |
|---|---|---|
| 场景价值与行业复用性 25% | 把单任务 demo 提升为矩阵级实验承诺和可迁移 adapter contract | 真实/公开实验 + 手工 baseline + 第二领域 mapping |
| 多 Agent 协作 25% | 从资源模板升级为可执行、可恢复、动态路由 bridge | 官方 live 3+ Worker correlated trace |
| Skill 工程与生态复用 25% | 从静态包升级为 digest-pinned runtime 与 lifecycle | Worker spawn/tool result + rollback live trace |
| 工程化、运行验证与安全可审计性 20% | 增加 RXP、独立 oracle、fault corpus、PostgreSQL 与 evidence bundle | live fault injection + exactly-once external effect proof |
| 开源贡献 5% | 增加协议、schema、adapter、benchmark 和 runbook | tagged release 与外部复用/反馈 |

## 2026-08-29 验证快照

| 命令 | 结果 | 结论边界 |
|---|---|---|
| `make test` | 242 tests passed：API 69、RXP 26、Skills 6、Proof 3、Benchmark 29、Acceptance 16、AgentTeams 41、Experiments 13、MCP 23、Web 16 | 证明当前仓库默认本地/contract 行为；不证明外部 origin |
| `make test-agentteams check-agentteams` | 41 tests passed；Ruff、mypy 通过；official lock shape 通过离线检查 | 证明 bridge/fixture contract、PostgreSQL adapter contract 与静态 pin 形状；**没有**验证 upstream bytes 或 live 服务 |
| `make test-rxp test-skills` | RXP 26 tests、Skill 6 tests passed；schema drift check 通过 | 证明 reference/local runtime 行为，不是分布式部署或 Worker live invocation |
| `make test-benchmark` | benchmark 29 tests passed；Ruff、mypy 与 strict local replay 通过 | local strict 允许 target capability gap 为 `SKIP`；它不是 AgentTeams release gate |
| acceptance / experiment suites | Acceptance 16、Experiments 13 passed | 证明 bundle 与真实 workload adapter 会 fail closed；**没有**证明外部执行发生 |
| disposable PostgreSQL 16.14 | 32 tests passed | 证明本地 PostgreSQL 控制面/bridge/权限/preflight contract；不证明 PolarDB、PITR 或云 IAM |
| `python3 scripts/verify_submission.py` | `PASS` | 证明提交包静态约束；不证明外部系统在线 |
| 1-repetition `--release-gate agentteams-rxp-target` | exit 1；0 pass、0 fail、0 error、14 skip | 正确阻止 release；缺 live 配置时没有 synthetic PASS |

四份复赛文档另通过 `git diff --check`、本地 Markdown link resolution、code-fence balance、
final newline 与 tab 检查。

## Claim 变更

| 旧表述风险 | 当前允许表述 |
|---|---|
| “EgoAgentOS 使用 AgentTeams 完成了实验” | “官方 AgentTeams 基础设施与四 Agent Matrix smoke 已 `LIVE_LOCAL`；完整实验 workflow/GPU 未运行” |
| “多 Agent 动态协作已证明” | “replan/reassign/recovery 逻辑已通过本地 fault contract；仍需官方事件链” |
| “Skill 已在 Worker 中调用” | “本地 Skill runtime 已调用；AgentTeams `TOOL_INVOKED` 证据尚缺” |
| “RXP 让 AI 实验确定” | “RXP 固化实验承诺、授权、验收和完整性边界，不保证随机训练结果或科学结论” |
| “RXP 是类似 MCP/A2A 的标准协议” | “RXP/1 是本项目提出并实现的协议草案/reference implementation，尚无标准地位或外部采用证明” |
| “benchmark 已通过” | “local deterministic-core 有可复核结果；AgentTeams target 当前全 `SKIP`，release gate 未通过” |

RXP 与相邻系统的精确边界见 [`protocols/RXP-comparison.md`](protocols/RXP-comparison.md)，
完整评分状态见 [`semifinal-scorecard.md`](semifinal-scorecard.md)。
