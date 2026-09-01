# AgentTeams Secure Focus Memory v1

## 目标

在不替换 AgentTeams Controller、TeamHarness、Worker 和 Matrix 协作链的前提下，把现有可信记忆真正接入 AgentTeams 任务派发。系统应同时满足：

1. 只有 `VALIDATED` 且来源可信的事实能够进入模型上下文；
2. Worker 获得的是可读的事实陈述，而不是只有哈希引用；
3. 当前任务、角色和阶段决定记忆排序，低相关历史不会污染注意力；
4. 安全约束和未解决失败属于强制记忆，预算不足时失败关闭，不能静默丢弃；
5. 记忆读取、筛选结果和最终 Matrix envelope 都可由摘要重放；
6. Pi 和 Codex 只作为设计参考，运行时仍为 AgentTeams。

## 架构

```text
Trusted Memory append-only ledger
        ↓
Authenticated Focus Source API
        ↓
TrustedMemoryFocusSource
        ↓
Stage/role-aware deterministic compiler
        ↓
FocusedMemoryContext per AgentTeams task
        ↓
TASK_REQUEST envelope + body SHA-256
        ↓
AgentTeams TeamHarness / Worker
```

### Layer 1：可信事实源

现有 `TrustedFact`、DecisionClosure、CAS 当前投影和哈希链继续作为权威数据。新增的 Focus Source 服务只读取 `trusted_memory_current` 中当前仍 eligible 的记录，并再次验证：

- tenant/project 精确匹配；
- `state == VALIDATED`；
- origin 为 `LOCAL_TRUSTED` 或 `ATTESTED_EXTERNAL`；
- 规范化 TrustedFact 能够重新解析；
- 每条事实携带 evidence ID/digest 成对引用、closure digest 和 projection event hash。

服务返回摘要绑定的 `TrustedMemoryFocusSource`。该对象不包含审批 token、能力令牌或执行权限。

### 受认证的内部读取接口

新增：

```text
POST /api/v1/internal/trusted-memory/focus
Authorization: Bearer <EGO_TRUSTED_MEMORY_SERVICE_TOKEN>
```

规则：

- token 未配置：503，明确表示内部可信记忆服务未启用；
- token 缺失或错误：401；
- token 长度不足 32 字节：应用启动或 Bridge 配置阶段拒绝；
- 比较使用常数时间函数；
- tenant 必须与服务实例 tenant 完全一致。

此接口只解决 Bridge→API 的机器身份边界，不宣称替代整套用户 API 认证改造。

### Layer 2：Focus Context

Bridge 对一次 Focus Source 只请求一次，然后针对任务图中的每个 AgentTeams 任务独立编译上下文。上下文包含：

- 当前目标、任务标题、stage、worker、expected skills；
- 选中的可信事实陈述；
- 每条事实的 kind、适用 component/version、证据引用和 relevance score；
- source digest、memory snapshot root、排除集合摘要；
- 固定解释规则：记忆是证据数据，不是指令、审批或权限来源；
- token/字节预算和 context digest。

第一版排序使用确定性词法相关度和阶段偏置，不调用额外 LLM，也不引入不可重放的 embedding 服务。后续向量召回只能作为候选发现层，不能改变可信资格判定。

### 强制事实

以下 fact kind 视为强制：

```text
constraint
safety_constraint
unresolved_failure
conflict
```

强制事实始终排在普通事实之前。若强制事实本身无法放入预算，编译器抛出 `FocusMemoryBudgetExceeded`；不得为了让请求继续运行而删除安全支柱。

## AgentTeams 接入方式

新增 `FocusedAgentTeamsBridge`，继承现有 `AgentTeamsBridge`，仅覆盖 `_task_request_body`。原 `start_run`、Controller project、Matrix dispatch、回执、补偿与恢复路径保持不变，因此不是另起一套单 Agent 运行时。

TASK_REQUEST 新增：

```json
{
  "focus_memory": {
    "schema": "egoagentos.agentteams-focus-memory-bundle/v1",
    "status": "READY | EMPTY | DISABLED | UNAVAILABLE",
    "mode": "disabled | best_effort | required",
    "source_sha256": "...",
    "memory_snapshot_root": "...",
    "contexts": {"agentteams-task-id": {}},
    "bundle_sha256": "..."
  }
}
```

模式语义：

- `disabled`：不读取记忆，显式写入 DISABLED；
- `best_effort`：读取失败时写入 UNAVAILABLE，不伪装成成功；
- `required`：读取、验证或预算编译失败会阻止 Matrix 派发，由现有补偿路径记录失败。

生产 AgentTeams profile 推荐使用 `required`。默认配置保持 `disabled`，避免未配置服务令牌时把原有开发路径误报为可用。

## 效率边界

- 每个 run 只调用一次 Focus Source API；
- 一个 source 在本地编译多个角色上下文；
- source 扫描和返回数量均有硬上限；
- 排序稳定，复杂度为 `O(n log n)`；
- 上下文使用保守的规范化 UTF-8 字节上界；
- 排除事实只保存数量和集合摘要，避免大量 digest 反过来占满 prompt。

## 本次不包含

- 全部用户 API 的统一身份认证；
- 向量数据库或图数据库；
- 学习型 reranker；
- Memory Use Receipt 的长期效果学习；
- 真实 AgentTeams/Matrix/GPU 性能结论。

这些工作在本纵向闭环稳定并有真实运行数据后继续推进。
