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


def test_brain_health_endpoint_for_nodriver_is_lightweight(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def fail_if_deep_health_is_called(*args, **kwargs):
        raise AssertionError("Обычный health не должен открывать браузер")

    monkeypatch.setattr(
        "astra_nexus.api.routes.brain.check_nodriver_deep_health",
        fail_if_deep_health_is_called,
    )
    settings = Settings(
        brain_provider="nodriver",
        database_url="sqlite:///:memory:",
        workspace_base_path=tmp_path / "workspaces",
        data_dir=tmp_path / "data",
        nodriver_user_data_dir=tmp_path / "data/browser_profiles/default",
    )
    client = TestClient(create_app(settings))

    response = client.get("/api/brain/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "nodriver"
    assert payload["status"] == "configured"
    assert payload["user_data_dir_exists"] is False
    assert payload["profile_locked"] is False
    assert "astra-nexus-nodriver-smoke" in payload["message"]


def test_brain_health_endpoint_for_nodriver_reports_live_profile_lock(
    tmp_path: Path,
) -> None:
    settings = Settings(
        brain_provider="nodriver",
        database_url="sqlite:///:memory:",
        workspace_base_path=tmp_path / "workspaces",
        data_dir=tmp_path / "data",
        nodriver_user_data_dir=tmp_path / "data/browser_profiles/default",
    )
    app = create_app(settings)
    client = TestClient(app)
    app.state.brain_provider.client.session.lifecycle.acquire()

    try:
        response = client.get("/api/brain/health")
    finally:
        app.state.brain_provider.client.session.lifecycle.release()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "profile_locked"
    assert payload["profile_locked"] is True
    assert payload["lock_pid"] is not None


def test_brain_deep_health_uses_nodriver_lifecycle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeTab:
        async def evaluate(self, query: str) -> bool:
            return False

    class FakeBrowser:
        async def get(self, url: str) -> FakeTab:
            return FakeTab()

        def stop(self) -> None:
            return None

    async def fake_start(**kwargs):
        assert kwargs["user_data_dir"] == str(
            (tmp_path / "data/browser_profiles/default").resolve()
        )
        return FakeBrowser()

    monkeypatch.setattr(
        "astra_nexus.brain.nodriver.browser_session.BrowserSession._load_nodriver_start",
        lambda self: fake_start,
    )
    settings = Settings(
        brain_provider="nodriver",
        database_url="sqlite:///:memory:",
        workspace_base_path=tmp_path / "workspaces",
        data_dir=tmp_path / "data",
        nodriver_user_data_dir=tmp_path / "data/browser_profiles/default",
    )
    client = TestClient(create_app(settings))

    response = client.get("/api/brain/health/deep")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert not (tmp_path / "data/runtime/nodriver/default.lock").exists()
