# EgoAgentOS 输入 / 输出合同

EgoAgentOS 的主输入不是一句开放式 prompt，而是一组冻结、可寻址、可审批的研究合同；
主输出也不是一段模型总结，而是能回放和独立复核的决策证据链。

## 系统输入

| 输入 | 核心字段 | 作用 |
|---|---|---|
| `ResearchGoal` | objective、硬件/预算约束、candidate arms、acceptance metrics | 冻结研究问题和成功标准 |
| `ContextBundle` | repository/data refs、历史失败、context version/digest | 给 Planner 最小可信上下文 |
| `MatrixPlan` / `ExperimentPlan` | baseline、候选臂、seeds、固定 split、验伪条件、rollback | 在执行前承诺完整实验矩阵 |
| `RunManifest` | git commit、config/dataset/environment/base-model digest、seed | 固定每个实际执行单元 |
| Approval / RXP `Grant` | action digest、scope、资源上限、TTL、单次消费 token | 授权 R2/R3 副作用，不授权更大范围 |
| AgentTeams envelope / external artifact | task/project/trace/correlation/context IDs、artifact bytes/digest、upstream receipt | 将协作结果绑定到正确任务和因果链 |
| Raw metric series | baseline/candidate paired samples、metric rule、bootstrap seed | 交给确定性 evaluator 计算，禁止模型代算 |

## 系统输出

| 输出 | 内容 | 验收语义 |
|---|---|---|
| Task snapshot + event chain | 当前 stage/version/agent、hash-chained events | 证明状态如何迁移以及谁做了什么 |
| Approval receipt | 被审批 action/digest/scope/expiry 与 token 消费事实 | 不持久化明文 bearer token |
| RXP ledger | `MatrixPlan → Intent → Grant → Receipt → Evidence → Decision` | 证明完整矩阵、父子因果和遗漏 cell |
| Evidence ledger | code/config/dataset/log/metric/trace/review 的内容摘要与 Merkle root | 缺证据时在 VERIFY fail closed |
| Evaluation results | 原始样本、均值/相对变化、固定种子 CI、PASS/FAIL/INCONCLUSIVE | 数值由版本化代码重算 |
| Independent review + Evidence Gate | reviewer 身份、完整 evidence digest set、verdict、missing list | Executor/Evaluator 不能自证成功 |
| Final `Decision` | KEEP/DROP/INCONCLUSIVE、rationale code、证据根 | 只有 gate 通过后才能提交 |
| Memory / Skill candidates | evidence-linked candidate、独立 validation/publish 状态 | 模型猜测不能直接成为 validated memory |
| Acceptance bundle | 冻结输入、原始输出、redacted receipts、trace、database、checksums | 可离线复核，不把配置状态冒充在线运行 |

## 本次 EgoLite 模型面实测

输入是 `examples/egolite/goal.yaml` 与 `experiment-plan.yaml`，并绑定当前 git commit、
模型名和四个角色合同。输出包含四份模型角色结果及摘要 receipt、一次实际本地审批/令牌
消费/状态机回放、独立复核、`acceptance.json` 和 `SHA256SUMS.json`。

真实性标签必须一起阅读：外部模型调用为 `LIVE`，本地控制面为 `LIVE_LOCAL`，EgoLite
指标仍为 `SYNTHETIC_FIXTURE`。2026-09-02 的独立本地验收已将官方 AgentTeams Controller、
Manager、四个 Worker 与 Matrix 升级为 `LIVE_LOCAL`；Project 保持 GPU gate 暂停，物理 GPU、
完整 workflow、Skill tool trace 与终态 Decision 仍为 `NOT_RUN`。详见
[`acceptance/live-local-2026-09-02.md`](acceptance/live-local-2026-09-02.md)。
