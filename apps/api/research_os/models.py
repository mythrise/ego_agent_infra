"""Typed contracts for the research input ladder and deterministic experiment plan."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class InputTier(str, Enum):
    DETAILED = "detailed_proposal"
    FUZZY = "fuzzy_idea"
    BASELINE_ONLY = "baseline_only"


class TruthClass(str, Enum):
    LIVE = "LIVE"
    LIVE_LOCAL = "LIVE_LOCAL"
    SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"
    NOT_RUN = "NOT_RUN"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    UNVERIFIED = "UNVERIFIED"


class ModuleSpec(BaseModel):
    """A user-supplied module or alternative in a proposal hierarchy."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(default="module", min_length=1, max_length=40)
    hypothesis: str = Field(default="")
    runnable: bool = False
    parameters: Dict[str, Any] = Field(default_factory=dict)
    children: List["ModuleSpec"] = Field(default_factory=list)


class ResearchInput(BaseModel):
    """The common input accepted at all three capability levels."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=12000)
    baseline: str = Field(min_length=1, max_length=40000)
    proposal: Optional[str] = Field(default=None, max_length=80000)
    idea: Optional[str] = Field(default=None, max_length=30000)
    branches: List[str] = Field(default_factory=list, max_length=64)
    core_code: Optional[str] = Field(default=None, max_length=60000)
    hierarchy: List[ModuleSpec] = Field(default_factory=list)
    metrics: List[str] = Field(default_factory=list, max_length=64)
    folds: List[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    seeds: List[int] = Field(default_factory=lambda: [17])
    requested_tier: Optional[InputTier] = None
    source_repository: Optional[str] = Field(default=None, max_length=500)

    @field_validator("branches", "metrics")
    @classmethod
    def nonempty_strings(cls, value: List[str]) -> List[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("values must be unique")
        return cleaned

    @field_validator("folds", "seeds")
    @classmethod
    def unique_integers(cls, value: List[int]) -> List[int]:
        if not value:
            raise ValueError("at least one value is required")
        if len(value) != len(set(value)):
            raise ValueError("values must be unique")
        return value


class ResourcePlan(BaseModel):
    """Machine-readable execution plan inspected outside the normal approval path."""

    model_config = ConfigDict(extra="forbid")

    matrix_cells: int = Field(ge=1)
    folds: int = Field(ge=1)
    estimated_cpu_hours: float = Field(ge=0)
    estimated_gpu_hours: float = Field(ge=0)
    cpu_per_cell: int = Field(ge=1)
    gpu_per_cell: float = Field(ge=0)
    row_shards: int = Field(ge=1)
    checkpoint_interval_minutes: Optional[int] = Field(default=None, ge=1)
    resume_supported: bool
    shared_dataset_cache: bool
    recomputes_fold_invariant_data: bool
    concurrent_cells: int = Field(ge=1)
    global_phase_barrier: bool
    validation_coupled_to_compute: bool
    output_partition_key: str = Field(default="cell_id/row_shard", max_length=200)
    human_approved: bool = False


class CompileResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: ResearchInput
    resource_plan: Optional[ResourcePlan] = None


class FocusMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern="^(system|user|assistant|tool)$")
    content: str = Field(min_length=1, max_length=50000)


class StageCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: str = Field(min_length=1, max_length=120)
    user_id: str = Field(min_length=1, max_length=120)
    session_id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=160)
    stage_id: str = Field(min_length=1, max_length=160)
    messages: List[FocusMessage] = Field(min_length=1, max_length=100)
    decisions: List[str] = Field(default_factory=list, max_length=40)
    evidence: List[str] = Field(default_factory=list, max_length=80)
    blockers: List[str] = Field(default_factory=list, max_length=40)
    next_actions: List[str] = Field(default_factory=list, max_length=40)
    validated_facts: List[str] = Field(default_factory=list, max_length=80)

    @model_validator(mode="after")
    def requires_focus_material(self) -> "StageCommitRequest":
        if not (self.decisions or self.evidence or self.blockers or self.next_actions):
            raise ValueError(
                "a stage commit must include a decision, evidence, blocker, or next action"
            )
        return self


ModuleSpec.model_rebuild()
