"""Exact public-artifact policy for the Worker wheel and source distribution."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import FrozenSet, Iterable, List, Set


class PublicArtifactError(ValueError):
    """Raised when an artifact crosses the explicit public Worker boundary."""


PUBLIC_WHEEL_PAYLOAD: FrozenSet[str] = frozenset(
    """
apps/agentteams_bridge/__init__.py
apps/agentteams_bridge/cli.py
apps/agentteams_bridge/clients.py
apps/agentteams_bridge/errors.py
apps/agentteams_bridge/main.py
apps/agentteams_bridge/models.py
apps/agentteams_bridge/postgres_store.py
apps/agentteams_bridge/service.py
apps/agentteams_bridge/settings.py
apps/agentteams_bridge/store.py
apps/agentteams_bridge/transport.py
apps/agentteams_bridge/migrations/postgres/001_bridge_control_plane.sql
apps/api/__init__.py
apps/api/errors.py
apps/api/event_stream.py
apps/api/evidence.py
apps/api/main.py
apps/api/memory.py
apps/api/models.py
apps/api/polardb_preflight.py
apps/api/policy.py
apps/api/postgres_store.py
apps/api/provenance.py
apps/api/rxp_runtime.py
apps/api/service.py
apps/api/skill_runtime_api.py
apps/api/state_machine.py
apps/api/store.py
apps/api/store_contract.py
apps/api/store_factory.py
apps/api/fixtures/egolite-mcp-launch.yaml
apps/api/migrations/postgres/001_control_plane.sql
apps/api/migrations/postgres/002_ledger_boundaries.sql
benchmarks/__init__.py
benchmarks/adapter_worker.py
benchmarks/evidence_bundle.py
benchmarks/model.py
benchmarks/oracle.py
benchmarks/report.py
benchmarks/runner.py
benchmarks/statistics.py
benchmarks/trace_verifier.py
benchmarks/corpus/v1/scenario.schema.json
benchmarks/corpus/v1/scenarios.json
benchmarks/profiles/__init__.py
benchmarks/profiles/agentteams_rxp.py
benchmarks/profiles/base.py
benchmarks/profiles/deterministic_core.py
benchmarks/profiles/naive.py
benchmarks/schemas/agentteams-rxp-trace-v1.schema.json
benchmarks/secure_memory/__init__.py
benchmarks/secure_memory/canonical.py
benchmarks/secure_memory/manifest.py
benchmarks/secure_memory/models.py
benchmarks/secure_memory/schemas/campaign-event-v1.schema.json
benchmarks/secure_memory/schemas/candidate-proposal-v1.schema.json
benchmarks/secure_memory/schemas/checkpoint-v1.schema.json
benchmarks/secure_memory/schemas/issued-budget-ticket-v1.schema.json
benchmarks/secure_memory/schemas/model-request-v1.schema.json
benchmarks/secure_memory/schemas/model-response-v1.schema.json
benchmarks/secure_memory/schemas/run-manifest-v2.schema.json
benchmarks/secure_memory/schemas/signed-task-lease-v1.schema.json
benchmarks/secure_memory/schemas/ticket-template-v1.schema.json
benchmarks/secure_memory/schemas/trusted-fact-v1.schema.json
benchmarks/secure_memory/schemas/trusted-relation-v1.schema.json
experiments/__init__.py
experiments/fashion_mnist_amp/__init__.py
experiments/fashion_mnist_amp/contract.py
experiments/fashion_mnist_amp/run.py
experiments/fashion_mnist_amp/verify.py
integrations/__init__.py
integrations/agentteams/__init__.py
integrations/agentteams/agentteams-resources.yaml.tmpl
integrations/agentteams/benchmark_adapter.py
integrations/agentteams/blueprint.yaml
integrations/agentteams/message-envelope.schema.json
integrations/agentteams/official-contract.lock.json
integrations/agentteams/render_resources.py
integrations/agentteams/result-envelope.schema.json
integrations/agentteams/scripts/verify_official_contract.py
protocols/__init__.py
protocols/rxp/__init__.py
protocols/rxp/__main__.py
protocols/rxp/canonical.py
protocols/rxp/cli.py
protocols/rxp/demo.py
protocols/rxp/errors.py
protocols/rxp/evidence.py
protocols/rxp/grants.py
protocols/rxp/ledger.py
protocols/rxp/models.py
protocols/rxp/schema.py
protocols/rxp/schemas/rxp-decision-v1.schema.json
protocols/rxp/schemas/rxp-evidence-v1.schema.json
protocols/rxp/schemas/rxp-grant-v1.schema.json
protocols/rxp/schemas/rxp-intent-v1.schema.json
protocols/rxp/schemas/rxp-matrix-ledger-v1.schema.json
protocols/rxp/schemas/rxp-matrix-plan-v1.schema.json
protocols/rxp/schemas/rxp-receipt-v1.schema.json
semifinal_acceptance/README.md
semifinal_acceptance/__init__.py
semifinal_acceptance/__main__.py
semifinal_acceptance/bundle.py
semifinal_acceptance/cli.py
semifinal_acceptance/schemas/semifinal-acceptance-v1.schema.json
skill_runtime/__init__.py
skill_runtime/handlers.py
skill_runtime/registry.py
skills/__init__.py
skills/ablation-analyzer/SKILL.md
skills/ablation-analyzer/egoagentos.skill.yaml
skills/dataset-manifest/SKILL.md
skills/dataset-manifest/egoagentos.skill.yaml
skills/dataset-manifest/scripts/build_manifest.py
skills/evidence-gate/SKILL.md
skills/evidence-gate/egoagentos.skill.yaml
skills/research-memory/SKILL.md
skills/research-memory/egoagentos.skill.yaml
skills/research-plan/SKILL.md
skills/research-plan/egoagentos.skill.yaml
skills/safe-experiment-runner/SKILL.md
skills/safe-experiment-runner/egoagentos.skill.yaml
worker_distribution.py
""".split()
)

KNOWN_PRIVATE_SOURCE_FILES: FrozenSet[str] = frozenset({"apps/api/evaluator.py"})
PUBLIC_SOURCE_ROOTS = (
    "apps/api",
    "apps/agentteams_bridge",
    "benchmarks",
    "experiments",
    "integrations",
    "protocols",
    "semifinal_acceptance",
    "skill_runtime",
    "skills",
)

_PRIVATE_MARKERS = ("evaluator", "hidden", "sealed")
_DIST_INFO_DIRECTORY = "egoagentos_researchops-0.1.0.dist-info"
_WHEEL_METADATA_FILES: FrozenSet[str] = frozenset(
    {
        "METADATA",
        "RECORD",
        "WHEEL",
        "entry_points.txt",
        "licenses/LICENSE",
        "top_level.txt",
    }
)
_SDIST_DIRECTORY = "egoagentos_researchops-0.1.0"
_SDIST_BUILD_FILES: FrozenSet[str] = frozenset(
    {
        "LICENSE",
        "PKG-INFO",
        "README.md",
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        "egoagentos_researchops.egg-info/PKG-INFO",
        "egoagentos_researchops.egg-info/SOURCES.txt",
        "egoagentos_researchops.egg-info/dependency_links.txt",
        "egoagentos_researchops.egg-info/entry_points.txt",
        "egoagentos_researchops.egg-info/requires.txt",
        "egoagentos_researchops.egg-info/top_level.txt",
    }
)


def _canonical_member(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name:
        raise PublicArtifactError(f"non-canonical archive member: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PublicArtifactError(f"non-canonical archive member: {name!r}")
    canonical = path.as_posix()
    if canonical != name.rstrip("/"):
        raise PublicArtifactError(f"non-canonical archive member: {name!r}")
    return canonical


def _canonical_members(members: Iterable[str]) -> List[str]:
    normalized = [_canonical_member(name) for name in members]
    if len(normalized) != len(set(normalized)):
        raise PublicArtifactError("archive contains duplicate member names")
    return normalized


def _require_exact_members(
    actual: Set[str], expected: FrozenSet[str], artifact_name: str
) -> None:
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        raise PublicArtifactError(
            f"{artifact_name} violates public allowlist; "
            f"missing={missing}; unexpected={unexpected}"
        )


def validate_public_worker_wheel(members: Iterable[str]) -> None:
    """Require the exact public Worker payload and exact wheel metadata files."""

    normalized = set(_canonical_members(members))
    expected = frozenset(
        PUBLIC_WHEEL_PAYLOAD
        | {f"{_DIST_INFO_DIRECTORY}/{name}" for name in _WHEEL_METADATA_FILES}
    )
    _require_exact_members(normalized, expected, "Worker wheel")


def validate_public_worker_sdist(members: Iterable[str]) -> None:
    """Require the exact public source distribution file set."""

    normalized = _canonical_members(members)
    prefix = _SDIST_DIRECTORY + "/"
    if any(not name.startswith(prefix) for name in normalized):
        raise PublicArtifactError("Worker sdist has an unexpected top-level directory")
    relative = {name[len(prefix) :] for name in normalized}
    expected = frozenset(PUBLIC_WHEEL_PAYLOAD | _SDIST_BUILD_FILES)
    _require_exact_members(relative, expected, "Worker sdist")


def validate_public_staging_subset(members: Iterable[str]) -> None:
    """Reject any pre-existing staged file outside the exact public payload."""

    normalized = set(_canonical_members(members))
    unexpected = sorted(normalized - PUBLIC_WHEEL_PAYLOAD)
    if unexpected:
        raise PublicArtifactError(
            f"stale public Worker staging contains non-allowlisted files: {unexpected}"
        )


def validate_complete_public_staging(members: Iterable[str]) -> None:
    """Require an exactly complete staged public payload before wheel assembly."""

    normalized = set(_canonical_members(members))
    _require_exact_members(normalized, PUBLIC_WHEEL_PAYLOAD, "Worker staging tree")


def validate_discovered_public_files(members: Iterable[str]) -> None:
    """Reject source files selected by setuptools outside the exact payload."""

    normalized = {_canonical_member(name) for name in members}
    unexpected = sorted(normalized - PUBLIC_WHEEL_PAYLOAD - KNOWN_PRIVATE_SOURCE_FILES)
    if unexpected:
        raise PublicArtifactError(
            f"setuptools discovered files outside the public Worker allowlist: {unexpected}"
        )


def validate_no_ambiguous_private_sources(project_root: Path) -> None:
    """Reject new private-looking sources; the one legacy private file is explicit."""

    ambiguous = []
    for source_root in PUBLIC_SOURCE_ROOTS:
        root = project_root / source_root
        if not root.exists():
            continue
        for candidate in root.rglob("*"):
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(project_root).as_posix()
            if relative in KNOWN_PRIVATE_SOURCE_FILES:
                continue
            lowered_components = (part.lower() for part in PurePosixPath(relative).parts)
            if any(
                marker in component
                for component in lowered_components
                for marker in _PRIVATE_MARKERS
            ):
                ambiguous.append(relative)
    if ambiguous:
        raise PublicArtifactError(
            f"ambiguous private Worker source is not explicitly classified: {sorted(ambiguous)}"
        )
