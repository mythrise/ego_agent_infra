"""Injectable monotonic time; wall clock values are never ordering authority."""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def monotonic_ns(self) -> int:
        """Return a monotonic timestamp in nanoseconds."""


class SystemMonotonicClock:
    def monotonic_ns(self) -> int:
        return time.monotonic_ns()
