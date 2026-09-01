# EgoLite live model-team acceptance

This bounded harness connects four EgoAgentOS roles to an OpenAI-compatible model
gateway, then runs the existing approval-gated EgoLite control-plane replay.

Truth labels are intentionally separate:

- model responses: `LIVE`;
- local ResearchOps state machine, approval token, gate, and event chain: `LIVE_LOCAL`;
- EgoLite metrics/workload: `SYNTHETIC_FIXTURE`;
- official AgentTeams Controller, Matrix transport, and physical GPU: `NOT_RUN`.

It therefore tests a real model backend suitable for AgentTeams Workers without
manufacturing official AgentTeams receipts. Use `integrations/agentteams/README.md`
for the additional Controller/Matrix requirements of a fully live collaboration run.

```bash
export EGO_AGENT_MODEL_BASE_URL='https://provider.example/v1'
read -r -s EGO_AGENT_MODEL_API_KEY && export EGO_AGENT_MODEL_API_KEY
export EGO_AGENT_MODEL='provider-model-id'
python -m experiments.egolite_agentteam.run \
  --output "artifacts/runtime/egolite-agentteam-$(date +%Y%m%dT%H%M%S)"
unset EGO_AGENT_MODEL_API_KEY
```

The ignored output directory contains copied frozen inputs, per-role JSON outputs,
redacted request/response receipts, the complete local control-plane snapshot, an
acceptance manifest, and checksums. The API key is neither serialized nor logged.

Verify the frozen directory offline:

```bash
python -m experiments.egolite_agentteam.verify artifacts/runtime/egolite-agentteam-<timestamp>
```
