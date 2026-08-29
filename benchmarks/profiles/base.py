"""Profile interface shared by local and external benchmark targets."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from benchmarks.model import Observation, Scenario


class Profile(ABC):
    name: str
    description: str

    @abstractmethod
    def run(
        self,
        scenario: Scenario,
        seed: int,
        repetition: int,
        workspace: Path,
    ) -> Observation:
        """Execute one measured trial. Implementations must not invent integration results."""
