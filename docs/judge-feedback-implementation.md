# 复赛评委意见落实说明

日期：2026-08-29  
适用分支：`semifinal/integration`  
规则：代码存在、合同测试通过与外部平台真实运行是三种不同证据，不互相替代。

## 结论

本轮把 EgoAgentOS 从“本地多角色演示”进一步收紧为两条可独立验收的主链：

1. **AgentTeams + 单 GPU 实验链**：已经实现从计划、R2 人工审批、官方服务适配、
   受限 GPU workload、确定性指标重算、独立复核到 Decision 与一键验收包的完整代码；
   在没有官方 Controller、Matrix、GPU 调度器和可认证外部来源时，最终状态只能是
   `CONTRACT_PASS_ORIGIN_UNVERIFIED`，不能升级为 live PASS。
2. **PostgreSQL / PolarDB-PG 数据链**：SQLite 保留为开发后备；PostgreSQL 成为可部署
   控制面，增加 JSONB、事务、RLS、细粒度角色、只追加触发器、LISTEN/NOTIFY、迁移校验、
   fresh-schema replay 与 PolarDB fail-closed preflight。真实本地 PostgreSQL 16 可验证 SQL
   合同；PolarDB、PITR 与多可用区演练仍必须由云端报告证明。

## 一、多 Agent 真实实验链

### 端到端状态与责任边界

```text
ResearchGoal / MatrixPlan
  → AgentTeams Project + TeamHarness
  → Architect 提交计划
  → Reviewer 计划复核
  → EgoAgentOS R2 pause
  → Human exact-scope Grant
  → Runtime 单次 GPU launch
  → raw metrics + predictions + telemetry + checkpoint
  → Evaluator 确定性重算
  → Independent Reviewer 绑定完整 evidence digest set
  → EgoAgentOS Evidence Gate
  → RXP Decision + task Decision
  → content-addressed acceptance bundle
```

AgentTeams 负责协作、委派、ACK、Matrix 消息与动态恢复；EgoAgentOS 负责状态、授权、
证据门和最终 Decision；RXP/1 负责一次实验从承诺到验收的因果完整性。Matrix 消息不是
授权源，Worker 自报的 `PASS` 也不是评测结果。

### 受限真实 GPU workload

实现位于 [`experiments/fashion_mnist_amp/`](../experiments/fashion_mnist_amp/)。该任务使用
Fashion-MNIST、一个小型 CNN 和单张 CUDA GPU，对同一冻结测试样本比较 FP32 与 AMP：

- 仅一个可见 GPU，无 CPU fallback，无任意 shell 字段；
- 最长 900 秒、最多 0.25 GPU-hour、一次 physical launch；
- config、environment lock、approval receipt、AgentTeams receipt 和 MatrixPlan 均按实际
  文件字节计算 SHA-256，不能用调用方自报 digest 替代；
- 保存逐样本预测、逐次 latency、GPU UUID/利用率/显存/功耗、模型状态和完整 metric matrix；
- CPU verifier 独立重算指标与 Decision，并拒绝 NaN、重复样本、越界资源、digest 漂移、
  多 GPU、缺失 receipt 或不完整矩阵。

“执行成功”与“研究假设成立”严格分离：候选可能 KEEP，也可能 REJECT。

### AgentTeams bridge 与恢复

实现位于 [`apps/agentteams_bridge/`](../apps/agentteams_bridge/)：

- 真实任务必须显式 `synthetic=false`，并固定 external source binding；
- 存档 upstream 原始响应、workflow、artifact receipt、Matrix raw messages、Reviewer
  decision 与 EgoAgentOS finalization receipt；
- conflict 可 replan，timeout 可 cancel/replacement/reassign；
- R2 审批前保持 pause，审批后才 resume；
- terminal Matrix 发送失败进入 compensation，checkpoint 恢复不能重复 finalization 或
  GPU side effect；
- 最终任务必须经过 `OBSERVE → EVALUATE → VERIFY → DECIDE → ARCHIVE → MEMORY_SKILL
  → COMPLETED`，不能由 bridge 直接写终态。

Bridge 可选 PostgreSQL backend 用于重启、并发与多副本；SQLite 仅是显式开发 fallback。

## 二、RXP/1：实验承诺与验收协议

RXP/1 把“token 代表一次实验”的创意具体化为每个 Matrix cell 的一次性 Grant：

```text
MatrixPlan
  └─ Cell
      └─ Intent → Grant → Receipt → Evidence → Independent Decision
```

Grant 绑定 task/generation、cell、action digest、资源上限、expiry 与 nonce；Receipt 必须绑定
Grant 和真实 effect；Evidence 必须绑定原始 artifact；Decision 必须由确定性 policy 与独立
Reviewer 共同约束。MatrixLedger 同时提交 expected cells、decided cells、missing decisions、
evidence Merkle root 与追加式 ledger root，从结构上阻止“漏跑不利实验格后选择性汇报”。

RXP 不替代 MCP、AgentTeams/A2A、MLflow 或 PROV-O：它定义的是研究实验的授权、因果与验收
不变量，可由这些系统承载。规范与 reference verifier 位于
[`protocols/rxp/`](../protocols/rxp/)；比较边界见
[`docs/protocols/RXP-comparison.md`](protocols/RXP-comparison.md)。

## 三、一键验收包与防伪边界

[`semifinal_acceptance/`](../semifinal_acceptance/) 将以下材料冻结为 content-addressed bundle：

- typed Matrix 事件与顺序；
- 人工审批和 AgentTeams 原始 receipt；
- RXP Intent/Grant/Receipt/Evidence/Decision；
- 原始 metric matrix、GPU telemetry 与资源账单；
- Reviewer 复核、Evidence Gate、失败恢复 checkpoint/fencing/MTTR；
- 主 trace、Matrix root、manifest 和顶层 Decision。

Verifier 交叉核对 raw metric policy、主 trace Decision、RXP Decision 与顶层 Decision，并重算
receipt 唯一性、Matrix root、GPU 时间、恢复链和所有 digest。负测覆盖来源冒充、receipt 重用、
空 Matrix 内容、Decision 冲突、负 telemetry 和 artifact 篡改。

当前 v1 有意不接受调用方把外部来源标记为已认证，只允许
`UNVERIFIED_OPERATOR_ASSERTION`。因此即使合同全部通过，机器状态也必须是：

```json
{
  "contract_gate_status": "PASS",
  "verification_status": "CONTRACT_PASS_ORIGIN_UNVERIFIED",
  "external_origin_status": "UNVERIFIED",
  "live_claim_allowed": false
}
```

只有平台签名、调度器 job identity、Matrix 原始事件与可信时间/身份链被独立验证后，后续协议
版本才可以定义 live claim 升级路径。

## 四、PostgreSQL / PolarDB-PG 落实

### 并发与多副本

- [`apps/api/postgres_store.py`](../apps/api/postgres_store.py) 使用事务、MVCC、optimistic
  version check 和 per-stream advisory lock；
- task、approval、evidence、memory 与 audit payload 使用 JSONB；
- `audit_events` 的前驱校验和 sequence 分配在数据库触发器内完成；
- transaction commit 后通过 `pg_notify('ego_stage_events', ...)` 发出事件，API SSE 用 durable
  audit cursor 重放，NOTIFY 只负责唤醒，不承担持久性；
- `EGO_DATABASE_MIGRATION_MODE=verify` 允许受限 runtime 只校验迁移 checksum，不要求 DDL
  权限；迁移由独立 owner 执行。

### 库层最小权限

[`deploy/postgres/security_roles.sql`](../deploy/postgres/security_roles.sql) 定义四个 NOLOGIN
组角色：

| 角色 | 可读 | 唯一写边界 |
|---|---|---|
| `egoagentos_runtime` | 控制面 tenant 数据、迁移版本 | task/approval 状态与各追加账本所需 INSERT |
| `egoagentos_auditor` | 全部审计视图 | 无写权限 |
| `egoagentos_evidence_writer` | task、approval、evidence | 仅 INSERT evidence |
| `egoagentos_memory_curator` | task、evidence、candidate、validated memory | 仅 INSERT `memory_candidates` |

Memory Curator 不再直接写 validated memory。应用先追加 candidate；独立 Evidence Gate 通过后，
`memory-validator` 才把 candidate 晋升到 `memories`，并在 audit chain 中分别记录提议和晋升。
`evidence`、`memory_candidates`、`memories` 的 UPDATE、DELETE、TRUNCATE 均由数据库触发器拒绝。
所有 tenant 表启用 RLS；是否 `FORCE RLS` 由 live manifest 单独设为必需检查，避免把普通 RLS
误报成 owner 隔离证明。

### Replay、恢复与 PolarDB 验收

[`apps/api/polardb_preflight.py`](../apps/api/polardb_preflight.py) 默认只读并输出脱敏 JSON：

- TLS、数据库名、引擎 marker、server version、writer/reader topology；
- JSONB、可选 pgvector、迁移版本与 checksum；
- RLS policy、四角色 privilege matrix、专用 login；
- audit/ledger immutable triggers 与 LISTEN/NOTIFY；
- fresh-schema replay 的多重防误删门。

PITR、备份、读写分离与多可用区故障转移不由本地 CLI 冒充。它们必须按
[`docs/polardb-live-acceptance-runbook.md`](polardb-live-acceptance-runbook.md) 保存云端 operation
ID、恢复时间点、恢复库校验、RPO/RTO、event-chain replay 和 teardown 记录。

## 五、严格 benchmark 与放行规则

14 类 canonical scenario 覆盖 happy path、审批绕过、过期/错 scope/replay Grant、伪造复核、
artifact 篡改、并发重复 effect、证据缺失、matrix omission、conflict/replan、timeout/reassign、
checkpoint 恢复与 adapter 欺骗。每个场景由 benchmark-owned oracle 决定结果，adapter 自报
状态无效。

Release gate 的不可妥协指标是：

| 指标 | 目标 |
|---|---:|
| 未授权或重放 Grant 被接受 | 0 |
| 重复 external effect | 0 |
| forged review 被接受 | 0 |
| required trace/evidence 完整率 | 100% |
| digest、Merkle root、event chain 校验率 | 100% |
| matrix cell 静默遗漏 | 0 |
| 可恢复故障到达合法终态 | 100% |

缺少 live capability 或来源认证必须记为 `SKIP`/`UNVERIFIED`，并使 release gate 失败；不能用
本地 synthetic/fixture PASS 抵消。

## 六、尚未完成的外部验收

以下项目需要新的外部资源或操作授权，本仓库当前不声称已完成：

- 官方 AgentTeams Controller、Team/Worker、Matrix 同一运行链；
- 一次真实单 GPU Fashion-MNIST 受控实验及可认证调度来源；
- PolarDB-PG writer/reader、四个专用登录和 provider identity；
- PITR、备份恢复、多可用区 failover 与实测 RPO/RTO；
- production key custody、可信时间戳或外部透明日志。

建议的最低成本 live 验收预算为一张 GPU、一次 physical launch、最多 900 秒 / 0.25
GPU-hour；云数据库演练必须另设费用上限、临时恢复实例和 teardown owner。任何实际运行前仍需
操作者确认资源、凭据与费用授权。
