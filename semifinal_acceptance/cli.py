"""CLI for building and independently verifying semifinal acceptance bundles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .bundle import AcceptanceError, build_bundle, verify_bundle


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="build a bundle from a local capture directory")
    build.add_argument("--source", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify", help="verify a bundle without external services")
    verify.add_argument("--bundle", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "build":
            report = build_bundle(args.source, args.output)
        else:
            report = verify_bundle(args.bundle)
    except AcceptanceError as error:
        sys.stderr.write("semifinal acceptance: %s\n" % str(error))
        return 1
    sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
    return 0

