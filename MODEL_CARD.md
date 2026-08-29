# Model card

EgoAgentOS is infrastructure and ships no trained model or checkpoint. The names of
candidate backbones in the synthetic EgoLite demo are planning labels only; that path
does not execute or evaluate those models.

`experiments/fashion_mnist_amp/` can train a small, task-specific CNN during an explicitly
approved single-GPU acceptance run. Its purpose is to exercise FP32-versus-AMP evidence,
not to publish a reusable model. If run, the tensor state, architecture/config digest,
environment lock, raw predictions and Decision must be retained together. No such model
artifact or live accuracy claim is committed at this snapshot.

An adapter may use commercial or open models, but it must record provider/model version,
request boundary, data policy, cost, fallback, license/terms, and migration plan. LLM
outputs cannot authorize actions, calculate raw metrics, or satisfy evidence gates.
