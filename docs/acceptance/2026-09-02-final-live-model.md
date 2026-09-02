# 最终真实模型验收：ResearchOS + compact memory

> 这是 17:44 CST 冻结的模型面验收快照。随后完成的官方 AgentTeams/Matrix 本地基础设施
> 验收见 [`live-local-2026-09-02.md`](live-local-2026-09-02.md)；本表中的 `NOT_RUN` 只描述
> 此旧快照本身，不能代表当天稍后的当前状态。

时间：2026-09-02 17:44 CST

Trace：`trace_fd42c6c404304e139b1ec86ee3114f39`

模型网关：`https://apihub.agnes-ai.com/v1`，model=`agnes-2.5-flash`

源码快照：`1992629fd9cb8bd091a1825c7df3c934ad38f328`

## 验收结论

| 层 | 状态 | 本轮证据 |
|---|---|---|
| 外部模型 API | `LIVE · PASS` | 4 个角色均 HTTP 200，request/response 均有 SHA-256 |
| Research Compiler | `LIVE_LOCAL · PASS` | B 支线编译为 165 cells，tree/matrix/compile digest 固定 |
| 独立资源 Reviewer | `LIVE_LOCAL · PASS` | `ALLOW_APPROVAL_GATE`；人类不能覆盖 VETO |
| 13 阶段控制面 | `LIVE_LOCAL · PASS` | 32 个 hash-chained event，Evidence Gate 7/7，最终 `KEEP` |
| per-agent compact | `LIVE_LOCAL · PASS` | 四个 Agent 各有独立 SQLite、`FOCUS.md` 和 receipt |
| EgoLite 指标 | `SYNTHETIC_FIXTURE` | 只用于验证控制语义，不是 GPU 或科研性能结果 |
| TDSQL Nexa | `NOT_CONFIGURED / NOT_RUN` | adapter-ready，但没有腾讯实例 endpoint/receipt |
| TencentDB Agent Memory | `NOT_CONFIGURED / NOT_RUN` | v3 adapter-ready，本轮只运行本地隔离投影 |
| 官方 AgentTeams / Matrix / GPU | `NOT_RUN` | 角色调用不能替代官方运行时或硬件回执 |

## 完整输入

控制面输入是 [`examples/egolite/goal.yaml`](../../examples/egolite/goal.yaml)
和 [`examples/egolite/experiment-plan.yaml`](../../examples/egolite/experiment-plan.yaml)：目标任务
`ego-lite-001`，三种轻量 backbone，R2 人工审批，24 GPU-hour 上限，验收阈值为 FPS 不低于
10 且 MPJPE 相对退化不超过 5%。两份输入摘要分别为：

- goal：`63725127709927af02e94977a032c6f10bfcb1339bda5670a5c2c45407c99760`
- plan：`1766d51a34fefcd08b192c8a5f84ac1a2918808caf747cb9a2086d1ea9959d4a`

Research Compiler 额外读取
[`examples/ego3d_b_branch/input.yaml`](../../examples/ego3d_b_branch/input.yaml)，冻结 C6/C7
headline、5 folds、3 seeds、B-DIAG 与 B0–B7 树、验收指标和资源计划。它的输出摘要是：

- compile：`c008811f4f207add92064c8f86d265d773e297e44bf9fdb89fd619b38a462580`
- tree：`4affd2e9c39258adae5cd815e99ec8c5585df79648ba62240f0c46b742d08bd4`
- matrix：`89a47d8e3952f047ddec1b05b6db18c223a985cc8b049614677e244144d89718`
- matrix cells：165

资源计划同时声明 `matrix_cells=165`、`folds=5`；编译服务把这两项与实际产物做硬绑定，
任一不一致都返回 `MATRIX_CARDINALITY_MISMATCH` 或 `FOLD_CARDINALITY_MISMATCH` 并 VETO。

API key 只从进程环境读取；receipt 固定记录 `credential_persisted=false`，证据目录和最终 ZIP
均执行密钥扫描。

## 实际过程

```text
输入冻结与 digest
  → B 支线编译：tree → 165-cell matrix → 每 cell RXP intent token
  → resource-reviewer：资源计划 PASS
  → research-pi 外部模型调用 → schema 校验 → 私有 INTAKE compact
  → scout 外部模型调用 → schema 校验 → 私有 CONTEXT compact
  → experiment-architect 外部模型调用 → schema 校验 → 私有 PLAN compact
  → 本地控制面 reset → R2 approval → execute → observe → evaluate
  → Evidence Gate 7/7 → independent verify → Decision KEEP → completed
  → reviewer 外部模型调用 → 绑定 reviewed-evidence digest → 私有 VERIFY compact
  → 全目录 SHA256SUMS → 独立离线 verifier
```

四次模型调用：

| 角色 | HTTP | 延迟 | request SHA-256 | response SHA-256 |
|---|---:|---:|---|---|
| research-pi | 200 | 3525 ms | `5ea8f95a2990ed8450c64df9a60f9b3688353f9856107ffb6406821263ef5c30` | `99478c7677c0e7facc7f45e880a0b5948589ce0c4d8a40060b0a602ca2d83c83` |
| scout | 200 | 6685 ms | `4f9cbb799f11264a097566ec1910129bf3dd8e48ecbd45e5b0ca949acbbfb572` | `1e75978f83a7e428304e2bc00abe5a2b45f1011f324711cbcdc218a03c082bb4` |
| experiment-architect | 200 | 7864 ms | `84b7eccf66dc3fd61a723c3a3a0723508ba343fa38298f9fbe4ad812a3cb0b83` | `709748656e270802119ba1c7f7776050a94c0e2f3aea3bfcbebfbfa9a11ed497` |
| reviewer | 200 | 5123 ms | `e868bedf2f34191ae02c9f86e461c91a4d6217e90236626d9d6261fc374b187d` | `6f855bb8f04a7586c4ecf853879b9a6c3f15a47bb2d239a5d73fb324b9e6b862` |

## 实际输出

- `research-pi` 保留 R2 审批门并给出五阶段研究路径；
- `scout` 固定 synthetic、预算、三个 backbone、100 样本和未执行能力边界；
- `experiment-architect` 给出五项验伪检查，并明确无 GPU 测量；
- `reviewer` 独立核对 evidence digest，结论 `PASS`，同时拒绝扩大到官方 AgentTeams、
  Matrix、物理 GPU 或真实模型质量；
- Evidence Gate 的 `code/config/dataset_manifest/log/metric/review/trace` 全部存在；
- control plane 最终 `COMPLETED`，Decision=`KEEP`；
- 离线 verifier 校验 41 个文件，`verified=true`、errors=`[]`。

成功 trace 的四个角色都在第 1 次通过，因此 `model_call_count=4`、`model_retry_count=0`。
成功前真实发生了三次被拒 trace：非 JSON、`scout` 缺少 `uncertainties`、以及
`research-pi.approval_required != true`。它们都没有进入 `COMPLETED`；恢复始终创建新目录，
最终 ZIP 同时保存这三份失败材料。当前代码又把恢复收紧为单角色最多 3 次，并在最终 receipt
中核对 `attempt/prior_failures/model_retry_count`；单测覆盖“第 1 次失败、第 2 次成功”。

## compact 细节

| Agent | 阶段 | 原始字符 | FOCUS 字符 | compact receipt SHA-256 |
|---|---|---:|---:|---|
| research-pi | INTAKE | 199 | 699 | `8fef3f074470e78cf022c4ef1ce0526358106f9f06c5cec35ec6c8c4c2c572cd` |
| scout | CONTEXT | 550 | 682 | `af7bf88a3e9b34d3115213735152f9e2bc712d3313d4e5d809beef2e4e310702` |
| experiment-architect | PLAN | 790 | 724 | `1038544f8e14d5a3bd0624d07990ba57caed7ce8a98b32021a9a1e8305a7299a` |
| reviewer | VERIFY | 777 | 690 | `04f12f1dba9cf3c258786ee19f1c945f377666f703a196029e1c69548e6a0764` |

`FOCUS.md` 不是简单截断聊天，而是只保留 validated facts、decisions、evidence、blockers 和
next actions；SQLite 保存原始 L0 与链式 receipt，下一阶段从新的 focus 投影继续。

## 复核命令

```bash
uv run --python 3.9 python -m experiments.egolite_agentteam.verify \
  artifacts/runtime/egolite-agentteam-20260902-1992629
```

预期唯一成功条件是 `verified=true` 且 `errors=[]`。修改任意被记录文件、删除任一角色 receipt、
改变 truth boundary、减少 165 cells 或把远端 memory 冒充为已配置，都会使 verifier 失败。

最终回归还通过：Python 默认套件 662 项（661 passed，1 个已有条件性 skip）、MCP 51 passed、
Web 16 passed、TypeScript/Vite production build、全仓 Ruff、B 支线 compileall 与
`docker compose config --quiet`。这些是本地代码/配置证据，不改变云端和 GPU 的 `NOT_RUN`。
