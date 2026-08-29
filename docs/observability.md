# Observability and replay

The production control plane stores generation-scoped, append-only PostgreSQL audit events,
uses `LISTEN/NOTIFY` only as a low-latency wake-up, and replays from a durable cursor. SQLite
preserves the same local contract as a developer fallback. The AgentTeams bridge can likewise
persist JSONB checkpoints plus append-only event and receipt chains in PostgreSQL; its verifier
recomputes every event hash and the declared chain head.

The EgoLite happy path still records an explicitly synthetic `trace` evidence artifact. This
repository does **not** emit OpenTelemetry spans or claim a live AgentTeams/Skill/MCP/GPU trace.

The target platform profile uses the following span vocabulary when an OTel exporter and
real Agent/MCP adapters are added:

```text
research.task
├── agent.route
├── skill.invoke
├── mcp.tool.call
├── experiment.submit
├── experiment.monitor
├── evaluation.compute
├── evidence.verify
├── decision.commit
└── memory.write
```

Target attributes use an `ego.*` namespace until a stable external semantic convention
fully covers the domain:

- `ego.task.id`, `ego.agent.id`, `ego.stage`
- `ego.skill.name`, `ego.skill.version`
- `ego.tool.name`, `ego.tool.call.id`, `ego.policy.result`
- `ego.plan.digest`, `ego.run.id`, `ego.run.manifest.sha256`
- `ego.risk.level`, `ego.approval.id`
- `ego.evidence.kind`, `ego.evidence.sha256`

An exporter must preserve standard trace/span IDs, service name, duration, error status,
and GenAI model/token fields where applicable, and redact trace/log/metric payloads. This
is a deployment contract, not a claim about the PostgreSQL/SQLite audit streams or the
AgentTeams bridge ledger.

## Replay contract

A judge selects a local decision and walks backward through gate result, review, raw
metric, run manifest event, configuration, dataset manifest, and frozen goal. Replay
checks artifact digests, generation isolation, and audit-chain order. The deterministic
EgoLite fixture can be reset from INTAKE without external services. Tool calls and Skill
invocations enter this chain only in an execution profile that actually invokes them.

The separate semifinal acceptance verifier replays content-addressed Matrix messages,
receipts, raw metrics, recovery checkpoints, Evidence Gate, primary trace, RXP Decision, and
top-level Decision. Its v1 success state is still `CONTRACT_PASS_ORIGIN_UNVERIFIED`; byte
integrity does not authenticate an external Controller, Matrix room, scheduler, or GPU.

## Infra metrics

- task and tool completion rate;
- evidence and trace completeness;
- unsafe action block and approval-bypass rate (target: 0 bypasses);
- experiment reproducibility and failure recovery rate;
- GPU-hour efficiency and Agent token/cost accounting when real adapters are enabled.
