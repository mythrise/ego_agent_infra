# Real GPU acceptance workload

This directory is a narrow, cost-capped live workload for the semifinal acceptance
run. It trains one small CNN on the public Fashion-MNIST training split, then compares
FP32 and CUDA AMP inference on the same frozen test samples. The research result may be
`KEEP` or `REJECT`; successful execution is never confused with candidate success.

The runner is intentionally strict:

- `synthetic=false`, the allowlisted upstream and MIT license are frozen in config;
- the run is deliberately R2 even though it is cheap: exact human approval and an
  independent Reviewer remain mandatory;
- `CUDA_VISIBLE_DEVICES` and Torch must expose exactly one GPU;
- there is no CPU fallback and no arbitrary command field;
- wall time is capped at 900 seconds / 0.25 GPU-hours;
- dataset bytes, code identity, approval receipt, AgentTeams receipt and Matrix plan are
  content-bound;
- every prediction and every latency repetition is retained as raw evidence, and the
  trained tensor state is written in a deterministic binary format whose digest is
  recomputable from the artifact bytes;
- fixed-argv `/usr/bin/nvidia-smi` stage-boundary sampling freezes the physical job's GPU UUID,
  utilization, memory and power as `gpu-telemetry.jsonl`; a missing sampler is fatal;
- baseline/candidate predictions are also projected into a complete sample × matrix-cell
  `accuracy-matrix.jsonl`, recomputable summary, and frozen metric contract understood by
  the semifinal acceptance-bundle verifier;
- the CPU-only verifier recomputes the final Decision and rejects duplicates, NaN,
  digest drift, missing receipts, multi-GPU visibility, or budget overrun.

The committed config defaults to `download=false`, so running it cannot unexpectedly
download data. To authorize the one-time public dataset download, copy the config,
change `dataset.download` to `true`, bind that exact file in the human approval, and
also pass `--allow-download`.

Example live invocation (only after AgentTeams and human approval receipts exist):

```bash
CUDA_VISIBLE_DEVICES=0 python -m experiments.fashion_mnist_amp.run \
  --config /absolute/approved-config.json \
  --data-root /absolute/fashion-mnist \
  --output-dir /new/empty/evidence/runtime \
  --git-commit "$(git rev-parse HEAD)" \
  --run-id "$RUN_ID" \
  --physical-launch-id "$PHYSICAL_LAUNCH_ID" \
  --environment-lock-file /absolute/environment.lock \
  --approval-receipt-file /absolute/approval-receipt.json \
  --agentteams-receipt-file /absolute/agentteams-receipt.json \
  --matrix-plan-file /absolute/matrix-plan.json
```

No live artifact is committed yet. The presence of this adapter proves only that the
repository is ready to execute the bounded workload once an official worker and CUDA
runner are available.

Anyone can recompute the contract gate and Decision without CUDA or Torch. This
proves deterministic self-consistency of the supplied bytes only: the verifier
always reports `CONTRACT_PASS_ORIGIN_UNVERIFIED` and
`live_claim_allowed=false`. A successful exit does not authenticate CUDA,
AgentTeams, or an external scheduler as the byte source:

```bash
python -m experiments.fashion_mnist_amp.verify \
  /absolute/evidence/runtime/raw-metrics.json \
  --expected-config-sha256 "$CONFIG_SHA256"
```
