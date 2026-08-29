# Data card

## Bytes committed to this repository

The repository contains only tiny, hand-authored synthetic text/JSON fixtures under
`examples/egolite/`. They exist to test ResearchOps behavior, not model quality.

- No people, images, biometric data, ego video, Fashion-MNIST samples, or other dataset
  payloads are committed.
- Fixture metric and resource values are explicitly marked `synthetic: true`.
- The fixture must not be used to claim hardware throughput or model accuracy.

## Optional live-acceptance dataset

`experiments/fashion_mnist_amp/` contains a bounded adapter for the public Fashion-MNIST
dataset. The committed config defaults to `download=false`; use of the adapter therefore
does not silently fetch data. A one-time run must separately authorize both an edited,
digest-bound config and `--allow-download`, then preserve the downloaded file manifest,
upstream identifier, license statement, split indices and SHA-256 values in the evidence
bundle.

No Fashion-MNIST run artifact is committed at this snapshot. The adapter and verifier are
implementation evidence, not proof that dataset bytes came from the stated upstream.

Real deployments must document dataset origin, consent/license, minimization, retention,
access scope, split construction, manifest digest and deletion procedure.
