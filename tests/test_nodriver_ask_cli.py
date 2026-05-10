import asyncio
import json
from pathlib import Path

import astra_nexus.brain.nodriver.ask as ask_module
from astra_nexus.brain.nodriver.ask import run
from astra_nexus.brain.nodriver.exceptions import (
    NoDriverPromptBoxNotFoundError,
    NoDriverPromptInsertFailedError,
    NoDriverTimeoutError,
)
from astra_nexus.config.settings import Settings


class FailingProvider:
    async def ask(self, agent_id: str, prompt: str, context: dict | None = None):
        raise NoDriverPromptBoxNotFoundError(
            "Поле ввода ChatGPT не найдено.",
            stage="chatgpt.prompt_box.search.started",
            url="https://chatgpt.com/",
            selector="#prompt-textarea",
        )


class OkProvider:
    async def ask(self, agent_id: str, prompt: str, context: dict | None = None):
        class Response:
            content = "Astra Nexus online."

        return Response()


class PromptInsertFailingProvider:
    async def ask(self, agent_id: str, prompt: str, context: dict | None = None):
        raise NoDriverPromptInsertFailedError(
            "Не удалось вставить prompt в поле ввода ChatGPT.",
            stage="chatgpt.prompt.insert.started",
            url="https://chatgpt.com/",
            page_title="ChatGPT",
            selector="#prompt-textarea",
            details={
                "selector": "#prompt-textarea",
                "activeElement": {"tagName": "div", "id": "prompt-textarea"},
                "outerHTML": '<div id="prompt-textarea" role="textbox"></div>',
                "dom_probe_summary": {"ready_state": "complete", "candidate_count": 13},
                "attempts": [{"method": "exec_command_insert_text", "ok": False}],
            },
        )


class ResponseWaitFailingProvider:
    async def ask(self, agent_id: str, prompt: str, context: dict | None = None):
        error = NoDriverTimeoutError(
            "ChatGPT Web завершил UI idle, но финальный текст assistant не найден в DOM.",
            stage="chatgpt.response.wait.started",
            details={
                "debug_artifact_path": "data/debug/nodriver/nodriver_response_wait_manual.json"
            },
        )
        error.debug_report_path = "data/debug/nodriver/nodriver_response_wait_manual.json"
        raise error


def test_nodriver_ask_cli_prints_structured_error(capsys) -> None:
    exit_code = asyncio.run(run(["Привет"], provider=FailingProvider()))

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "status: prompt_box_not_found" in output
    assert "stage: chatgpt.prompt_box.search.started" in output
    assert "message: Поле ввода ChatGPT не найдено." in output


def test_nodriver_ask_cli_writes_prompt_insert_debug_report(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        ask_module,
        "load_settings",
        lambda: Settings(_env_file=None, data_dir=tmp_path),
    )

    exit_code = asyncio.run(run(["Привет"], provider=PromptInsertFailingProvider()))

    output = capsys.readouterr().out
    report_path = tmp_path / "debug/nodriver/prompt_insert_failed.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert "debug_report:" in output
    assert payload["error_code"] == "prompt_insert_failed"
    assert payload["url"] == "https://chatgpt.com/"
    assert payload["page_title"] == "ChatGPT"
    assert payload["selector"] == "#prompt-textarea"
    assert payload["activeElement"]["id"] == "prompt-textarea"
    assert payload["dom_probe_summary"]["candidate_count"] == 13
    assert payload["attempts"][0]["method"] == "exec_command_insert_text"
    assert payload["details"]["activeElement"]["id"] == "prompt-textarea"
    assert payload["details"]["dom_probe_summary"]["candidate_count"] == 13
    assert payload["details"]["attempts"][0]["method"] == "exec_command_insert_text"


def test_nodriver_ask_cli_prints_response_wait_debug_report(capsys) -> None:
    exit_code = asyncio.run(run(["Привет"], provider=ResponseWaitFailingProvider()))

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "status: response_timeout" in output
    assert "stage: chatgpt.response.wait.started" in output
    assert "debug_report: data/debug/nodriver/nodriver_response_wait_manual.json" in output


def test_nodriver_ask_cli_prints_answer(capsys) -> None:
    exit_code = asyncio.run(run(["Привет"], provider=OkProvider()))

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "status: ok" in output
    assert "Astra Nexus online." in output
