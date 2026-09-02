"""Compile a research request into a deterministic, portable acceptance directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import yaml

from .models import CompileResearchRequest
from .service import ResearchOSService


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def compile_file(source: Path, output: Path) -> Dict[str, Any]:
    body = CompileResearchRequest.model_validate(
        yaml.safe_load(source.read_text(encoding="utf-8"))
    )
    result = ResearchOSService(memory_root=output / "agent-memory").compile(body)
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "compile.json", result)
    _write_json(output / "experiment-tree.json", result["tree"])
    _write_json(output / "experiment-matrix.json", result["matrix"])
    _write_json(output / "resource-review.json", result["resource_review"])
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    result = compile_file(args.input, args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "compile_sha256": result["compile_sha256"],
                "cells": result["matrix"]["cell_count"],
                "resource_decision": result["resource_review"]["decision"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
