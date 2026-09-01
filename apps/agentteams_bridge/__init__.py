"""Live AgentTeams bridge for EgoAgentOS ResearchOps.

The bridge is deliberately separate from the synthetic demo runner. A run is
labelled ``live`` only after the official AgentTeams Controller, Team resource,
Workers, and Matrix delivery path have all answered real requests.
"""

from .focused_service import (
    EgoTrustedMemoryProvider,
    FocusMemoryFetch,
    FocusMemoryMode,
    FocusMemoryProvider,
    FocusedAgentTeamsBridge,
)
from .models import BridgeRun, RunState
from .service import AgentTeamsBridge

__all__ = [
    "AgentTeamsBridge",
    "BridgeRun",
    "EgoTrustedMemoryProvider",
    "FocusMemoryFetch",
    "FocusMemoryMode",
    "FocusMemoryProvider",
    "FocusedAgentTeamsBridge",
    "RunState",
]
