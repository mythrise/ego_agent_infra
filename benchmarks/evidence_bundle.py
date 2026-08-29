"""Persistent, replayable evidence bundles for target benchmark trials."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from benchmarks.model import Observation, Scenario, canonical_json
from benchmarks.trace_verifier import verify_trace_bytes


BUNDLE_SCHEMA_VERSION = "egoagentos.benchmark-evidence-bundle/v1"


def bundle_relative_path(observation: Observation) -> Path:
    return (
        Path(observation.profile)
        / observation.scenario_id
        / ("repetition-%03d" % observation.repetition)
    )


def persist_evidence_bundle(
    observation: Observation,
    *,
    scenario: Scenario,
    seed: int,
    trial_workspace: Path,
    evidence_dir: Path,
) -> Path:
    """Copy a verified trace and a replay manifest out of the temporary trial."""

    if not observation.trace_root or not observation.evidence_root:
        raise ValueError("only independently verified observations can be persisted")
    trace_value = observation.details.get("verified_trace_path")
    if not isinstance(trace_value, str):
        raise ValueError("verified trace path is absent from the observation")
    source = (trial_workspace / trace_value).resolve()
    if trial_workspace.resolve() not in source.parents or not source.is_file():
        raise ValueError("verified trace source is outside the trial workspace")
    trace_bytes = source.read_bytes()
    verified = verify_trace_bytes(trace_bytes, scenario=scenario, seed=seed)
    if verified.trace_root != observation.trace_root:
        raise ValueError("trace root changed before evidence persistence")
    if verified.evidence_root != observation.evidence_root:
        raise ValueError("evidence root changed before evidence persistence")

    relative = bundle_relative_path(observation)
    destination = evidence_dir / relative
    if destination.exists():
        raise FileExistsError("evidence bundle already exists: %s" % destination)
    destination.mkdir(parents=True, exist_ok=False)
    trace_path = destination / "trace.json"
    trace_path.write_bytes(trace_bytes)
    manifest: Dict[str, Any] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "profile": observation.profile,
        "scenario_id": observation.scenario_id,
        "repetition": observation.repetition,
        "seed": seed,
        "trace_file": trace_path.name,
        "trace_root": verified.trace_root,
        "evidence_root": verified.evidence_root,
        "trace_schema": "egoagentos.agentteams-trace/v1",
        "verified_facts": verified.facts,
    }
    (destination / "manifest.json").write_text(
        canonical_json(manifest) + "\n", encoding="utf-8"
    )
    observation.details["evidence_bundle_relpath"] = relative.as_posix()
    return destination


def replay_evidence_bundle(
    bundle: Path,
    *,
    scenario: Scenario,
    observation: Dict[str, Any],
) -> None:
    """Re-run the independent verifier and bind it to one result observation."""

    manifest_path = bundle / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("manifest.json is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise ValueError("unsupported evidence bundle manifest")
    expected = {
        "profile": observation.get("profile"),
        "scenario_id": observation.get("scenario_id"),
        "repetition": observation.get("repetition"),
        "seed": observation.get("seed"),
        "trace_root": observation.get("trace_root"),
        "evidence_root": observation.get("evidence_root"),
    }
    mismatched = [key for key, value in expected.items() if manifest.get(key) != value]
    if mismatched:
        raise ValueError("manifest/result mismatch: %s" % ", ".join(sorted(mismatched)))
    trace_name = manifest.get("trace_file")
    if not isinstance(trace_name, str) or Path(trace_name).name != trace_name:
        raise ValueError("trace_file must be one local filename")
    trace_path = bundle / trace_name
    if not trace_path.is_file():
        raise ValueError("persisted trace is missing")
    verified = verify_trace_bytes(
        trace_path.read_bytes(), scenario=scenario, seed=int(observation["seed"])
    )
    if verified.trace_root != manifest.get("trace_root"):
        raise ValueError("persisted trace root does not match the manifest")
    if verified.evidence_root != manifest.get("evidence_root"):
        raise ValueError("persisted evidence root does not match the manifest")
    if verified.facts != manifest.get("verified_facts"):
        raise ValueError("persisted verified facts do not replay")
