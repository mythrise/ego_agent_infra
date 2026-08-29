"""FastAPI-facing adapter for the deterministic Skill registry."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import Field

from skill_runtime import SkillInvocationError, SkillRegistry, default_handlers

from .errors import ControlPlaneError
from .models import StrictModel


class SkillInvokeRequest(StrictModel):
    correlation_id: str = Field(min_length=1, max_length=256)
    payload: Dict[str, Any]
    expected_version: Optional[str] = None
    expected_package_digest: Optional[str] = None


def default_skill_root() -> Path:
    configured = os.getenv("EGO_SKILLS_ROOT")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "skills"


def create_skill_registry(root: Optional[str] = None) -> SkillRegistry:
    return SkillRegistry.discover(
        Path(root) if root is not None else default_skill_root(),
        default_handlers(),
    )


def invoke_skill(
    registry: SkillRegistry,
    name: str,
    request: SkillInvokeRequest,
) -> Dict[str, Any]:
    try:
        return registry.invoke(
            name,
            request.payload,
            request.correlation_id,
            expected_version=request.expected_version,
            expected_package_digest=request.expected_package_digest,
        )
    except SkillInvocationError as error:
        status = 403 if error.code == "E_NOT_EXECUTABLE" else 422
        raise ControlPlaneError(
            "skill_%s" % error.code.lower(),
            error.message,
            status,
            {"trace": error.trace.public_dict()},
        ) from error
    except KeyError as error:
        raise ControlPlaneError("skill_not_found", str(error), 404) from error
    except RuntimeError as error:
        raise ControlPlaneError("skill_not_routable", str(error), 409) from error
