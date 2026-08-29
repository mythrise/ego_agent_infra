from pathlib import Path
from typing import Any, Dict

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _services() -> Dict[str, Dict[str, Any]]:
    payload = yaml.safe_load((REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    return payload["services"]


def test_compose_keeps_owner_credentials_out_of_long_lived_services() -> None:
    services = _services()

    backend_environment = services["backend"]["environment"]
    assert "${EGO_RUNTIME_USER" in backend_environment["EGO_DATABASE_URL"]
    assert "${EGO_RUNTIME_PASSWORD:?" in backend_environment["EGO_DATABASE_URL"]
    assert "EGO_POSTGRES_USER" not in str(backend_environment)
    assert "EGO_POSTGRES_PASSWORD" not in str(backend_environment)
    assert backend_environment["EGO_DATABASE_MIGRATION_MODE"] == "verify"
    assert "${EGO_OPERATOR_KEY:?" in backend_environment["EGO_OPERATOR_KEY"]
    assert backend_environment["EGO_ALLOW_UNAUTHENTICATED_DEMO"].endswith(":-false}")
    assert services["backend"]["depends_on"]["api-security"]["condition"] == (
        "service_completed_successfully"
    )

    bridge_environment = services["agentteams-bridge"]["environment"]
    assert "${EGO_AGENTTEAMS_RUNTIME_USER" in bridge_environment["EGO_AGENTTEAMS_DATABASE_URL"]
    assert (
        "${EGO_AGENTTEAMS_RUNTIME_PASSWORD:?" in bridge_environment["EGO_AGENTTEAMS_DATABASE_URL"]
    )
    assert "EGO_POSTGRES_USER" not in str(bridge_environment)
    assert "EGO_POSTGRES_PASSWORD" not in str(bridge_environment)
    assert "EGO_AGENTTEAMS_MIGRATION_DATABASE_URL" not in bridge_environment
    assert bridge_environment["EGO_AGENTTEAMS_MIGRATION_MODE"] == "verify"
    assert "${EGO_OPERATOR_KEY:?" in bridge_environment["EGO_OPERATOR_KEY"]
    assert "${EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY:?" in bridge_environment[
        "EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY"
    ]
    assert (
        bridge_environment["EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY"]
        != bridge_environment["EGO_OPERATOR_KEY"]
    )
    assert (
        services["agentteams-bridge"]["depends_on"]["agentteams-bridge-security"]["condition"]
        == "service_completed_successfully"
    )


def test_compose_owner_jobs_precede_distinct_runtime_logins() -> None:
    services = _services()

    api_migration_url = services["api-migrate"]["environment"]["EGO_DATABASE_URL"]
    bridge_migration_url = services["agentteams-bridge-migrate"]["environment"][
        "EGO_AGENTTEAMS_DATABASE_URL"
    ]
    for migration_url in (api_migration_url, bridge_migration_url):
        assert "${EGO_POSTGRES_USER" in migration_url
        assert "${EGO_POSTGRES_PASSWORD:?" in migration_url

    api_security = services["api-security"]
    bridge_security = services["agentteams-bridge-security"]
    assert api_security["depends_on"]["api-migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert bridge_security["depends_on"]["agentteams-bridge-migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert api_security["environment"]["EGO_RUNTIME_GROUP"] == "egoagentos_runtime"
    assert bridge_security["environment"]["EGO_RUNTIME_GROUP"] == ("egoagentos_bridge_runtime")
    assert (
        api_security["environment"]["EGO_RUNTIME_USER"]
        != bridge_security["environment"]["EGO_RUNTIME_USER"]
    )
    assert "${EGO_RUNTIME_PASSWORD:?" in api_security["environment"]["EGO_RUNTIME_PASSWORD"]
    assert (
        "${EGO_AGENTTEAMS_RUNTIME_PASSWORD:?"
        in bridge_security["environment"]["EGO_RUNTIME_PASSWORD"]
    )
