# RXP/1 与 MCP、A2A、PROV-O、MLflow 的边界比较

## 结论先行

RXP/1 是 EgoAgentOS 项目定义并实现的**实验承诺、验收与完整性协议**。它在执行前冻结完整
实验矩阵，在执行时绑定 scope-bound one-time Grant 与 Receipt，在执行后以 content digest、
独立 Evidence 和 Decision 证明“计划了什么、授权了什么、产出了哪些 bytes、依据什么验收、
哪些 cell 仍缺失”。

RXP/1 **不是** MCP 的工具/上下文协议、A2A 的 Agent 协作协议、PROV-O 的通用 provenance
ontology，也不是 MLflow Tracking 的运行记录平台。它可以被这些系统承载、描述或存储，
但不替代它们。当前 `RXP/1` 是仓库内 reference specification/reference implementation，
没有标准组织背书、行业标准地位或外部采用证明。

## 官方定义与比较依据

| 系统 | 官方定位 | 本文使用的官方来源 |
|---|---|---|
| MCP | LLM 应用与外部 data/tools 连接的 client-server protocol；以 JSON-RPC、capability negotiation、resources、prompts、tools 为核心 | [MCP Specification 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25) |
| A2A | 让独立、可能 opaque 的 Agent 通过 Agent Card、Message、stateful Task、Part、Artifact、streaming/push 互操作 | [A2A specification](https://a2a-protocol.org/latest/specification/) |
| PROV-O | W3C PROV data model 的 OWL2 ontology，用 Entity、Activity、Agent 及关系表达和交换 provenance | [W3C PROV-O Recommendation](https://www.w3.org/TR/prov-o/) |
| MLflow Tracking | 记录和查询 runs、parameters、metrics、code version 与 artifacts 的 API/UI 和存储模型 | [MLflow Tracking documentation](https://mlflow.org/docs/latest/ml/tracking/) |
| RXP/1 | 本项目的矩阵级实验 commitment、per-cell authorization、evidence acceptance 与 omission proof | [RXP/1 reference specification](RXP.md) |

版本说明：以上链接用于确认相邻系统的职责，不表示 RXP 与任何版本已取得官方兼容认证。
集成前仍应 pin 具体版本并运行各自 conformance/contract test。

## 能力边界

| 维度 | MCP | A2A | PROV-O | MLflow Tracking | RXP/1 |
|---|---|---|---|---|---|
| 首要问题 | 模型如何发现/调用工具并读取上下文 | Agent 如何发现彼此并交换消息、任务与产物 | provenance 如何被语义化描述和交换 | 一次 run 的参数、指标和产物如何记录/查询 | 一个完整实验矩阵如何在执行前承诺，并在执行后被严格验收 |
| 核心对象 | host/client/server、tool、resource、prompt | Agent Card、Message、Task、Part、Artifact | Entity、Activity、Agent、provenance relation | Experiment、Run、Param、Metric、Artifact | MatrixPlan、Intent、Grant、Receipt、Evidence、Decision、MatrixLedger |
| 传输/交互 | 定义 JSON-RPC lifecycle 与 transports | 定义 Agent 交互、task lifecycle 与 streaming/push | RDF/OWL vocabulary；不负责执行 transport | API、SDK、server/UI 和 artifact store 集成 | transport-independent canonical documents；不定义 Agent 消息或 tool transport |
| 工具发现与调用 | 是核心职责 | 可由 Agent 能力/任务间接涉及 | 否 | 否 | 否；RXP 文档可作为 tool I/O |
| 多 Agent 委派 | 否 | 是核心职责 | 可描述参与者，但不编排 | 否 | 否；由 A2A/AgentTeams 等负责 |
| 执行前完整矩阵冻结 | 不是协议职责 | 不是协议职责 | 可描述计划实体，不规定 Cartesian completeness | 可记录计划/父 run，但 Tracking 文档不规定此验收合同 | 是；axes/cells 必须等于完整 Cartesian product |
| Scope-bound one-time authorization | 不是协议内建实验语义 | 不是协议内建实验语义 | 不执行授权 | 可记录 tag/artifact；Tracking 模型不定义一次性实验 Grant | 是；Grant 精确绑定 Intent/cell/action/scope/bounds/expiry，Receipt 原子消费 |
| 内容寻址与因果父链 | 可携带 URI/metadata | 可携带 Artifact/metadata | 擅长表达一般 provenance 关系 | 记录 run/artifact 元数据，具体 digest policy 由部署决定 | canonical domain-separated digest；Receipt/Evidence/Decision 强制精确父链 |
| 独立验收 | 不定义科研证据 gate | 可委派 Reviewer，但不规定独立性规则 | 可表达 Agent/Activity 关系，不执行 gate | 可记录评价指标或 model evaluation；不等于 RXP gate | Reviewer 不得产生 non-review evidence；Evidence set/root 与 verdict 被重算 |
| 缺失实验证明 | 不定义 | 不定义 | 可查询缺失陈述，但没有 RXP matrix invariant | UI 可显示缺 run；Tracking 模型不证明原计划无 silent omission | MatrixLedger 列出 expected/decided/missing cells 并提交 append-only root |
| “结果正确”保证 | 不保证 | 不保证 | 不保证 | 不保证 | 不保证；只让 claim boundary 可验证 |

表中的“不是协议职责”不是说某个实现无法通过扩展做到，而是说其官方核心抽象没有定义
RXP 的这组实验验收不变量。反过来，RXP 也没有这些系统的工具生态、Agent 通信、语义推理
或 tracking UI。

## RXP 的窄创新主张

RXP 只主张把以下四件事合并为一个可执行、可审计的实验合同：

1. **Experiment commitment**：执行前冻结完整 Cartesian matrix、每个 cell、seed、代码、
   配置、数据 manifest、环境与 determinism floor，并对 canonical bytes 求 digest；
2. **Scoped execution acceptance**：每个 cell 需要精确、限时、资源有界的一次性 Grant，
   Receipt 被接受时原子消费，防止 replay 和 scope substitution；
3. **Independent evidence acceptance**：Evidence 内容寻址、父链固定、完整集合以 Merkle root
   提交，Reviewer 身份独立，Decision 由 verifier 重算而不是 Agent 自报；
4. **Matrix integrity**：append-only MatrixLedger 同时给出 expected、decided 与 missing cells，
   防止 cherry-pick 或静默遗漏。

这组机制提高的是实验过程和声明边界的确定性，不是模型输出、GPU kernel、统计结论或科学
真理的确定性。矩阵是否设计合理、指标是否有意义、数据是否代表真实分布，仍由研究方法与
人类评审负责。

## 组合方式

```text
AgentTeams / A2A             MCP tools                 MLflow / artifact store
协作、委派、任务状态   →   实际执行与数据访问    →    运行记录、指标、artifact bytes
         \                      |                           /
          \---- RXP canonical documents + digests --------/
                         |
                  optional PROV-O projection
```

推荐映射如下：

| 组合 | 安全映射 | 必须保留的边界 |
|---|---|---|
| MCP + RXP | tool input 接收 `intent_digest`/Grant reference；tool output 返回 Receipt/Evidence URI 和 digest | MCP authorization/capability negotiation 不能自动视为 RXP Grant；RXP 不发现或调用 tool |
| A2A/AgentTeams + RXP | Task/Message/DataPart/Artifact metadata 携带 RXP document URI、digest、cell 与 correlation ID | A2A/AgentTeams task completion 不自动成为 RXP Decision；RXP 不替代委派/聊天/room identity |
| PROV-O + RXP | 将 RXP documents 投影为 `prov:Entity`，physical run 为 `prov:Activity`，actor 为 `prov:Agent`，父链投影为 provenance relation | PROV graph 是查询/交换视图；RXP canonical bytes、signature、state guard 和 gate 仍是本项目 verifier 的规范输入 |
| MLflow + RXP | MLflow run/tag/artifact 保存 RXP IDs、digests、documents 与 evidence URI | “已记录 run”不等于 MatrixPlan 完整、Grant 单次消费或 Decision gate 通过；MLflow 继续负责 tracking 体验 |

## 明确非目标

RXP/1 不定义：

- Agent discovery、conversation、delegation、streaming 或 room membership；
- LLM/tool protocol、scheduler、container sandbox、GPU allocator 或 cloud control plane；
- artifact store 的可用性、分布式共识、可信时间戳或身份基础设施；
- 通用 provenance ontology 或 experiment tracking UI；
- metric 的科学有效性、数据授权、统计功效或模型结果的必然复现。

本地 HMAC Grant 与 SQLite replay registry 只适用于 reference/local profile。分布式部署需要
独立的 key resolution、可信身份和 serializable/linearizable replay store；这不是协议文档能
替代的部署证明。

## 对外表述护栏

可以使用：

> EgoAgentOS 提出并实现 RXP/1：一个项目定义的实验承诺、验收与完整性协议。它可通过
> MCP/A2A/AgentTeams 传递、投影到 PROV-O、存入 MLflow，但不替代这些系统。

不能使用：

- “RXP 是国际、国家、行业或事实标准”；
- “RXP 已获得 MCP、A2A、W3C、MLflow 或 AgentTeams 官方兼容认证”；
- “RXP 替代 MCP/A2A/PROV-O/MLflow”；
- “RXP 保证 AI 实验结果确定、科学结论正确或任意训练可字节复现”；
- “RXP 已被外部项目采用”，除非附可验证的第三方证据。

规范、不变量与 conformance boundary 见 [`RXP.md`](RXP.md)；实现位于
[`../../protocols/rxp/`](../../protocols/rxp/)。
