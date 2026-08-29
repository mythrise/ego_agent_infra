"""Subprocess boundary for untrusted benchmark adapters."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Sequence

from benchmarks import BENCHMARK_VERSION
from benchmarks.model import canonical_json


def _load(reference: str) -> ModuleType:
    candidate = Path(reference)
    if candidate.suffix == ".py" or candidate.is_absolute():
        if not candidate.is_file():
            raise FileNotFoundError("adapter file does not exist")
        spec = importlib.util.spec_from_file_location(
            "egoagentos_external_benchmark_adapter", candidate
        )
        if spec is None or spec.loader is None:
            raise ImportError("adapter file cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return importlib.import_module(reference)


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--response", required=True, type=Path)
    args = parser.parse_args(list(argv) if argv else None)
    request: Dict[str, Any] = json.loads(args.request.read_text(encoding="utf-8"))
    module = _load(args.adapter)
    if getattr(module, "BENCHMARK_ADAPTER_VERSION", None) != BENCHMARK_VERSION:
        raise ValueError("adapter BENCHMARK_ADAPTER_VERSION does not match the corpus")
    run_scenario = getattr(module, "run_scenario", None)
    if not callable(run_scenario):
        raise TypeError("adapter does not export callable run_scenario")
    raw = run_scenario(
        request["scenario"],
        int(request["seed"]),
        Path(request["workspace"]),
    )
    if not isinstance(raw, dict):
        raise TypeError("run_scenario must return a dict")
    temporary = args.response.with_suffix(".tmp")
    temporary.write_text(canonical_json(raw) + "\n", encoding="utf-8")
    temporary.replace(args.response)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("%s: %s" % (type(error).__name__, str(error)), file=sys.stderr)
        raise SystemExit(1)
