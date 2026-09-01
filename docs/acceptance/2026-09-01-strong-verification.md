# 强验证档：D_EVIDENCE_LAYERED_V1

本档记录当前可复现的本地/静态验证结果。AgentTeams Controller、TeamHarness 和
Matrix 是唯一协作运行时；Pi/Codex 仅作为设计参考，未被导入或执行。

## 已通过

| 检查 | 命令 | 结果 |
|---|---|---|
| AgentTeams、Memory、secure-memory 回归 | `.venv/bin/pytest -q tests/agentteams tests/memory tests/secure_memory` | 全部通过，1 个明确的环境条件 skip |
| MCP 安全/工作区网关 | `cd mcp_servers && PYTHONPATH=src .venv/bin/pytest -q` | 51 passed |
| Worker wheel/sdist 公共边界 | `.venv/bin/pytest -q tests/secure_memory/test_canonical_and_manifest.py -k 'worker_wheel or worker_sdist or combined_digest_index'` | 19 passed |
| Workspace authority 伪造拒绝 | `tests/agentteams/test_workspace_authority.py` | 2 passed |
| schema 与 digest | `apps.agentteams_bridge.extensions.schema_contract.export_schema_contract()` | 5 个 AgentTeams schema digest 与索引一致 |

## 安全闭包

- WorkspaceExecutor 没有可信 `EffectAuthorityVerifier` 时立即拒绝；它不接受仅凭自洽
  digest 构造的伪造 effect。
- Control-ledger verifier 只从不可变 extension event 链重放 campaign、canonical
  effect、Guardian、SafetyDecision，并重新生成 workspace wire projection。
- 普通状态只显示当前层和直接下一层；RISK、APPROVAL、SECURITY 事件可覆盖深度限制。
- 所有展示的专业术语来自固定中英文 glossary；未知术语或 authority/instruction prose
  会 fail-closed。
- Layer 1 只接受 evaluator + terminal DecisionClosure 关闭的可信事实；Layer 2 是按当前
  requirement、checkpoint、失败项和 token cap 编译的 disposable attention，不具有 authority。
- 失败、补偿、回滚和未执行状态均保留在 append-only ledger；不能把 dry-run 当作 live 证据。

## 未执行（明确不是通过）

以下项目因本机没有对应外部运行条件，保持 `NOT_EXECUTED`/`DEFERRED`：

- PolarDB PostgreSQL live DSN、Docker/testcontainers、RLS 实连、并发和 PITR 恢复演练；
- 真实 Agnes API 调用、真实 GPU 实验、真实 AgentTeams/Matrix 端到端验收包；
- A/B/C/E/F 对比、optimizer、付费 provider qualification 和性能排名；
- Task 6 的 terminal Extractor 生产启用及 lease→role→checkpoint→budget 全链路实跑。

因此，本档证明的是本地确定性合同、拒绝路径、replay 和包边界；不宣称外部服务来源已认证，
也不宣称某一架构在真实 GPU/API 上优于其他方案。
