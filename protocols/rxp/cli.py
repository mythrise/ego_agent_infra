"""Command-line conformance surface for RXP/1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from .canonical import canonical_json, digest_document
from .demo import DEMO_HMAC_KEY, DEMO_KEY_ID, demo_bytes
from .errors import RXPError
from .grants import GrantSigner
from .ledger import verify_grant_signatures, verify_ledger_document
from .models import MatrixLedgerDocument
from .schema import DEFAULT_SCHEMA_DIR, check_schemas, write_schemas


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rxp", description="RXP/1 reproducible experiment protocol reference CLI"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    demo = subcommands.add_parser("demo", help="emit a complete synthetic ledger")
    demo.add_argument("--output", "-o", type=Path, default=None)

    verify = subcommands.add_parser("verify", help="verify a MatrixLedger")
    verify.add_argument("ledger", type=Path)
    verify.add_argument("--hmac-key-file", type=Path, default=None)
    verify.add_argument("--key-id", default=None)
    verify.add_argument(
        "--demo-key",
        action="store_true",
        help="verify with the public synthetic fixture key",
    )

    hash_command = subcommands.add_parser("hash", help="canonicalize and digest JSON")
    hash_command.add_argument("document", type=Path)

    schema = subcommands.add_parser("schema", help="write or check committed schemas")
    schema.add_argument("--output-dir", type=Path, default=DEFAULT_SCHEMA_DIR)
    schema.add_argument("--check", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "demo":
            payload = demo_bytes()
            if args.output is None:
                sys.stdout.buffer.write(payload)
            else:
                args.output.write_bytes(payload)
            return 0
        if args.command == "verify":
            payload = args.ledger.read_bytes()
            ledger = MatrixLedgerDocument.model_validate_json(payload)
            verify_ledger_document(ledger)
            if args.demo_key:
                signer = GrantSigner(DEMO_HMAC_KEY, key_id=DEMO_KEY_ID)
                verify_grant_signatures(ledger, signer)
            elif args.hmac_key_file is not None:
                if args.key_id is None:
                    raise RXPError("key_id_required", "--key-id is required with an HMAC key")
                if args.hmac_key_file.is_symlink():
                    raise RXPError("hmac_key_invalid", "HMAC key file must not be a symlink")
                key = args.hmac_key_file.read_bytes()
                if len(key) > 4096:
                    raise RXPError("hmac_key_invalid", "HMAC key file is too large")
                verify_grant_signatures(ledger, GrantSigner(key, key_id=args.key_id))
            report = {
                "complete": ledger.completeness == "COMPLETE",
                "decided_cells": ledger.decided_cell_count,
                "entry_count": ledger.entry_count,
                "matrix_id": ledger.matrix_id,
                "ok": True,
                "root": ledger.root,
                "signatures_verified": bool(args.demo_key or args.hmac_key_file),
            }
            sys.stdout.write(canonical_json(report) + "\n")
            return 0
        if args.command == "hash":
            parsed = json.loads(args.document.read_text(encoding="utf-8"))
            sys.stdout.write(digest_document(parsed) + "\n")
            return 0
        if args.command == "schema":
            if args.check:
                stale = check_schemas(args.output_dir)
                if stale:
                    raise RXPError(
                        "schemas_stale", "Committed schemas are stale", {"files": stale}
                    )
            else:
                write_schemas(args.output_dir)
            return 0
    except (OSError, json.JSONDecodeError, ValidationError, RXPError, TypeError, ValueError) as exc:
        if isinstance(exc, RXPError):
            error = exc
        else:
            error = RXPError("input_invalid", "RXP command input is invalid")
        sys.stderr.write(str(error) + "\n")
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
