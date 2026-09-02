# EgoAgentOS 最终提交说明

## 一句话

EgoAgentOS 是 AutoResearch 的确定性操作系统：Agent 负责提出与解释，操作系统负责把研究
输入编译成实验树和矩阵，用一次性 RXP Token 授权每次实验，用独立 Reviewer 与 Evidence
Gate 决定是否采信，并在每个阶段把各 Agent 的注意力压缩成可追溯的新鲜记忆。

## 作品创新

1. **Research eXecution Protocol (RXP)**：一次实验不是一句 prompt，而是
   `Intent → Grant → Receipt → Evidence → Decision` 的哈希因果链；矩阵缺一个 cell 都不能完成。
2. **能力阶梯编译器**：完整方案、模糊 idea、只有 baseline 三种输入最终落到同一显式树和矩阵；
   低信息输入的模板扩展会标记 `SYNTHETIC_FIXTURE/model_call=NOT_RUN`，不伪称模型创新。
3. **双重记忆面**：TencentDB Agent Memory 承担隔离的 L0–L3 上下文；每 Agent 单独 SQLite +
   `FOCUS.md` 提供可读、可重放的本地投影。每个阶段自动 archive/compact，而不是无限增长聊天记录。
4. **不可讨价还价的资源 Reviewer**：它不评价研究“想不想做”，只判断计划是否浪费或不可恢复；
   人类审批不能覆盖 VETO，只能修改计划。
5. **Agent-native data authority**：TDSQL Nexa 是生产事务/证据权威，复用 SQL 事务、权限、
   append-only 和事件通知；腾讯端未配置时 fail closed。

## 输入

统一 `ResearchInput` 至少需要 `title/objective/baseline`，可进一步提供 `idea`、`proposal`、
`branches`、`hierarchy`、`core_code`、metrics、folds、seeds 和仓库 URL。资源执行前还要提供
CPU/GPU 预算、并发、row shards、checkpoint/resume、缓存、barrier、validation 隔离和输出分区。

## 输出

- 标准化 proposal 和 truth label；
- 层次化 experiment tree；
- 完整 fold × seed × runnable leaf matrix；
- 每 cell 唯一 `rxpi_` token 与全局 digests；
- 独立资源 PASS/VETO receipt；
- 13 阶段审批/执行/评测/复核/Decision trace；
- 每 Agent 的 L0 database、`FOCUS.md` 和 compact receipt chain；
- 可离线核验的证据与最终 acceptance ZIP。

## 演示路径（8 分钟）

1. 首屏切换三种输入，点击 `Compile research tree`，展示 tree、165 cells 和 compact；
2. 打开 Ego3D B 支线，解释为什么 `head = optical center` 不可辨识，以及 B1–B7 的逐级门；
3. 提交故意糟糕的资源计划，在 `human_approved=true` 下仍得到八类 VETO；
4. 提交安全计划，进入正常 R2 human approval；重放 token 被拒；
5. 展示 RXP 两个 cell 的完整 digest chain 和 Evidence Gate；
6. 完成 planner/reviewer 两个阶段，展示两个不同 SQLite、两个 `FOCUS.md` 和前后字符数；
7. 查看 `/api/v1/research/storage`：本地为 `LIVE_LOCAL`，Nexa/Agent Memory 无凭据时为
   `NOT_CONFIGURED`；
8. 运行测试与 ZIP verifier，最后展示 `SHA256SUMS`。

## 当前实证边界

| 项目 | 状态 |
|---|---|
| Research Compiler / tree / matrix / resource veto | `LIVE_LOCAL · PASS` |
| per-agent SQLite + FOCUS.md compact | `LIVE_LOCAL · PASS` |
| secure memory / control-plane / RXP tests | `LIVE_LOCAL · PASS` |
| Agnes 风格静态 Demo | `SYNTHETIC_FIXTURE · browser-tested` |
| OpenAI-compatible 外部模型 harness | `LIVE · PASS`：4/4 HTTP 200，4 角色 schema 校验、独立复核与离线哈希重验通过 |
| TDSQL Nexa instance | `NOT_CONFIGURED / NOT_RUN` |
| TencentDB Agent Memory instance | `NOT_CONFIGURED / NOT_RUN` |
| 官方 AgentTeams Controller + Matrix + GPU | `NOT_RUN`，不能由本地角色标签代替 |

本轮 receipt：`trace_8f4e3f93754b4d4faafa4c7fa962373a`。它验证的不是 Ego3D 模型精度，
而是外部模型角色输出进入本地确定性 Research Compiler、资源门、13 阶段控制面和 per-agent
compact 后仍可完整离线复核。完整输入、输出、时序、摘要与限制见
[`acceptance/2026-09-02-final-live-model.md`](acceptance/2026-09-02-final-live-model.md)。

## 评委入口

- Demo：<https://mythrise.github.io/ego_agent_infra/>
- 源码：<https://github.com/mythrise/ego_agent_infra>
- 三级输入示例：[`../examples/ego3d_b_branch/input.yaml`](../examples/ego3d_b_branch/input.yaml)
- 数学与测试方案：[`../examples/ego3d_b_branch/B_BRANCH_DESIGN.md`](../examples/ego3d_b_branch/B_BRANCH_DESIGN.md)
- 数据面合同：[`agent-native-data-plane.md`](agent-native-data-plane.md)
- 复赛记分卡：[`semifinal-scorecard.md`](semifinal-scorecard.md)

## 最终 ZIP

```bash
uv run --python 3.9 python scripts/build_final_release.py \
  --evidence-dir artifacts/runtime/egolite-agentteam-20260902-final
```

构建器先调用独立 verifier 校验 evidence 目录，再扫描凭据，最后把源码、B 支线编译结果、
四角色原始输入/输出/receipt、控制面、私有 SQLite/`FOCUS.md` 与 `SHA256SUMS.json` 一起写入
确定性 ZIP。任何哈希不一致或疑似密钥都会拒绝出包。
