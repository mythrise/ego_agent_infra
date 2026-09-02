# 最终真实模型验收：ResearchOS + compact memory

时间：2026-09-02 17:27 CST  
Trace：`trace_8f4e3f93754b4d4faafa4c7fa962373a`  
模型网关：`https://apihub.agnes-ai.com/v1`，model=`agnes-2.5-flash`  
源码快照：`e7faee4c2140e1fe5a98ebc9b1fd1faa7390ca74`

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

控制面输入是 [`examples/egolite/research-goal.yaml`](../../examples/egolite/research-goal.yaml)
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
| research-pi | 200 | 4341 ms | `246326460e9a93ecfb26edecd5752de996598343ed4e67b0a82b618207a140db` | `4ce45b901bc96ad798d53212f03772676dffc61d90f5fd1103b3df74c7183136` |
| scout | 200 | 4556 ms | `fcafa5d49e23e7e763d78c69692ac48bbccc0d6f8ee169189704d0de56f78c41` | `776e1a41d08b1a6f5db03ec0651766472e9d86895484dbcb8d3df22dd939f4a6` |
| experiment-architect | 200 | 5151 ms | `df945d2dd4ed57b4bc8ee4378e13cb7e686198228ffc9f31bd7eff8295bf5966` | `a96cff10ea977066f1da1dc1830d60950d14e1bcd167fc8a5caa10e07b00682a` |
| reviewer | 200 | 4384 ms | `c22059b9aa159a01c129bf41f488a28736668e3d0861319255e6a9fc5cd53285` | `429d32da0fcc39a6ebd9e09be0283210ef709ddd38a91ef42785b24fb54ecd50` |

## 实际输出

- `research-pi` 保留 R2 审批门并给出五阶段研究路径；
- `scout` 固定 synthetic、预算、三个 backbone、100 样本和未执行能力边界；
- `experiment-architect` 给出五项验伪检查，并明确无 GPU 测量；
- `reviewer` 独立核对 evidence digest，结论 `PASS`，同时拒绝扩大到官方 AgentTeams、
  Matrix、物理 GPU 或真实模型质量；
- Evidence Gate 的 `code/config/dataset_manifest/log/metric/review/trace` 全部存在；
- control plane 最终 `COMPLETED`，Decision=`KEEP`；
- 离线 verifier 校验 41 个文件，`verified=true`、errors=`[]`。

## compact 细节

| Agent | 阶段 | 原始字符 | FOCUS 字符 | compact receipt SHA-256 |
|---|---|---:|---:|---|
| research-pi | INTAKE | 321 | 699 | `af4acc22e012dd6c361249a96fc162cb248eeb98600869d56e0843f783d47dd6` |
| scout | CONTEXT | 850 | 682 | `ca9c53effb6a025de05ad2384e5060036dea2b5b2907e8f63135135be71d853d` |
| experiment-architect | PLAN | 800 | 724 | `11fe639db4179defebba938c70fd5d6ee869cc27bcfed6ad4b1324830ea9a7d9` |
| reviewer | VERIFY | 888 | 690 | `7d2c02d46fb5493244d89dd1a5591a41f8b34ab851e01d2fc49e0e1a83f119b4` |

`FOCUS.md` 不是简单截断聊天，而是只保留 validated facts、decisions、evidence、blockers 和
next actions；SQLite 保存原始 L0 与链式 receipt，下一阶段从新的 focus 投影继续。

## 复核命令

```bash
uv run --python 3.9 python -m experiments.egolite_agentteam.verify \
  artifacts/runtime/egolite-agentteam-20260902-final
```

预期唯一成功条件是 `verified=true` 且 `errors=[]`。修改任意被记录文件、删除任一角色 receipt、
改变 truth boundary、减少 165 cells 或把远端 memory 冒充为已配置，都会使 verifier 失败。
