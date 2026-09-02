"""Independent resource reviewer with non-bypassable, plan-level vetoes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .ladder import canonical_sha256
from .models import ResourcePlan


class IndependentResourceReviewer:
    """Review compute structure, not scientific desirability or human authority."""

    reviewer_id = "resource-reviewer/external-v1"
    policy_version = "resource-efficiency-policy/1"

    def review(
        self,
        plan: ResourcePlan,
        *,
        expected_matrix_cells: Optional[int] = None,
        expected_folds: Optional[int] = None,
    ) -> Dict[str, Any]:
        findings: List[Dict[str, str]] = []

        def veto(code: str, message: str, remediation: str) -> None:
            findings.append(
                {"severity": "VETO", "code": code, "message": message, "remediation": remediation}
            )

        if expected_matrix_cells is not None and plan.matrix_cells != expected_matrix_cells:
            veto(
                "MATRIX_CARDINALITY_MISMATCH",
                "The resource declaration does not match the compiled experiment matrix.",
                "Set matrix_cells to the compiler-produced cell_count before approval.",
            )
        if expected_folds is not None and plan.folds != expected_folds:
            veto(
                "FOLD_CARDINALITY_MISMATCH",
                "The resource declaration does not match the compiled fold set.",
                "Set folds to the number of unique compiler input folds before approval.",
            )

        if plan.recomputes_fold_invariant_data or not plan.shared_dataset_cache:
            veto(
                "FOLD_INVARIANT_RECOMPUTE",
                "Fold-invariant data is copied or recomputed inside fold jobs.",
                "Materialize it once by content digest and mount a read-only shared cache.",
            )
        if plan.cpu_per_cell == 1 and plan.estimated_cpu_hours >= 8:
            veto(
                "CELL_GRAIN_TOO_COARSE",
                "A long matrix cell is pinned to one CPU and cannot saturate the host.",
                "Shard rows deterministically and merge them in stable row-key order.",
            )
        if plan.matrix_cells >= 4 and plan.row_shards < 2 and plan.estimated_cpu_hours >= 4:
            veto(
                "NO_ROW_SHARDS",
                "The plan has several expensive cells but no intra-cell row sharding.",
                "Declare row_shards >= 2 with exclusive output partitions.",
            )
        if (plan.estimated_cpu_hours + plan.estimated_gpu_hours) >= 2 and (
            not plan.resume_supported or plan.checkpoint_interval_minutes is None
        ):
            veto(
                "NO_CHECKPOINT_RESUME",
                "A material compute plan has no bounded checkpoint/resume contract.",
                "Checkpoint deterministic state and declare a restart interval.",
            )
        if not all(key in plan.output_partition_key for key in ("cell_id", "row_shard")):
            veto(
                "OUTPUT_COLLISION_RISK",
                "Output keys do not isolate both matrix cells and row shards.",
                "Use a write-once cell_id/row_shard partition and one atomic merger.",
            )
        if plan.global_phase_barrier and plan.matrix_cells > plan.concurrent_cells:
            veto(
                "UNNECESSARY_GLOBAL_BARRIER",
                "A global phase barrier stalls independent cells.",
                "Use dependency edges per cell and release ready successors independently.",
            )
        if plan.validation_coupled_to_compute:
            veto(
                "VALIDATION_COMPUTE_COUPLED",
                "Validation shares the mutable compute job and cannot independently replay it.",
                "Freeze raw outputs first; run validation as a separate read-only job.",
            )
        if plan.concurrent_cells == 1 and plan.matrix_cells >= 8:
            veto(
                "LOW_PARALLEL_UTILIZATION",
                "Independent matrix cells are serialized despite available parallel structure.",
                "Schedule a bounded worker pool and preserve deterministic merge order.",
            )

        decision = "VETO" if findings else "PASS"
        core = {
            "reviewer_id": self.reviewer_id,
            "policy_version": self.policy_version,
            "decision": decision,
            "findings": findings,
            "human_approval_observed": plan.human_approved,
            "human_approval_can_override": False,
            "compiled_expectation": {
                "matrix_cells": expected_matrix_cells,
                "folds": expected_folds,
            },
            "plan": plan.model_dump(mode="json"),
        }
        return {
            **core,
            "review_sha256": canonical_sha256(core),
            "gate": "BLOCK_EXECUTION" if findings else "ALLOW_APPROVAL_GATE",
        }
