# 最终真实模型验收：ResearchOS + compact memory

时间：2026-09-02 17:32 CST

Trace：`trace_a85ed1b8233b4cb48970c5e2aff6cc6b`

模型网关：`https://apihub.agnes-ai.com/v1`，model=`agnes-2.5-flash`

源码快照：`dcd621024af83edb5252095ac5c3960c27f4f1bd`

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
| research-pi | 200 | 5164 ms | `5bf6759242ec376c88a698be85d6a4fdeb773409476e3b4baf057bc8e097e09b` | `f43e4d9d2ca1d477fc91d9f7163e98eaeebff7c7d219f07f34afc829cbb7d9cc` |
| scout | 200 | 7490 ms | `f305466aa842abfbe7ea5de63a5333f8c56cb6c9fd872844cd86420a81a69e93` | `f09361bfdd69b2dfbee50d3a36c6e004a1c608bc274b6ded9be6331aa2a5d3f3` |
| experiment-architect | 200 | 9822 ms | `bdba37c2a0954f1cc5d6432a5c4c870e2c2e7db8a889358fdc59b6fff3b0a2a0` | `532062f586d25489be8280774f5b98587b6278aff3a14a61385477610e223af7` |
| reviewer | 200 | 6139 ms | `a79c3ef22be31b7856127ef89a8f2f8e16e8941914e0b24e97d36e40b90acb39` | `f514927a633a9a33954ac967e2753ffa3373e9cc75c486d4df351833932a0a83` |

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
| research-pi | INTAKE | 1673 | 699 | `a850eca59c323da275152156c0b552899cba6d299921d2865c82752d9e8615fc` |
| scout | CONTEXT | 753 | 682 | `abb4548b96fad30b3dec446e9a8cdd4399d6afe7b0bce15932f276d8924e8438` |
| experiment-architect | PLAN | 964 | 724 | `1b1874cc7586c74bbd61dce358d0115bf7963e752e52d909055cbe3c9184cfef` |
| reviewer | VERIFY | 857 | 690 | `0dea65ac32b44237f352c070a0f358646e6b267b76fab4cfd7319abc25e950cc` |

`FOCUS.md` 不是简单截断聊天，而是只保留 validated facts、decisions、evidence、blockers 和
next actions；SQLite 保存原始 L0 与链式 receipt，下一阶段从新的 focus 投影继续。

## 复核命令

```bash
uv run --python 3.9 python -m experiments.egolite_agentteam.verify \
  artifacts/runtime/egolite-agentteam-20260902-dcd6210
```

预期唯一成功条件是 `verified=true` 且 `errors=[]`。修改任意被记录文件、删除任一角色 receipt、
改变 truth boundary、减少 165 cells 或把远端 memory 冒充为已配置，都会使 verifier 失败。

最终回归还通过：Python 默认套件 661 项（660 passed，1 个已有条件性 skip）、MCP 51 passed、
Web 16 passed、TypeScript/Vite production build、全仓 Ruff、B 支线 compileall 与
`docker compose config --quiet`。这些是本地代码/配置证据，不改变云端和 GPU 的 `NOT_RUN`。
