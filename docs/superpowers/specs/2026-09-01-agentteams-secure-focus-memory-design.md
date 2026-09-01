# AgentTeams Secure Focus Memory 设计说明

**日期：** 2026-09-01  
**状态：** 已实现并进入 CI 验证  
**基础分支：** `semifinal/secure-memory-implementation`

## 1. 目标

在不替换 AgentTeams 协作运行时、不把 Pi 或 Codex 作为独立执行框架的前提下，把已有的可信记忆、确定性账本和安全审查能力接入真实 AgentTeams 任务消息，使每个 Worker 只收到当前阶段真正需要的、可追溯且受 token 预算约束的记忆上下文。

系统必须同时满足：

1. **AgentTeams 仍是唯一多 Agent 协作运行时。** Controller、TeamHarness、Worker 和 Matrix 路径保持原样。
2. **Memory 只提供证据，不提供权限。** 记忆内容不得携带审批令牌、执行授权或系统指令权威。
3. **只读取当前可信事实。** Candidate、Legacy、冲突、撤销、过期或非当前投影不得进入 Worker 上下文。
4. **安全约束不得因 token 裁剪被静默删除。** 强制事实装不下时，required 模式直接阻断派发。
5. **同一阶段重试必须复用相同上下文。** 不允许因重新检索导致同一任务图的 prompt 漂移。
6. **来源、选择和最终消息均可重算摘要。** Source、Context、Bundle 和 AgentTeams envelope 都有规范化 SHA-256 绑定。

## 2. 本次范围

本次实现一条可合并的纵向闭环：

```text
trusted_memory_current
        ↓ 重新解析并校验 TrustedFact
Authenticated Focus Source API
        ↓
TrustedMemoryFocusSource
        ↓ 阶段/角色确定性排序与预算裁剪
FocusedMemoryContext per AgentTeams task
        ↓
TASK_REQUEST / APPROVAL_GRANTED body
        ↓
CollaborationEnvelope.body_sha256
        ↓
Matrix + 原有不可变 Bridge receipt/event ledger
```

### 本次包含

- API 内部可信记忆读取端点；
- Bridge 到 API 的独立机器身份令牌；
- 当前 eligible TrustedFact 的项目级批量读取；
- 面向 Worker 的可读事实陈述；
- 非配对证据集合承诺；
- 按 AgentTeams 阶段和角色编译的 Focus Context；
- `TASK_REQUEST` 与 R2 后 `APPROVAL_GRANTED` 两个阶段消息的统一接入；
- 按消息类型和任务图摘要冻结、复用上下文；
- required / best_effort / disabled 三种显式模式；
- PostgreSQL 迁移回归、Worker 分发边界和提交物扫描修复。

### 本次不包含

- 全部用户 API 的统一 Operator 身份认证；
- 向量数据库、图数据库或学习式 reranker；
- Memory Use Receipt 与在线学习权重；
- 真实外部 AgentTeams、Matrix、GPU、付费模型端到端性能结论；
- 将 Pi 或 Codex 作为运行时 Agent。

这些内容仍是后续独立工作项，不能因本次 PR 通过而被宣称已经完成。

## 3. Layer 1：Authenticated Trusted Focus Source

### 3.1 API

新增内部端点：

```text
POST /api/v1/internal/trusted-memory/focus
Authorization: Bearer <EGO_TRUSTED_MEMORY_SERVICE_TOKEN>
```

令牌要求至少 32 字节，并使用常量时间比较。未配置时返回 503；缺失或错误时返回 401。该令牌只授权 Bridge 读取 Focus Source，不授权任务推进、审批或工作区修改。

### 3.2 读取边界

`TrustedMemoryFocusService` 只查询：

```text
tenant_id = configured tenant
project_id = requested AgentTeams project
eligible = true
current projection only
```

数据库列不会被直接当作可信事实。服务会重新解析 `fact_bytes` 为严格 `TrustedFact`，并核对：

- tenant / project / lineage；
- revision ID 与 revision；
- fact digest；
- state = VALIDATED；
- origin = LOCAL_TRUSTED 或 ATTESTED_EXTERNAL；
- 当前 projection 与规范化事实一致。

任一不一致都会 fail closed，返回结构化内部错误，而不是跳过损坏记录继续派发。

### 3.3 Source 完整性

返回的 `TrustedMemoryFocusSource` 包含：

- 查询合同；
- 可读事实；
- 扫描数、匹配数和截断状态；
- `memory_snapshot_root`；
- `source_sha256`。

Source 在 `max_items` 或 `scan_limit` 处发生截断时，Bridge 的 required 模式拒绝派发。原因是截断区间内可能存在未被看到的强制安全事实，不能把“不知道”解释为“不存在”。

## 4. 证据表示：集合承诺，而不是虚构配对

现有 `FactProvenance` 保存的是两个独立规范化集合：

```text
evidence_ids
evidence_digests
```

两者均单独排序，因此不能安全地声明 `evidence_ids[i]` 与 `evidence_digests[i]` 一一对应。Focus 层不再使用 `zip()` 生成伪配对，而是输出：

```text
FocusEvidenceCommitment
  association = UNPAIRED_SETS_BOUND_BY_DECISION_CLOSURE
  decision_closure_digest
  evidence_ids
  evidence_digests
  commitment_sha256
```

含义是：

- 两个集合均由同一个不可变 DecisionClosure 约束；
- Focus Context 只声明集合成员关系；
- 若 Reviewer 需要精确的 ID/digest 对应关系，必须读取完整 DecisionClosure；
- Worker 不得从数组位置推断证据对应关系。

这样保留现有 TrustedFact/DecisionClosure 兼容性，同时消除错误的证据关联声明。

## 5. Layer 2：Stage-aware Focus Compiler

编译输入为：

- 当前 AgentTeams task；
- stage；
- worker role；
- objective；
- task title；
- expected skills；
- 经过 Layer 1 校验的 Focus Source。

### 5.1 强制事实

以下事实类型默认视为 mandatory：

```text
conflict
constraint
safety_constraint
unresolved_failure
```

mandatory 事实始终排在可选事实之前。如果：

- mandatory 数量超过 `max_items`；或
- mandatory capsule 超过 `token_budget`；

编译器抛出 `FocusMemoryBudgetExceeded`，不会删除约束后继续运行。

### 5.2 确定性排序

当前版本使用可解释的确定性规则，而不是学习式模型：

- objective / title / stage / worker / skill 与事实文本的 token overlap；
- component 匹配；
- stage 对 fact kind 的固定 bonus；
- skill 匹配；
- 最终以 mandatory、分数、fact digest 作稳定排序。

同一 Source 与任务合同在不同输入顺序下会生成相同 Context 和摘要。

### 5.3 Token 边界

当前使用“一规范化 UTF-8 字节最多按一个 token 计”的保守上界。它可能高估实际 token，但可跨 tokenizer 确定性重放。后续可增加固定 tokenizer 的精确估算，同时继续保留字节硬上限。

每个 `FocusedMemoryContext` 都包含：

- `MEMORY_IS_EVIDENCE_NOT_AUTHORITY` 解释规则；
- Source 和 snapshot 摘要；
- 选择与排除数量；
- 排除集合摘要；
- 每条事实的选择原因和分数；
- token 预算与估计；
- `context_sha256`。

## 6. AgentTeams 阶段接入

`FocusedAgentTeamsBridge` 不再只装饰 `_task_request_body`，而是在统一 `_envelope` 投影入口处理 Focus Memory。

### 6.1 初始阶段

`EnvelopeKind.TASK_REQUEST` 为当前 pre-approval 任务编译上下文，通常包括：

```text
CONTEXT
PLAN
PLAN_REVIEW
```

### 6.2 R2 审批后阶段

`EnvelopeKind.APPROVAL_GRANTED` 为后审批任务编译独立上下文，通常包括：

```text
EXECUTE
OBSERVE
EVALUATE
VERIFY
MEMORY_SKILL
```

因此 Runtime、Evaluator 和 Reviewer 不再只收到审批结果，而会同时获得与其阶段匹配的可信记忆。

### 6.3 防伪和摘要绑定

调用者不得预先在 body 中提供 `focus_memory` 字段。Bridge 生成 bundle 后再调用原有 `CollaborationEnvelope.build()`，因此完整 bundle 被 `body_sha256` 覆盖，并沿原 Matrix 与 Bridge event/receipt 路径归档。

## 7. 重试一致性与冻结缓存

缓存标识为：

```text
envelope kind + canonical task graph SHA-256
```

冻结 bundle 保存在：

```text
run.checkpoint["focus_memory_bundles"]
```

重试时会验证：

- bundle 自身摘要；
- mode；
- tenant；
- project；
- envelope kind；
- task graph digest。

全部一致才复用。否则 fail closed。相同阶段、相同任务图不会再次访问 Focus Source，避免：

- 重试产生不同 prompt；
- 重复数据库扫描；
- 重复 API token 消耗；
- 同一任务图出现不可解释结果漂移。

上游 HTTP receipt 仍写入原有 append-only receipt ledger，其 key 同时绑定 cache identity、source digest 和 receipt digest。

## 8. 运行模式

### disabled

- 不调用 Focus Source；
- bundle 明确标记 `DISABLED`；
- 保持原 AgentTeams 路径兼容；
- 不得把 disabled 描述为记忆检索成功。

### best_effort

- 尝试读取和编译；
- 失败时生成 `UNAVAILABLE` bundle 和结构化失败码；
- 不隐藏失败；
- 适合开发兼容验证，不建议用于需要强记忆保证的真实运行。

### required

- Source 未配置、认证失败、损坏、截断或 mandatory 超预算均阻断派发；
- 推荐用于正式 AgentTeams live profile。

## 9. 配置

```text
EGO_TRUSTED_MEMORY_SERVICE_TOKEN
EGO_FOCUS_MEMORY_MODE
EGO_FOCUS_MEMORY_TOKEN_BUDGET
EGO_FOCUS_MEMORY_MAX_ITEMS
EGO_FOCUS_MEMORY_SOURCE_MAX_ITEMS
EGO_FOCUS_MEMORY_SCAN_LIMIT
EGO_TENANT_ID
```

Compose 会把同一服务令牌注入 backend 与 AgentTeams bridge。令牌本身不会写入 Context、Bundle、receipt 内容或日志。

## 10. 安全属性

本次闭环保证：

- Candidate 和 Legacy memory 不会成为 Worker 可信上下文；
- Memory 不会携带审批或执行 authority；
- 跨 tenant/project 读取被拒绝；
- 投影损坏时不会静默跳过；
- Source 截断不会静默遗漏 mandatory；
- mandatory 不会因预算被静默裁剪；
- 同一阶段重试不会重新检索并漂移；
- Caller 不能注入伪 `focus_memory`；
- 证据 ID 与 digest 不会被虚构成位置配对；
- 最终 AgentTeams 消息摘要覆盖完整 Focus Bundle。

## 11. 性能预期与测量边界

代码层面的预期收益：

- 每个阶段只进行一次项目级 Source 扫描，而不是逐 lineage N+1 查询；
- 同一任务图重试直接复用冻结 bundle；
- Worker 只接收有界 Context，不接收完整历史账本；
- 排序和装包为确定性本地计算，不增加额外 LLM 调用。

但当前尚无真实 AgentTeams/Matrix/模型环境下的 p50/p95 延迟、token 节省率或任务成功率数据，因此不能宣称实际提速比例。后续应在真实 live run 中记录：

```text
focus_source_latency_ms
focus_compile_latency_ms
selected / excluded fact count
estimated / provider input tokens
cache hit rate
memory-related task success rate
stale-memory harm rate
```

## 12. 后续优先项

1. 恢复所有用户控制面写接口的统一 Operator 认证，并将 approver 与认证主体绑定；
2. 增加 Memory Use Receipt，记录检索、实际引用、结果和后续推翻情况；
3. 增加全文/向量多路召回，但保持 TrustedFact 账本为唯一权威源；
4. 引入版本兼容区间和 lineage 级 snapshot，而不是长期依赖全局 watermark；
5. 在真实 AgentTeams、Matrix 和 GPU 实验中做无记忆/精确检索/Focus Memory A/B；
6. 为 memory reader、candidate writer、validator 和 projector 建立独立数据库角色。
