# 复赛评分映射

本页按 2026-08-29 从登录态复赛规则页核对的五项权重映射 RXP Bench。Benchmark
只提供可机器复核的证据，不替代评委对选题与产品价值的判断。

| 复赛维度 | 权重 | Benchmark 证据 | 边界 |
|---|---:|---|---|
| 场景价值与可迁移性 | 20% | 14 个版本化故障场景；同一 corpus 可运行三个 profile；JSON/Markdown 双输出 | 固定 synthetic corpus 不能证明真实科研任务的泛化能力 |
| 多 Agent 协作 | 25% | `worker_timeout_reassign`、plan conflict、reviewer independence、dynamic routing 指标；AgentTeams target adapter | **只有真实 AgentTeams adapter 的执行结果可作为此项证据；当前缺失即 SKIP** |
| Skill 工程化 | 20% | `skill_version_rollback` 场景、版本匹配 adapter contract、可复现 runner/CI | 当前 core 没有 skill registry，故 rollback 为 SKIP，不能申报已完成 |
| 工程实现与安全审计 | 30% | token replay/expiry/scope、approval bypass=0、exactly-once、crash recovery/MTTR、audit/evidence completeness、tamper/forged-reviewer | 本地 synthetic control-plane 结果不等于生产环境安全认证 |
| 开源贡献 | 5% | 版本化 corpus、JSON Schema、profile adapter contract、golden tests、canonical raw result | 是否形成外部贡献需由公开仓库、文档与后续社区采用证明 |

## 硬门槛与否决项

复赛核心协作必须真实使用 AgentTeams，至少包含三个不同职能 Agent。固定脚本或模拟
事件不得冒充 AgentTeams，核心 Skill 必须有可运行证据，数据、trace 与结果不得伪造。
因此本 benchmark 强制以下语义：

- `scripted-negative-control-v1` 是明示的脚本化负对照，不是被测 Agent 系统，永远不能作为 AgentTeams 合规证据；
- `agentteams-rxp-target` 缺少真实、版本匹配的 adapter 时只能 `SKIP`；
- `SKIP` 降低 coverage，绝不进入 pass 分母；
- 金额成本、GPU 结果和未接入协议均为 `null`/`SKIP`；
- committed artifact 明示 synthetic、local、non-GPU 环境。

## 复赛演示建议

先运行 fixed baseline 暴露审批绕过、重复副作用和无恢复；再运行 deterministic core
展示安全指标；最后接入真实 AgentTeams+RXP adapter，重点消除 dynamic routing、skill
rollback 和 experiment matrix 三类 coverage gap。演示时展示 raw JSON 的 corpus digest、
固定 seed、每次 trial 的 implementation path，以及 Markdown 统计置信区间。
