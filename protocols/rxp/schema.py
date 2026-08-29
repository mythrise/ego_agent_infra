"""Generate and check the committed RXP JSON Schema contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Type

from .models import (
    Decision,
    Evidence,
    Grant,
    Intent,
    MatrixLedgerDocument,
    MatrixPlan,
    Receipt,
    StrictModel,
)

SCHEMA_MODELS: Dict[str, Type[StrictModel]] = {
    "rxp-decision-v1.schema.json": Decision,
    "rxp-evidence-v1.schema.json": Evidence,
    "rxp-grant-v1.schema.json": Grant,
    "rxp-intent-v1.schema.json": Intent,
    "rxp-matrix-ledger-v1.schema.json": MatrixLedgerDocument,
    "rxp-matrix-plan-v1.schema.json": MatrixPlan,
    "rxp-receipt-v1.schema.json": Receipt,
}

DEFAULT_SCHEMA_DIR = Path(__file__).with_name("schemas")


def schema_bytes(filename: str, model: Type[StrictModel]) -> bytes:
    schema = model.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://egoagentos.dev/rxp/1/schemas/{filename}"
    return (
        json.dumps(schema, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
        + "\n"
    ).encode("utf-8")


def write_schemas(directory: Path = DEFAULT_SCHEMA_DIR) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for filename, model in SCHEMA_MODELS.items():
        (directory / filename).write_bytes(schema_bytes(filename, model))


def check_schemas(directory: Path = DEFAULT_SCHEMA_DIR) -> list[str]:
    stale = []
    for filename, model in SCHEMA_MODELS.items():
        path = directory / filename
        if not path.is_file() or path.read_bytes() != schema_bytes(filename, model):
            stale.append(filename)
    return stale
