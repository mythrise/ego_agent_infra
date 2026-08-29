"""Runtime discovery, deterministic routing, invocation, and release lifecycle.

The repository's ``skills/*`` folders are portable Agent Skill packages.  This
module adds the control-plane behavior that a static package cannot provide:
digest-pinned loading, a narrow executable allowlist, correlated invocation
traces, deterministic canary routing, rollback, and retirement.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, MutableMapping, NoReturn, Optional, Tuple

import yaml  # type: ignore[import-untyped]


_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
SkillHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _package_digest(skill_md: bytes, manifest: Mapping[str, Any]) -> str:
    hasher = hashlib.sha256()
    hasher.update(b"SKILL.md\0")
    hasher.update(skill_md)
    hasher.update(b"\0egoagentos.skill.yaml\0")
    hasher.update(_canonical_bytes(manifest))
    return hasher.hexdigest()


def _frontmatter(raw: str, package: Path) -> Mapping[str, Any]:
    if not raw.startswith("---\n"):
        raise ValueError(f"{package}: SKILL.md must start with YAML frontmatter")
    try:
        block, _ = raw[4:].split("\n---\n", 1)
    except ValueError as error:
        raise ValueError(f"{package}: SKILL.md frontmatter is not closed") from error
    parsed = yaml.safe_load(block)
    if not isinstance(parsed, Mapping):
        raise ValueError(f"{package}: SKILL.md frontmatter must be a mapping")
    return parsed


class SkillReleaseState(str, Enum):
    DRAFT = "draft"
    CANARY = "canary"
    ACTIVE = "active"
    RETIRED = "retired"


@dataclass(frozen=True)
class SkillDescriptor:
    name: str
    description: str
    version: str
    risk_level: str
    owner_agent: str
    reviewer_agent: str
    evidence_emitted: Tuple[str, ...]
    package_digest: str
    package_path: str
    manifest_state: SkillReleaseState
    executable: bool

    def public_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["evidence_emitted"] = list(self.evidence_emitted)
        value["manifest_state"] = self.manifest_state.value
        return value


@dataclass(frozen=True)
class SkillInvocationTrace:
    invocation_id: str
    correlation_id: str
    skill_name: str
    skill_version: str
    package_digest: str
    input_digest: str
    output_digest: Optional[str]
    release_state: SkillReleaseState
    status: str
    error_code: Optional[str]

    def public_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["release_state"] = self.release_state.value
        return value


class SkillInvocationError(RuntimeError):
    """A fail-closed Skill invocation with its correlated trace."""

    def __init__(self, code: str, message: str, trace: SkillInvocationTrace):
        super().__init__(message)
        self.code = code
        self.message = message
        self.trace = trace


class SkillRegistry:
    """In-process reference registry with deterministic release routing.

    The registry deliberately does not claim distributed consensus or durable
    production rollout state.  Deployments can persist the returned lifecycle
    events in the control-plane store; the reference implementation keeps them
    in memory so every lifecycle rule remains independently testable.
    """

    def __init__(
        self,
        descriptors: Iterable[SkillDescriptor],
        handlers: Optional[Mapping[str, SkillHandler]] = None,
    ) -> None:
        self._descriptors: Dict[Tuple[str, str], SkillDescriptor] = {}
        self._states: MutableMapping[Tuple[str, str], SkillReleaseState] = {}
        self._canary_percent: MutableMapping[Tuple[str, str], int] = {}
        self._handlers: Dict[str, SkillHandler] = dict(handlers or {})
        self._traces: Dict[str, SkillInvocationTrace] = {}
        for descriptor in descriptors:
            key = (descriptor.name, descriptor.version)
            if key in self._descriptors:
                raise ValueError(f"duplicate Skill release: {descriptor.name}@{descriptor.version}")
            self._descriptors[key] = descriptor
            self._states[key] = descriptor.manifest_state

    @classmethod
    def discover(
        cls,
        root: Path,
        handlers: Optional[Mapping[str, SkillHandler]] = None,
    ) -> "SkillRegistry":
        resolved_root = root.resolve(strict=True)
        descriptors = []
        for package in sorted(path for path in resolved_root.iterdir() if path.is_dir()):
            skill_path = package / "SKILL.md"
            manifest_path = package / "egoagentos.skill.yaml"
            if not skill_path.exists() and not manifest_path.exists():
                continue
            if not skill_path.is_file() or not manifest_path.is_file():
                raise ValueError(f"{package}: both SKILL.md and egoagentos.skill.yaml are required")
            skill_bytes = skill_path.read_bytes()
            frontmatter = _frontmatter(skill_bytes.decode("utf-8"), package)
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, Mapping):
                raise ValueError(f"{manifest_path}: manifest must be a mapping")
            name = str(frontmatter.get("name", ""))
            description = str(frontmatter.get("description", ""))
            version = str(manifest.get("version", ""))
            if not name or not description:
                raise ValueError(f"{skill_path}: name and description are required")
            if package.name != name:
                raise ValueError(f"{package}: directory name must match Skill name {name!r}")
            if not _SEMVER.fullmatch(version):
                raise ValueError(f"{manifest_path}: version must be strict SemVer x.y.z")
            try:
                state = SkillReleaseState(str(manifest.get("registry_state", "draft")))
            except ValueError as error:
                raise ValueError(f"{manifest_path}: unsupported registry_state") from error
            owner = str(manifest.get("owner_agent", ""))
            reviewer = str(manifest.get("reviewer_agent", ""))
            if not owner or not reviewer or owner == reviewer:
                raise ValueError(f"{manifest_path}: owner and reviewer must be distinct")
            evidence = manifest.get("evidence_emitted", [])
            if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
                raise ValueError(f"{manifest_path}: evidence_emitted must be a string list")
            descriptors.append(
                SkillDescriptor(
                    name=name,
                    description=description,
                    version=version,
                    risk_level=str(manifest.get("risk_level", "")),
                    owner_agent=owner,
                    reviewer_agent=reviewer,
                    evidence_emitted=tuple(evidence),
                    package_digest=_package_digest(skill_bytes, manifest),
                    package_path=str(package.relative_to(resolved_root.parent)),
                    manifest_state=state,
                    executable=name in (handlers or {}),
                )
            )
        if not descriptors:
            raise ValueError(f"no Skill packages discovered under {resolved_root}")
        return cls(descriptors, handlers)

    def catalog(self) -> Tuple[Dict[str, Any], ...]:
        items = []
        for key in sorted(self._descriptors):
            descriptor = self._descriptors[key]
            value = descriptor.public_dict()
            value["runtime_state"] = self._states[key].value
            value["canary_percent"] = self._canary_percent.get(key, 0)
            items.append(value)
        return tuple(items)

    def set_canary(self, name: str, version: str, percent: int) -> Dict[str, Any]:
        if percent < 1 or percent > 50:
            raise ValueError("canary percent must be between 1 and 50")
        key = self._require(name, version)
        if self._states[key] not in {SkillReleaseState.DRAFT, SkillReleaseState.CANARY}:
            raise ValueError("only draft or canary releases can enter canary")
        self._states[key] = SkillReleaseState.CANARY
        self._canary_percent[key] = percent
        return self._release_event("canary", key, {"percent": percent})

    def activate(self, name: str, version: str) -> Dict[str, Any]:
        key = self._require(name, version)
        if self._states[key] not in {
            SkillReleaseState.DRAFT,
            SkillReleaseState.CANARY,
            SkillReleaseState.ACTIVE,
        }:
            raise ValueError("retired releases cannot be activated; publish a new version")
        for candidate in self._keys_for(name):
            if candidate != key and self._states[candidate] == SkillReleaseState.ACTIVE:
                self._states[candidate] = SkillReleaseState.RETIRED
        self._states[key] = SkillReleaseState.ACTIVE
        self._canary_percent.pop(key, None)
        return self._release_event("activate", key, {})

    def retire(self, name: str, version: str) -> Dict[str, Any]:
        key = self._require(name, version)
        self._states[key] = SkillReleaseState.RETIRED
        self._canary_percent.pop(key, None)
        return self._release_event("retire", key, {})

    def rollback(self, name: str, target_version: str) -> Dict[str, Any]:
        target = self._require(name, target_version)
        if self._states[target] == SkillReleaseState.RETIRED:
            self._states[target] = SkillReleaseState.DRAFT
        event = self.activate(name, target_version)
        event["action"] = "rollback"
        return event

    def resolve(self, name: str, correlation_id: str) -> SkillDescriptor:
        candidates = self._keys_for(name)
        if not candidates:
            raise KeyError(f"unknown Skill: {name}")
        canaries = [key for key in candidates if self._states[key] == SkillReleaseState.CANARY]
        for key in sorted(canaries, reverse=True):
            bucket = int(_digest({"correlation_id": correlation_id, "skill": name})[:8], 16) % 100
            if bucket < self._canary_percent[key]:
                return self._descriptors[key]
        active = [key for key in candidates if self._states[key] == SkillReleaseState.ACTIVE]
        if len(active) == 1:
            return self._descriptors[active[0]]
        if len(active) > 1:
            raise RuntimeError(f"multiple active releases for {name}")
        # A first release has no incumbent to receive the non-canary bucket.
        # Keep it routable while still recording CANARY state; production
        # deployments should normally seed an active release before canarying a
        # successor.
        if len(canaries) == 1:
            return self._descriptors[canaries[0]]
        drafts = [key for key in candidates if self._states[key] == SkillReleaseState.DRAFT]
        if len(drafts) == 1:
            return self._descriptors[drafts[0]]
        raise RuntimeError(f"no routable release for {name}")

    def invoke(
        self,
        name: str,
        payload: Mapping[str, Any],
        correlation_id: str,
        *,
        expected_version: Optional[str] = None,
        expected_package_digest: Optional[str] = None,
    ) -> Dict[str, Any]:
        descriptor = self.resolve(name, correlation_id)
        key = (descriptor.name, descriptor.version)
        input_digest = _digest(payload)
        invocation_id = "skinv_" + _digest(
            {
                "correlation_id": correlation_id,
                "input_digest": input_digest,
                "package_digest": descriptor.package_digest,
                "skill": descriptor.name,
                "version": descriptor.version,
            }
        )[:24]

        def fail(code: str, message: str) -> NoReturn:
            trace = SkillInvocationTrace(
                invocation_id=invocation_id,
                correlation_id=correlation_id,
                skill_name=descriptor.name,
                skill_version=descriptor.version,
                package_digest=descriptor.package_digest,
                input_digest=input_digest,
                output_digest=None,
                release_state=self._states[key],
                status="FAIL",
                error_code=code,
            )
            self._traces[invocation_id] = trace
            raise SkillInvocationError(code, message, trace)

        if expected_version is not None and descriptor.version != expected_version:
            fail("E_VERSION_PIN", "resolved Skill version does not match the requested pin")
        if expected_package_digest is not None:
            if not _SHA256.fullmatch(expected_package_digest):
                fail("E_PACKAGE_DIGEST", "expected package digest is not a lowercase SHA-256")
            if descriptor.package_digest != expected_package_digest:
                fail("E_PACKAGE_DIGEST", "resolved Skill package digest does not match")
        handler = self._handlers.get(descriptor.name)
        if handler is None:
            fail("E_NOT_EXECUTABLE", "Skill package is discoverable but has no allowlisted handler")
        try:
            output = dict(handler(payload))
        except SkillInvocationError:
            raise
        except ValueError as error:
            fail("E_INPUT", str(error))
        except Exception as error:  # pragma: no cover - defensive trust boundary
            fail("E_HANDLER", f"handler failed closed: {type(error).__name__}")
        output_digest = _digest(output)
        trace = SkillInvocationTrace(
            invocation_id=invocation_id,
            correlation_id=correlation_id,
            skill_name=descriptor.name,
            skill_version=descriptor.version,
            package_digest=descriptor.package_digest,
            input_digest=input_digest,
            output_digest=output_digest,
            release_state=self._states[key],
            status="PASS",
            error_code=None,
        )
        self._traces[invocation_id] = trace
        return {"result": output, "trace": trace.public_dict()}

    def trace(self, invocation_id: str) -> Dict[str, Any]:
        try:
            return self._traces[invocation_id].public_dict()
        except KeyError as error:
            raise KeyError(f"unknown Skill invocation: {invocation_id}") from error

    def _require(self, name: str, version: str) -> Tuple[str, str]:
        key = (name, version)
        if key not in self._descriptors:
            raise KeyError(f"unknown Skill release: {name}@{version}")
        return key

    def _keys_for(self, name: str) -> Tuple[Tuple[str, str], ...]:
        return tuple(key for key in self._descriptors if key[0] == name)

    def _release_event(
        self, action: str, key: Tuple[str, str], details: Mapping[str, Any]
    ) -> Dict[str, Any]:
        body = {
            "action": action,
            "name": key[0],
            "version": key[1],
            "package_digest": self._descriptors[key].package_digest,
            "state": self._states[key].value,
            "details": dict(details),
        }
        body["event_digest"] = _digest(body)
        return body
