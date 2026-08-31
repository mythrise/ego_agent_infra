# Secure Agent v2 public contracts

The models in `benchmarks.secure_memory.models`, their generated JSON schemas
in `benchmarks/secure_memory/schemas`, and the semantic entry point
`benchmarks.secure_memory.manifest.validate_wire_document` form one canonical
wire contract for the secure AgentTeams memory campaign. Runtime components
must not maintain shadow request, ticket, lease, checkpoint, memory, or event
types.

JSON Schema validation alone is not sufficient. Every generated schema carries
`x-canonical-semantic-validator` and `x-semantic-validation-required`. A
consumer must first apply the schema's structural constraints and then call the
named semantic validator on the original bytes before trusting, storing, or
hashing the document. The semantic pass rejects duplicate JSON keys,
non-finite/unsupported JSON values, noncanonical UTF-8 base64, path traversal,
ordering violations, cross-field ceilings, canonical digest mismatches, and
model invariants. Signed task leases additionally require the frozen manifest
and authoritative owner-specific context; missing context fails closed.

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

The repository's root wheel is the public Worker distribution. Its setuptools
hook removes Python modules and non-schema data whose path identifies an
Evaluator, sealed, or hidden component, including stale files in the wheel
staging directory. Package discovery also excludes matching future private
subpackages. The current generated schemas contain no private path component;
a future public schema whose filename matches the private-path policy requires
an explicit reviewed packaging-policy change rather than entering by glob.
