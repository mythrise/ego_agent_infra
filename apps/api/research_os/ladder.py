"""Deterministic three-level proposal normalization and tree/matrix compilation."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Sequence

from .models import InputTier, ModuleSpec, ResearchInput, TruthClass


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def classify_input(value: ResearchInput) -> InputTier:
    if value.requested_tier is not None:
        return value.requested_tier
    if value.proposal and (value.hierarchy or value.branches or value.core_code):
        return InputTier.DETAILED
    if value.idea or value.proposal:
        return InputTier.FUZZY
    return InputTier.BASELINE_ONLY


def _default_hierarchy(value: ResearchInput) -> List[ModuleSpec]:
    """Produce an explicit V/C/P factorization when no hierarchy was supplied."""

    visual = ["MoViNet temporal encoder", "MobileNet image encoder", "RTMW pose encoder"]
    constraints = ["C%02d falsifiable constraint" % index for index in range(1, 11)]
    priors = ["kinematic prior", "camera geometry prior", "temporal smoothness prior"]

    stages: List[ModuleSpec] = []
    for stage_index, label in ((1, "observation and local representation"), (2, "global inference and dynamics")):
        groups = []
        for name, variants in (("V", visual), ("C", constraints), ("P", priors)):
            groups.append(
                ModuleSpec(
                    name=name,
                    kind="factor",
                    hypothesis="Stage %d %s factor" % (stage_index, name),
                    children=[
                        ModuleSpec(
                            name=variant,
                            kind="variant",
                            hypothesis=(
                                "Changing only %s in stage %d produces a measurable delta "
                                "against the frozen baseline."
                            )
                            % (variant, stage_index),
                            runnable=True,
                        )
                        for variant in variants
                    ],
                )
            )
        stages.append(
            ModuleSpec(
                name="Stage %d: %s" % (stage_index, label),
                kind="stage",
                children=groups,
            )
        )
    return stages


def normalize_proposal(value: ResearchInput) -> Dict[str, Any]:
    tier = classify_input(value)
    if tier == InputTier.DETAILED:
        proposal_text = value.proposal or value.idea or value.objective
        method = "user_specified"
        claim = TruthClass.LIVE_LOCAL.value
    elif tier == InputTier.FUZZY:
        proposal_text = (
            "Convert the idea into a falsifiable ablation program. Freeze the baseline, vary one "
            "factor per cell, preserve fold assignments, declare primary metrics before execution, "
            "and require independent verification before KEEP. Idea: %s" % (value.idea or value.proposal)
        )
        method = "deterministic_falsification_template"
        claim = TruthClass.SYNTHETIC_FIXTURE.value
    else:
        proposal_text = (
            "Reproduce the supplied baseline first. Generate factorized V/C/P alternatives, include "
            "identity and shuffled controls, predeclare stop criteria, and admit improvements only "
            "when held-out evidence passes the gate."
        )
        method = "deterministic_baseline_expansion"
        claim = TruthClass.SYNTHETIC_FIXTURE.value

    hierarchy = value.hierarchy or _default_hierarchy(value)
    if value.branches:
        hierarchy = list(hierarchy) + [
            ModuleSpec(
                name="User improvement branches",
                kind="branch_group",
                children=[
                    ModuleSpec(
                        name=branch,
                        kind="variant",
                        hypothesis="User-specified branch must beat the frozen baseline.",
                        runnable=True,
                    )
                    for branch in value.branches
                ],
            )
        ]

    metrics = value.metrics or [
        "primary_metric",
        "variance_across_folds",
        "runtime_seconds",
        "peak_memory_bytes",
    ]
    return {
        "tier": tier.value,
        "normalization": {"method": method, "truth_class": claim, "model_call": "NOT_RUN"},
        "title": value.title,
        "objective": value.objective,
        "baseline": value.baseline,
        "proposal": proposal_text,
        "core_code": value.core_code,
        "source_repository": value.source_repository,
        "metrics": metrics,
        "hierarchy": [item.model_dump(mode="json") for item in hierarchy],
    }


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:48] or "node"


def build_tree(normalized: Dict[str, Any]) -> Dict[str, Any]:
    sequence = 0

    def visit(item: Dict[str, Any], path: Sequence[str]) -> Dict[str, Any]:
        nonlocal sequence
        sequence += 1
        current_path = tuple(path) + (item["name"],)
        node_id = "n%03d-%s" % (sequence, _slug(item["name"]))
        return {
            "node_id": node_id,
            "name": item["name"],
            "kind": item.get("kind", "module"),
            "hypothesis": item.get("hypothesis", ""),
            "runnable": bool(item.get("runnable", False)),
            "parameters": item.get("parameters", {}),
            "path": list(current_path),
            "children": [visit(child, current_path) for child in item.get("children", [])],
        }

    baseline = {
        "name": "Frozen baseline reproduction",
        "kind": "baseline",
        "hypothesis": "The existing result is reproducible before any improvement is tested.",
        "runnable": True,
        "parameters": {"frozen": True},
        "children": [],
    }
    root = {
        "name": normalized["title"],
        "kind": "research_goal",
        "hypothesis": normalized["objective"],
        "runnable": False,
        "parameters": {},
        "children": [baseline] + normalized["hierarchy"],
    }
    tree = visit(root, ())
    tree["tree_sha256"] = canonical_sha256(tree)
    return tree


def _runnable_nodes(node: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    if node["runnable"]:
        yield node
    for child in node["children"]:
        yield from _runnable_nodes(child)


def build_matrix(
    tree: Dict[str, Any], folds: Sequence[int], seeds: Sequence[int], metrics: Sequence[str]
) -> Dict[str, Any]:
    cells = []
    for node in _runnable_nodes(tree):
        for fold in sorted(folds):
            for seed in sorted(seeds):
                core = {
                    "tree_node_id": node["node_id"],
                    "tree_path": node["path"],
                    "fold": fold,
                    "seed": seed,
                    "parameters": node["parameters"],
                    "metrics": list(metrics),
                }
                digest = canonical_sha256(core)
                cells.append(
                    {
                        "cell_id": "exp_%s" % digest[:16],
                        **core,
                        "intent_token": "rxpi_%s" % digest[:24],
                        "status": "PLANNED",
                    }
                )
    core = {
        "schema_version": "egoagentos-experiment-matrix/v1",
        "tree_sha256": tree["tree_sha256"],
        "cells": cells,
    }
    return {**core, "matrix_sha256": canonical_sha256(core), "cell_count": len(cells)}


def compile_research_input(value: ResearchInput) -> Dict[str, Any]:
    normalized = normalize_proposal(value)
    tree = build_tree(normalized)
    matrix = build_matrix(tree, value.folds, value.seeds, normalized["metrics"])
    core = {"normalized_proposal": normalized, "tree": tree, "matrix": matrix}
    return {
        "schema_version": "egoagentos-research-compile/v1",
        **core,
        "compile_sha256": canonical_sha256(core),
        "next_gate": "INDEPENDENT_RESOURCE_REVIEW",
    }
