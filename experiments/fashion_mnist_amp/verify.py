"""Offline self-consistency verifier for a claimed Fashion-MNIST GPU artifact.

This command recomputes the decision from supplied bytes. It cannot authenticate that
those bytes originated from CUDA hardware, AgentTeams, or any external service.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .contract import ContractError, canonical_bytes, evaluate_raw_result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_metrics", type=Path)
    parser.add_argument("--expected-config-sha256")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        payload = json.loads(arguments.raw_metrics.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ContractError("raw metrics root must be an object")
        result = evaluate_raw_result(
            payload, expected_config_sha256=arguments.expected_config_sha256
        )
    except (OSError, json.JSONDecodeError, ContractError) as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "live_gpu_evidence_rejected",
                        "message": str(error),
                    },
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    output = canonical_bytes(
        {
            "ok": True,
            "verification_status": "CONTRACT_PASS_ORIGIN_UNVERIFIED",
            "external_origin_status": "UNVERIFIED",
            "live_claim_allowed": False,
            "result": result,
        }
    ) + b"\n"
    if arguments.output is None:
        sys.stdout.buffer.write(output)
    else:
        target = arguments.output.resolve()
        if target.exists():
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": "output_exists",
                            "message": "refusing to overwrite verifier output",
                        },
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 2
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
