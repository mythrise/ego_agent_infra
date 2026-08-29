from __future__ import annotations

import json
import subprocess
import sys

import pytest

from protocols.rxp.canonical import canonical_bytes, digest_document, merkle_root
from protocols.rxp.demo import demo_bytes
from protocols.rxp.ledger import verify_grant_signatures, verify_ledger_document
from protocols.rxp.schema import check_schemas


def test_canonical_key_order_unicode_normalization_and_known_hash() -> None:
    left = {"z": [1, True, None], "é": "café"}
    right = {"e\u0301": "cafe\u0301", "z": [1, True, None]}
    assert canonical_bytes(left) == canonical_bytes(right)
    assert canonical_bytes(left) == '{"z":[1,true,null],"é":"café"}'.encode()
    assert digest_document(left) == (
        "sha256:6d39d772002443f15d40173bc6880488bf96d1e3c75a03cd506cd7034e7751d4"
    )


@pytest.mark.parametrize("invalid", [1.5, float("nan"), float("inf"), 2**63])
def test_canonical_rejects_ambiguous_or_unbounded_numbers(invalid: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        canonical_bytes({"value": invalid})


def test_merkle_root_is_order_independent_and_has_known_vector() -> None:
    leaves = ("sha256:" + "0" * 64, "sha256:" + "f" * 64, "sha256:" + "a" * 64)
    assert merkle_root(leaves) == merkle_root(tuple(reversed(leaves)))
    assert merkle_root(leaves) == (
        "sha256:aeac668f594504cfd58582e4104183d5161b7653ffd2c0c3d7fce6dfba16b3f6"
    )
    with pytest.raises(ValueError, match="duplicate"):
        merkle_root((leaves[0], leaves[0]))


def test_demo_is_byte_identical_and_structurally_valid(demo_snapshot, signer) -> None:  # type: ignore[no-untyped-def]
    assert demo_bytes() == demo_bytes()
    verify_ledger_document(demo_snapshot)
    verify_grant_signatures(demo_snapshot, signer)
    assert demo_snapshot.completeness == "COMPLETE"
    assert demo_snapshot.missing_decisions == ()
    assert check_schemas() == []


def test_cli_repeated_output_is_byte_identical_and_verifiable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    for output in (first, second):
        result = subprocess.run(
            [sys.executable, "-m", "protocols.rxp", "demo", "-o", str(output)],
            check=False,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr.decode()
    assert first.read_bytes() == second.read_bytes()

    verified = subprocess.run(
        [
            sys.executable,
            "-m",
            "protocols.rxp",
            "verify",
            str(first),
            "--demo-key",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr
    report = json.loads(verified.stdout)
    assert report["complete"] is True
    assert report["signatures_verified"] is True
