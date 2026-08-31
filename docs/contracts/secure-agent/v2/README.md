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
hooks share one exact, file-level public allowlist with the wheel/sdist archive
validator. A real wheel build starts from an empty staging tree; unexpected
stale staged files make the build fail before the tree is cleared. Python
modules and package data are selected explicitly rather than by broad public
globs, and new Evaluator-, sealed-, or hidden-looking source files fail closed.
A future public file requires an explicit reviewed allowlist and package-data
change rather than entering an artifact through discovery or stale staging.
