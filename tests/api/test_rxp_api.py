from copy import deepcopy

from fastapi.testclient import TestClient


def test_rxp_demo_is_explicitly_synthetic_and_structurally_verified(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/rxp/demo")
    assert response.status_code == 200
    payload = response.json()
    assert payload["protocol"] == "RXP/1.0"
    assert payload["physical_gpu_run"] is False
    assert payload["production_signature_trust"] is False
    assert payload["fixture_signature_verified"] is True
    assert payload["structural_verification"] == "PASS"
    assert payload["ledger"]["completeness"] == "COMPLETE"
    assert payload["ledger"]["expected_cell_count"] == 2
    assert payload["ledger"]["decided_cell_count"] == 2


def test_rxp_verify_accepts_fixture_and_rejects_tampered_entry(client: TestClient) -> None:
    ledger = client.get("/api/v1/rxp/demo").json()["ledger"]
    verified = client.post("/api/v1/rxp/verify", json={"ledger": ledger})
    assert verified.status_code == 200
    assert verified.json()["verified"] is True
    assert verified.json()["signature_trust_verified"] is False

    tampered = deepcopy(ledger)
    tampered["entries"][1]["document"]["action"] = "experiment.delete"
    rejected = client.post("/api/v1/rxp/verify", json={"ledger": tampered})
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"].startswith("rxp_")


def test_rxp_schema_catalog_is_content_addressed(client: TestClient) -> None:
    response = client.get("/api/v1/rxp/schemas")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_count"] == 7
    assert all(item["sha256"].startswith("sha256:") for item in payload["schemas"])
