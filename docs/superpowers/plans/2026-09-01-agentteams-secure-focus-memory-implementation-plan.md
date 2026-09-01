# AgentTeams Secure Focus Memory 实施计划

> 目标分支：`codex/agentteams-secure-focus-memory`
>
> 基线：`semifinal/secure-memory-implementation@082fa6019abc973e84e54d1d7e059fa137e95de3`

## Task 1：以失败测试固定接口和不变量

新增：

- `tests/api/test_trusted_memory_focus_api.py`
- `tests/agentteams/test_focus_memory_context.py`

覆盖：

1. 内部 Focus API 未配置 token 时返回 503；
2. 缺失或错误 Bearer token 返回 401；
3. 正确 token 才能获得 digest-bound source；
4. SQLite 当前可信投影能够被项目级扫描，跨项目事实不泄露；
5. Focus Context 包含可读 statement；
6. 输入顺序不影响 source/context；
7. mandatory facts 不被 `max_items` 或预算静默裁剪；
8. Bridge 一次读取 source，为每个 AgentTeams task 生成独立 context；
9. required 模式失败会阻止派发，disabled 模式明确标记。

先提交测试并观察 CI 因缺失模块失败。

## Task 2：实现可信 Focus Source 合同

新增 `apps/api/trusted_memory/focus_contracts.py`：

- `FocusEvidenceRef`
- `FocusMemoryQuery`
- `TrustedFocusFact`
- `TrustedMemoryFocusSource`
- `build_trusted_memory_focus_source`

所有集合规范排序、去重并绑定 SHA-256；证据使用结构化 pair，避免 ID/digest 平行数组错位。

## Task 3：实现项目级可信事实读取与内部 API

新增 `apps/api/trusted_memory/focus_service.py`：

- 只扫描 `trusted_memory_current.eligible`；
- 重新解析 `TrustedFact` 并校验 scope/state/origin；
- 支持 SQLite 和 PostgreSQL；
- `scan_limit`、`max_items` 有硬上限；
- 注册 `POST /api/v1/internal/trusted-memory/focus`；
- 使用常数时间 Bearer token 校验；
- token 未配置时 fail closed。

修改 `apps/api/main.py`：

- 增加显式 `trusted_memory_service_token` 测试注入参数；
- 注册 Focus API 与 service；
- CORS 允许 `Authorization`；
- 不改变现有公共 API 行为。

## Task 4：实现阶段/角色感知的 Focus 编译器

新增 `apps/agentteams_bridge/extensions/focus_memory.py`：

- `FocusMemorySourceContext`
- `FocusedMemoryItem`
- `FocusedMemoryContext`
- `FocusMemoryBudgetExceeded`
- `build_focused_memory_context`

算法：

1. 验证 source tenant/project；
2. 规范化任务与 statement token；
3. 计算词法重合、component/skill 命中和 stage-kind 偏置；
4. mandatory 与 optional 分层稳定排序；
5. mandatory 先装包，预算不足立即失败；
6. optional 按分数和 digest 裁剪；
7. 写入 selected/excluded 摘要与 context SHA-256。

## Task 5：接入真实 AgentTeams TASK_REQUEST

新增 `apps/agentteams_bridge/focused_service.py`：

- `FocusMemoryMode`
- `FocusMemoryFetch`
- `FocusMemoryProvider`
- `EgoTrustedMemoryProvider`
- `FocusedAgentTeamsBridge`

行为：

- 一次 run 只调用一次 API；
- 每个 `ResearchTaskSpec` 独立编译上下文；
- source HTTP receipt 写入现有 Bridge receipt ledger；
- focus bundle 写入 TASK_REQUEST，最终由 envelope body digest 绑定；
- required 失败触发现有 start-run compensation；
- best-effort 失败显式记录 UNAVAILABLE；
- disabled 不发起读取。

修改：

- `apps/agentteams_bridge/main.py`
- `apps/agentteams_bridge/settings.py`
- 两个 package `__init__.py`
- `.env.example`
- `docker-compose.yml`

## Task 6：修复 PostgreSQL 迁移回归

更新两处硬编码迁移断言，期望包含：

```text
001_control_plane.sql
002_ledger_boundaries.sql
003_trusted_memory_core.sql
004_decision_closure_bytes.sql
```

目标文件：

- `tests/postgres/test_polardb_preflight.py`
- `tests/postgres/test_postgres_store.py`

## Task 7：验证与提交

依次要求：

```bash
pytest -q tests/api/test_trusted_memory_focus_api.py
pytest -q tests/agentteams/test_focus_memory_context.py
pytest -q tests/agentteams tests/memory tests/secure_memory
pytest -q tests/postgres
ruff check apps tests
mypy apps protocols benchmarks skill_runtime
```

随后检查 GitHub Actions：API、MCP、RXP、PostgreSQL、Web、submission deliverables 全部通过，再创建 PR 到 `semifinal/secure-memory-implementation`。
