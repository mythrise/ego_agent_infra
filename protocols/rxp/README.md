# RXP reference package

This package implements RXP/1's strict documents, canonical serialization/hash,
complete matrix commitment, scoped one-time grants, evidence/Merkle gate, append-only
ledger, deterministic fixture, and conformance CLI.

```bash
python -m protocols.rxp demo -o /tmp/rxp.json
python -m protocols.rxp verify /tmp/rxp.json --demo-key
python -m protocols.rxp schema --check
pytest tests/protocols
```

Read the normative scope and threat boundary in
[`docs/protocols/RXP.md`](../../docs/protocols/RXP.md). The public demo key is fixture
material, not a credential. Production adapters must supply trusted keys, authenticated
transport, durable artifact storage, and a serializable replay registry.
