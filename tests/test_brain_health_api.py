from pathlib import Path

from fastapi.testclient import TestClient

from astra_nexus.api.app import create_app
from astra_nexus.config.settings import Settings


def test_brain_health_endpoint_for_dummy_provider(tmp_path: Path) -> None:
    settings = Settings(
        brain_provider="dummy",
        database_url="sqlite:///:memory:",
        workspace_base_path=tmp_path / "workspaces",
    )
    client = TestClient(create_app(settings))

    response = client.get("/api/brain/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "provider": "dummy",
        "message": "DummyBrainProvider готов к работе.",
    }
