import asyncio
from pathlib import Path

import astra_nexus.brain.nodriver.insert_probe as insert_probe
from astra_nexus.config.settings import Settings


class FakeSession:
    user_data_dir = Path("profile")
    tab = object()

    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FakeClient:
    def __init__(self) -> None:
        self.prompt: str | None = None
        self.ask_called = False

    async def _fill_prompt(self, tab: object, prompt: str) -> dict[str, object]:
        self.prompt = prompt
        return {
            "ok": True,
            "selector": "#prompt-textarea",
            "method": "exec_command_insert_text",
            "textLength": len(prompt),
        }

    async def ask(self, prompt: str) -> str:
        self.ask_called = True
        raise AssertionError("insert-probe не должен отправлять prompt")


def test_insert_probe_inserts_prompt_without_sending(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    session = FakeSession()
    client = FakeClient()
    report_path = tmp_path / "debug/nodriver/dom_probe.json"

    async def fake_collect_dom_probe(_session: FakeSession) -> dict[str, object]:
        return {
            "status": "ok",
            "ready_state": "complete",
            "textarea_count": 1,
            "contenteditable_count": 1,
            "textbox_count": 1,
            "candidate_count": 13,
            "login_state": "logged_in",
            "composer_found": True,
            "marked_selector": "#prompt-textarea",
        }

    def fake_write_dom_probe_report(
        _settings: Settings,
        _payload: dict[str, object],
    ) -> Path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("{}", encoding="utf-8")
        return report_path

    monkeypatch.setattr(
        insert_probe,
        "load_settings",
        lambda: Settings(_env_file=None, data_dir=tmp_path),
    )
    monkeypatch.setattr(
        insert_probe,
        "BrowserSession",
        lambda settings, lifecycle_context: session,
    )
    monkeypatch.setattr(
        insert_probe,
        "ChatGPTClient",
        lambda settings, session: client,
    )
    monkeypatch.setattr(insert_probe, "collect_dom_probe", fake_collect_dom_probe)
    monkeypatch.setattr(insert_probe, "write_dom_probe_report", fake_write_dom_probe_report)

    exit_code = asyncio.run(insert_probe.run(["тестовый", "текст"]))

    output = capsys.readouterr().out
    assert exit_code == 0
    assert client.prompt == "тестовый текст"
    assert client.ask_called is False
    assert session.stopped is True
    assert "status: ok" in output
    assert "selector: #prompt-textarea" in output
    assert f"dom_probe: {report_path}" in output
