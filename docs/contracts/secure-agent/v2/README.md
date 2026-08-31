# Secure Agent v2 public contracts

The JSON schemas in `benchmarks/secure_memory/schemas` are generated from
`benchmarks.secure_memory.models` and are the sole wire source of truth for the
secure AgentTeams memory campaign. Runtime components import those canonical
models; they must not maintain shadow request, ticket, lease, checkpoint,
memory, or event types.

`contract-digests.json` records the SHA-256 digest of every public schema byte
file. Regenerate the schemas and index with:

```bash
python -m benchmarks.secure_memory.manifest schema
```

Verify that no schema is missing, changed, orphaned, or unindexed with:

```bash
python -m benchmarks.secure_memory.manifest schema --check
```

The package contains public contracts only. Sealed requirements, hidden tests,
Evaluator implementation, signing material, and provider credentials are not
package data. The initial RunManifest contains exactly configurations A-E. A
concrete F and all F/WINNER_SEALED/F_SEALED leases require a later verified,
signed post-selection extension digest.
