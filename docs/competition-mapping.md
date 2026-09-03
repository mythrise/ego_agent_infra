# GOAI Agent Infra 复赛规则映射

本页把 [复赛规则](https://alidocs.dingtalk.com/i/nodes/AR4GpnMqJzYd2LLOhLB1x4zLVKe0xjE3)
的五项权重与硬门槛映射到仓库证据。权重与 AgentTeams 要求于 2026-08-29 从登录态规则页
核对；[公开 Agent Infra 赛道页](https://www.goaihz.com/#tracks)用于赛道背景。若规则更新，以
组委会最新页面为准。

## 评分维度 → 实现 → 证据

| 复赛维度 | 权重 | EgoAgentOS 设计回应 | 已落库证据 | 当前真实边界 |
|---|---:|---|---|---|
| 场景价值与行业复用性 | 25% | 把具身 AI 的高成本实验从聊天式黑盒改为目标、矩阵、授权、执行、复核和决策闭环 | `apps/api/`、`protocols/rxp/`、6 个 Skills、14 场景 benchmark、真实 Fashion-MNIST 单 GPU adapter | adapter 与验收器已实现，但官方 GPU origin 未验证；尚无真实科研用户基线和跨领域验收 |
| 多 Agent 协作 | 25% | AgentTeams 负责真实团队协作；PI、Scout、Architect、Runtime、Evaluator、Reviewer、Memory Curator 分权；中间结果可 replan，超时可改派，重启可恢复 | `apps/agentteams_bridge/`、`integrations/agentteams/`、`tests/agentteams/`、live runbook、四 Agent Matrix receipt | 官方 Controller/Manager/Team/Workers/Matrix 已 `LIVE_LOCAL`；完整委派/Skill/R2/终态与逐场景 fault harness 尚未验收 |
| Skill 工程与生态复用 | 25% | Skill 不是提示词附件，而是可发现、版本固定、可调用、可追踪、可回滚的运行时资产 | `skills/`、`skill_runtime/`、`/api/v1/skills/*`、`tests/skills/` | 本地 runtime 已验证；真实 Worker 的 discovery/tool invocation 仍待 live trace |
| 工程化、运行验证与安全可审计性 | 20% | 确定性控制面拥有状态、授权、幂等、证据和最终验收；RXP 固化实验承诺链；benchmark 与 acceptance bundle 独立验证 adapter 证据 | RXP schemas/verifier、PostgreSQL 生产路径与四类角色、R2 token、append-only hash-chain、LISTEN/NOTIFY、Nexa/Agent Memory adapters、evidence gate、persistent bundle | 本地/contract 证据不等于生产安全认证，也不证明真实 GPU/完整 AgentTeams workflow/Nexa/PITR |
| 开源贡献 | 5% | 发布代码、协议文档、JSON Schema、Skill 包、adapter、负对照与可复现测试 | Apache-2.0 repository、README、runbooks、CI commands | 尚无正式 release/adoption 证据；RXP 是项目协议，不是行业标准 |

## 硬门槛的可执行解释

```text
真实 AgentTeams Project
  → Team Leader delegate
  → 至少 3 个不同职能 Worker ACK / execute / submit
  → 中间证据触发 replan 或故障触发 reassign
  → R2 pause / human Grant / resume
  → independent review
  → evidence-gated terminal Decision
```

以上链路的每个箭头都必须由同一 correlation 下的官方 response/event 和内容摘要证明。
以下内容一律不能替代硬门槛：

- 本地 role handler、固定脚本或 `scripted-negative-control-v1`；
- fixture、mock response、静态网页回放或预制 Matrix 事件；
- 只有 Worker CRD/role 名称、没有接单与产物事件；
- Skill 只在 `spec.skills` 中声明、没有 spawn/tool result；
- adapter 自报 `PASS`、但没有 benchmark-owned schema 校验和持久化 trace；
- 用合成性能数据、未执行的 GPU/云调用或截图冒充真实结果。

因此当前 canonical `agentteams-rxp-target` 诚实返回 `SKIP`；即使 live opt-in 也明确为
`UNIMPLEMENTED/SKIP`。这说明逐场景 harness 与 live 证据缺失，不是测试通过；在 release
gate 中 `SKIP` 与 `FAIL`/`ERROR` 一样阻止放行。

## 业务全链路映射

| 业务阶段 | AgentTeams 动态协作 | EgoAgentOS / RXP 控制 | 验收证据 |
|---|---|---|---|
| 目标与上下文冻结 | Leader 接收结构化 `TASK_REQUEST` | 绑定 task、trace、correlation、context version 与 body digest | Project create response、Matrix event ID、envelope hash |
| 计划与分工 | Leader 用 TeamHarness 创建依赖图并 delegate | MatrixPlan/Intent 冻结 axes、cells、代码/数据/配置引用 | workflow snapshot、task/spawn IDs、Intent digest |
| 中间冲突 | Worker 返回 conflict 或 bridge 发现 revision/context 不一致 | 拒绝 stale context；计算 cycle-safe replacement DAG | `plan.conflict_detected`、Controller replan response/hash |
| 超时与改派 | ACK/execute timeout 后 cancel，并设置 `replacementTaskId` | bounded retry、attempt/causation 继承，防止无限改派 | timeout、cancel、replacement、reassigned events |
| 高风险执行 | Project pause，不允许聊天内容直接放行 | 人类签发 scope-bound one-time Grant；Ego 消费后再 resume | grant ID、receipt digest、pause/resume response |
| 产物与验收 | Worker submit，Leader check/accept | 下载 declared artifact，复算 SHA-256，核对 task/context | artifact URI/hash、result envelope、acceptance state |
| 独立复核 | Reviewer 与 executor 身份分离 | Evidence gate 重算完整性与 review independence | reviewer actor、Evidence root、PASS verdict |
| 决策与记忆 | Project 到达终态 | Decision 只引用完整 evidence set；缺 cell 显式列出 | Decision digest、Matrix root、missing list、final trace |
| 恢复与补偿 | 重读官方 workflow 后续跑 | PostgreSQL checkpoint/CAS/advisory lock 为生产路径；SQLite 仅开发 fallback；失败的 resume/replan/send 被 pause fence | recovery event chain、compensation state、无重复 Project/receipt |

## 已实现、已验证与待验收

| 能力 | Code | 本地/contract 证据 | 官方 live 证据 |
|---|:---:|:---:|:---:|
| AgentTeams resource + official contract pin | ✓ | ✓ | ✓ `LIVE_LOCAL` |
| Project create/pause/resume/replan/complete 与 task cancel/replacement bridge | ✓ | ✓ | — |
| Matrix structured dispatch 与 response correlation | ✓ | ✓ | ✓ 四 Agent smoke；非完整 workflow |
| conflict/replan、timeout/reassign、restart/resume、compensation | ✓ | ✓ | — |
| R2 Grant 进入恢复链 | ✓ | ✓ | — |
| PostgreSQL bridge checkpoint/event/receipt、append-only 与最小权限 | ✓ | ✓，本地 16.14 | — PolarDB/PITR |
| Fashion-MNIST 单 GPU FP32/AMP workload + acceptance verifier | ✓ | ✓ contract/negative tests | — official AgentTeams/GPU origin |
| 3+ Worker、Skill `TOOL_INVOKED`、独立 review、终态 | ✓ verifier | ✓ fixture rejection/acceptance contract | — |
| 14 个场景各自的真实 fault proof | ✓ fail-closed adapter/verifier | ✓ 缺证据会拒绝 | —；当前 70/70 `SKIP` |

`✓ verifier` 表示校验器能验证该事实，不表示事实已经在官方服务上发生；`—` 表示未提交
live evidence。

## 复赛材料对应关系

| 材料 | 应展示的证据 | 禁止表述 |
|---|---|---|
| 更新方案书 | 初赛→复赛差异、五项权重映射、AgentTeams 动态协作图、RXP 边界、风险与迁移方案 | 把设计图写成部署完成 |
| Demo / 视频 | live Project/Worker/Matrix、动态分支、R2 恢复、Skill invocation、独立 review、trace replay | 用静态 GitHub Pages 当作 live AgentTeams |
| GitHub 仓库 | 安装步骤、固定版本、schemas、测试、benchmark 原始结果与 honest claim ledger | 隐藏 `SKIP` 或只给汇总百分比 |
| 评审答辩 | 现场解释 AgentTeams 负责协作、RXP 负责实验承诺/验收/完整性、控制面负责授权 | 声称 RXP 替代 MCP/A2A/PROV-O/MLflow 或已成为标准 |

当前可安全使用的总述是：

> EgoAgentOS 已在本地运行官方 AgentTeams Controller/Manager、Active Team、四个 Worker
> 资源和 Matrix 四 Agent smoke，并验证 fail-closed bridge、PostgreSQL 数据合同、RXP、
> Fashion-MNIST 单 GPU adapter 与一键验收层；完整 AgentTeams Project/Skill/R2/Decision、
> GPU origin、Nexa/PITR 和 14 场景 target release benchmark 尚待验收。

详细 release 判据见 [`semifinal-scorecard.md`](semifinal-scorecard.md)，live 操作见
[`agentteams-live-runbook.md`](agentteams-live-runbook.md)，claim 状态见
[`claims-evidence.md`](claims-evidence.md)。
