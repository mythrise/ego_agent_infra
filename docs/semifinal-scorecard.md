# GOAI Agent Infra 复赛就绪度记分卡

规则来源：[复赛规则](https://alidocs.dingtalk.com/i/nodes/AR4GpnMqJzYd2LLOhLB1x4zLVKe0xjE3)，
于 2026-08-29 按登录态页面核对。本文是仓库内部 release gate，不是评委打分，也不替代
组委会最新规则。

## 当前结论

> 2026-09-02 最终版更新：数据库生产目标已由 PolarDB PG 更新为 TDSQL Nexa SQL 数据权威，
> 上下文层接入 TencentDB Agent Memory v3 合同；本地已经验证 per-agent SQLite + `FOCUS.md`
> 自动 compact、三级输入编译、实验树/矩阵和不可被人类审批覆盖的资源否决。腾讯云实例仍未
> 配置，因此 live 状态是 `NOT_CONFIGURED/NOT_RUN`。下文保留的 PolarDB 记录是上一轮评委意见
> 的历史验收，不代表本轮已跑 Nexa。

**工程候选已形成，但复赛 AgentTeams 硬门槛仍为 `BLOCKED`。** 仓库已经提供可执行
Controller/Matrix bridge、PostgreSQL checkpoint/receipt/event backend、动态 replan、超时
改派、恢复与补偿、R2 HITL 恢复链、结构化 correlation envelope、Skill 证据分级、
Fashion-MNIST 单 GPU adapter、离线验收包和 fail-closed benchmark adapter；这些行为已有
本地 contract/fixture 测试。当前主机没有可用的官方 AgentTeams 服务、真实 Team/Worker、
Matrix 凭据、GPU receipt 与逐场景 fault/replay harness，因此不能产生官方运行证据。已提交
benchmark 中 `agentteams-rxp-target` 为 70/70 `SKIP`；live opt-in 仍明确返回
`UNIMPLEMENTED/SKIP`，不是 `PASS`。

证据必须按以下三层表述：

| 层级 | 可以主张 | 不可以主张 |
|---|---|---|
| 已实现 | bridge、schema、adapter、runbook 与 release verifier 已进入代码库 | 这些代码已经驱动官方服务 |
| 本地验证 | fixture/contract、离线契约锁、状态机、RXP、Skill runtime 与安全 oracle 通过测试 | fixture 是 AgentTeams Worker 的真实输出 |
| 官方 live | 仅当 Controller、TeamHarness、Matrix 与真实 Worker 共同产生可复核 trace 后成立 | 用静态回放、mock response、角色标签或自报 `pass` 替代 live trace |

## 硬门槛与否决项

以下条目按复赛硬要求转化为仓库的 fail-closed 验收门。任何一项未满足，均不得把作品标记
为“复赛 live-ready”；本表只说明仓库如何执行规则，不扩写新的官方规则。

| 硬门槛 / 否决风险 | 必须提交的机器证据 | 当前状态 | Release 判定 |
|---|---|---|---|
| 核心业务链真实使用 AgentTeams | 同一 Project 的官方 create/workflow/spawn/Matrix 标识，覆盖创建、委派、接单、执行、验收与终态 | bridge 合同完成；无官方 live run | **未满足** |
| 至少 3 个不同职能 Agent | trace 中至少 3 个真实 AgentTeams Worker，`id`、Matrix user、role 唯一且事件 actor 可解析 | 资源定义 7 个 Worker；仅本地合同验证 | **未满足 live 证明** |
| 动态协作而非固定脚本 | 中间结果触发 conflict/replan；timeout 触发 cancel/replacement/reassign；恢复后继续原 correlation | 逻辑与故障测试完成；无官方事件链 | **未满足 live 证明** |
| 核心 Skill 可发现、调用、追踪 | Worker 声明、spawn 授权和官方成功 `tool_result`；版本及 package digest 与任务 trace 关联 | 本地 registry/API 可运行；AgentTeams `TOOL_INVOKED` 未 live 验证 | **部分满足** |
| 高风险动作有人类授权 | R2 先 pause，单次 scope-bound Grant 被 EgoAgentOS 消费，再 resume/replan；重放被拒 | bridge 与本地 approval/RXP 测试完成；无 live receipt | **部分满足** |
| Demo 可运行且证据真实 | 干净环境启动、失败分支、恢复、原始 trace、artifact digest 和 replay 命令 | 本地 synthetic Demo 和离线 acceptance bundle 可运行；官方 live Demo 缺失 | **未满足 live 证明** |
| 不伪造数据、trace 或结果 | adapter 的 `PASS` 由 benchmark 自己校验持久化 trace；harness 未实现或缺 live 证据必须 `SKIP` | 已 fail-closed；当前 target 全部 `UNIMPLEMENTED/SKIP` | **防伪门已满足，业务门未满足** |

固定脚本、模拟 AgentTeams 事件、空 trace、自报指标、预填 Worker 状态或截图均不能消除上述
缺口。`scripted-negative-control-v1` 只能作为反例；`SKIP` 降低覆盖率且在 release gate 中按
失败处理。

## 五项评分映射

这里不计算主观“预估总分”，只列出可复核证据和当前失分面。

| 复赛维度 | 权重 | 当前可复核优势 | 仍需补齐的评审证据 | 当前判定 |
|---|---:|---|---|---|
| 场景价值与可迁移性 | 20% | 面向具身 AI 实验的目标→矩阵→执行→评测→复核→决策闭环；RXP 与 adapter 为领域无关合同；真实 Fashion-MNIST FP32/AMP 工作负载已代码就绪 | 一次官方 AgentTeams+GPU 同源运行、研究员手工基线，以及第二领域的迁移映射 | `PARTIAL` |
| 多 Agent 协作 | 25% | 7 个职责分离 Worker；bridge 映射 Project/TeamHarness/Matrix；实现 conflict/replan、timeout/reassign/resume/compensation | 一条官方 live trace 证明至少 3 Worker 的动态协作与终态验收 | `BLOCKED` |
| Skill 工程化 | 20% | 6 个版本化 Skill 包；本地 discovery、digest pin、invocation trace、canary/retire/rollback 已实现并测试 | 在真实 Worker 上证明包存在、spawn 授权、成功调用、失败与版本回滚 | `PARTIAL` |
| 工程实现与安全审计 | 30% | RXP/1、14 场景 benchmark、独立 trace oracle、content-addressed bundle、TDSQL Nexa SQL adapter、TencentDB Agent Memory v3、per-agent compact、PostgreSQL-compatible 事务/最小权限/append-only/LISTEN-NOTIFY、R2/重放/篡改门禁 | 官方 AgentTeams live fault injection、Nexa/Agent Memory 实例验收、外部 effect exactly-once 与真实恢复时间 | `PARTIAL` |
| 开源贡献 | 5% | Apache-2.0 代码、JSON Schema、Skill、adapter、测试、runbook 与可复现实验协议均公开可读 | tag/release、干净机安装记录，以及外部 issue、复用或反馈证据 | `PARTIAL` |

## AgentTeams live 证据门

只有 [`agentteams-live-runbook.md`](agentteams-live-runbook.md) 的验收项全部成立，才可将
AgentTeams claim 从 `contract-verified` 升级为 `live-verified`。最低证据包必须包含：

1. 官方 Controller 版本、部署镜像/commit、Team `Active` 与 Worker ready 记录；
2. 同一 `project_id`、`task_id`、`trace_id`、`correlation_id` 与 `context_version`；
3. 至少 3 个真实 Worker 的 delegate、ACK、Skill/tool、submit、accept 与 review 事件；
4. 一次中间冲突 replan 和一次 timeout cancel/replacement/reassign；
5. R2 pause → 人类 Grant → Ego receipt → resume 的连续恢复链；
6. Intent → Grant → Receipt → Evidence → Matrix/Decision 的 digest 关联；
7. 原始 artifact、workflow 与 Matrix response 的 SHA-256；
8. benchmark 生成的持久化 trace、manifest、replay 结果与匹配的 `trace_sha256`。
9. GPU receipt、原始 prediction/latency/memory 指标与 workload telemetry 证明来自同一次
   bounded execution，而不是 operator assertion。

本地验收器即使完整通过，也只输出 `CONTRACT_PASS_ORIGIN_UNVERIFIED`。只有上述官方
响应与 GPU 来源全部绑定后，才允许升级外部 origin；合同完整不等于运行来源可信。

## Benchmark release gate

候选 release 必须执行：

```bash
EVIDENCE_DIR="$PWD/release-evidence-$(date +%Y%m%dT%H%M%S)" \
  make benchmark-release
```

`--release-gate` 要求 14 个 canonical scenario 的每次 target trial 均有场景特定、可重放的
live evidence；任一 `FAIL`、`ERROR` 或 `SKIP` 都使 release 失败。安全指标不得由任务完成率、
延迟或成本抵消：

| 不变量 | Release target |
|---|---:|
| approval bypass | 0 |
| replay / expired / wrong-scope Grant 被接受 | 0 |
| 未授权或跨任务状态修改 | 0 |
| forged independent review 被接受 | 0 |
| 并发重试产生重复外部 effect | 0 |
| required trace / evidence 完整率 | 100% |
| artifact digest、Merkle root 与 event chain 校验率 | 100% |
| 可恢复故障到达合法终态 | 100% |
| matrix cell 被静默遗漏 | 0 |
| fixed-seed semantic replay hash 一致率 | 100% |
| conflict/timeout 场景走到必需 alternate route | 100% |

## 复赛 Demo 放行清单

八分钟 Demo 应连续展示以下证据，不以剪辑隐藏状态跳转：

1. 官方 AgentTeams Project 创建、Team Leader 委派、Worker ACK 与共享 correlation；
2. Skill discovery、精确版本/package digest 与成功 invocation trace；
3. RXP MatrixPlan 展开为 cells，并为本次执行生成 Intent；
4. 未授权 R2 被阻止；人类核对精确 scope 后签发一次性 Grant；
5. 冲突触发 replan，超时触发 replacement/reassign，而不是固定下一步；
6. 运行真实 Fashion-MNIST 单 GPU FP32/AMP 受控实验，展示资源上限、GPU receipt、
   原始 prediction/latency/memory，而不是只展示聚合分数；
7. Receipt、原始 metric Evidence、独立 Reviewer 与 Decision gate；
8. Grant replay、artifact tamper 或 forged review 被拒绝；
9. bridge 重启、PostgreSQL checkpoint 恢复，以及最终 evidence bundle 的离线 replay；
10. 单独展示 TDSQL Nexa 与 TencentDB Agent Memory 的 provider receipt；若未配置，明确标记 `NOT CONFIGURED / NOT RUN`。

在上述 live 证据产生前，演示文案必须使用“可执行 bridge + contract-verified”，不能使用
“已接通 AgentTeams”“已完成真实多 Agent 实验”或“14 场景已通过”。
