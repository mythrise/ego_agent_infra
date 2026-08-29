# Semifinal acceptance bundle

`egoagentos.semifinal-acceptance/v1` packages an already captured live
AgentTeams/RXP/GPU acceptance run into deterministic, content-addressed bytes.
The builder and verifier make no network or GPU calls.

The package deliberately implements only the judge-facing eight-scenario MVP
contract gate. A valid manifest reports `CONTRACT_PASS_ORIGIN_UNVERIFIED`,
`live_claim_allowed=false`, `8/14` coverage, and
`full_release_status=NOT_EVALUATED`. Offline byte checks cannot authenticate an
external service as the producer. Only a future verifier for actual upstream
signatures/attestations may promote source authenticity; this v1 format never
does. Separately, only `rxp-bench --release-gate` over all 14 scenarios and all
configured repetitions may produce a full release claim.

## Build and verify

Prepare a private capture directory using the contract below, excluding every
bearer token, approval token, API key, private key, and `.env` file. Then run:

```bash
ego-semifinal-bundle build \
  --source /absolute/path/to/live-capture \
  --output /absolute/path/to/new-bundle

ego-semifinal-bundle verify \
  --bundle /absolute/path/to/new-bundle
```

The output contains `manifest.json`, `manifest.sha256`, and byte-identical
copies under `artifacts/`. `trace_root` commits the primary normative trace,
`evidence_root` is the RXP Evidence Gate root, and `bundle_root` commits all
declared artifacts and claim metadata.

## Source contract

The source root contains `acceptance-input.json`. Its `files` map must name:

- frozen inputs and budget;
- official AgentTeams response receipts plus their raw response files;
- raw Matrix room events;
- at least two raw samples from exactly one GPU job/GPU UUID;
- complete per-sample/per-cell raw metrics and a recomputable summary;
- a structurally valid, complete, non-synthetic RXP MatrixLedger;
- seven-kind Evidence Gate, failure/recovery proof, independent review, and
  final decision.

`scenario_results` declares all 14 canonical scenarios. The eight MVP scenarios
must be `PASS` with separate schema-verified real-AgentTeams traces; the other
six must remain explicit `SKIP` values with reasons. A generic trace cannot be
reused because each trace is checked against its own scenario and seed.

Every RXP Evidence artifact is read from the source, hashed again, and matched
to its embedded Evidence document and Decision root. Raw metrics must enumerate
the frozen sample-by-cell Cartesian product exactly once. A missing record,
duplicate record, non-finite JSON number, undeclared filter, summary mismatch,
secret, synthetic marker, resource-bound violation, or changed byte fails
closed.

Content hashes prove byte integrity and cross-artifact consistency. They are
not a third-party signature that a remote service emitted the bytes. Even an
entirely local fixture can satisfy structural tests, so this limit is
machine-readable in every manifest and CLI result rather than only prose.
