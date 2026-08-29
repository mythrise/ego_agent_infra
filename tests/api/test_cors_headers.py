from fastapi.testclient import TestClient


def test_browser_can_read_one_time_approval_token_header(client: TestClient) -> None:
    response = client.get(
        "/api/v1/health",
        headers={"Origin": "http://localhost:4173"},
    )
    assert response.status_code == 200
    exposed = {
        item.strip().lower()
        for item in response.headers["access-control-expose-headers"].split(",")
    }
    assert "x-ego-approval-token" in exposed
