"""HTTP-facing RXP reference runtime.

The API exposes a deterministic synthetic fixture and a structural verifier.
It never upgrades the public demo key into production trust and never claims a
physical experiment occurred.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List

from protocols.rxp import (
    GrantSigner,
    MatrixLedgerDocument,
    build_demo_ledger,
    canonical_bytes,
    verify_grant_signatures,
    verify_ledger_document,
)
from protocols.rxp.demo import DEMO_HMAC_KEY, DEMO_KEY_ID
from protocols.rxp.schema import DEFAULT_SCHEMA_DIR


def _schema_index() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for path in sorted(DEFAULT_SCHEMA_DIR.glob("*.schema.json")):
        payload = path.read_bytes()
        items.append(
            {
                "name": path.name,
                "sha256": "sha256:%s" % hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "uri": "https://egoagentos.dev/rxp/1/schemas/%s" % path.name,
            }
        )
    return items


def schema_catalog() -> Dict[str, Any]:
    schemas = _schema_index()
    return {
        "protocol": "RXP/1.0",
        "canonicalization": "RXP-CJ/1 (UTF-8, NFC strings, sorted keys, no floats)",
        "schemas": schemas,
        "schema_count": len(schemas),
        "truth": "Committed JSON Schemas; no network registry lookup was performed.",
    }


def demo_ledger() -> Dict[str, Any]:
    ledger = build_demo_ledger().snapshot()
    verify_ledger_document(ledger)
    verify_grant_signatures(ledger, GrantSigner(DEMO_HMAC_KEY, key_id=DEMO_KEY_ID))
    return {
        "protocol": "RXP/1.0",
        "execution_class": "synthetic deterministic fixture",
        "physical_gpu_run": False,
        "production_signature_trust": False,
        "fixture_signature_verified": True,
        "fixture_key_notice": (
            "The checked-in public HMAC key proves protocol wiring only; it is not a secret "
            "and confers no production authority."
        ),
        "structural_verification": "PASS",
        "canonical_sha256": "sha256:%s"
        % hashlib.sha256(canonical_bytes(ledger)).hexdigest(),
        "ledger": ledger.model_dump(mode="json"),
    }


def verify_uploaded_ledger(value: Dict[str, Any]) -> Dict[str, Any]:
    verify_ledger_document(value)
    ledger = MatrixLedgerDocument.model_validate_json(canonical_bytes(value))
    return {
        "verified": True,
        "verification_scope": [
            "schema",
            "canonical document digests",
            "append-only root chain",
            "causal parents",
            "state transitions",
            "evidence gate",
            "matrix completeness accounting",
        ],
        "signature_trust_verified": False,
        "signature_notice": (
            "Structural verification cannot establish issuer trust without an operator key resolver."
        ),
        "matrix_id": ledger.matrix_id,
        "root": ledger.root,
        "completeness": ledger.completeness,
        "expected_cell_count": ledger.expected_cell_count,
        "decided_cell_count": ledger.decided_cell_count,
        "missing_decisions": list(ledger.missing_decisions),
    }
