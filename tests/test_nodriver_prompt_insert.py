import asyncio

import pytest

from astra_nexus.brain.nodriver.chatgpt_client import ChatGPTClient
from astra_nexus.brain.nodriver.exceptions import NoDriverPromptInsertFailedError
from astra_nexus.config.settings import Settings


class FakeSession:
    async def current_url(self) -> str:
        return "https://chatgpt.com/"

    async def current_title(self) -> str:
        return "ChatGPT"


class FakeTab:
    def __init__(self, result: object | list[object]) -> None:
        self.results = list(result) if isinstance(result, list) else [result]
        self.scripts: list[str] = []
        self.set_text_called = False

    async def evaluate(
        self,
        script: str,
        *,
        await_promise: bool = False,
        return_by_value: bool = False,
    ) -> object:
        self.scripts.append(script)
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]

    async def set_text(self, _prompt: str) -> None:
        self.set_text_called = True
        raise AssertionError("_fill_prompt не должен вызывать set_text у DOM-узла")


def make_client() -> ChatGPTClient:
    return ChatGPTClient(settings=Settings(_env_file=None), session=FakeSession())


def test_fill_prompt_uses_js_and_does_not_call_set_text() -> None:
    tab = FakeTab(
        {
            "ok": True,
            "textLength": 6,
            "visibleText": "Привет",
            "tagName": "div",
            "id": "prompt-textarea",
            "role": "textbox",
            "isContentEditable": True,
        }
    )
    client = make_client()

    details = asyncio.run(client._fill_prompt(tab, "Привет"))

    assert details["ok"] is True
    assert details["visibleText"] == "Привет"
    assert tab.set_text_called is False
    assert len(tab.scripts) == 1
    assert "PROMPT_INSERT" in tab.scripts[0]
    assert "document.execCommand('insertText'" in tab.scripts[0]
    assert "textContent = prompt" in tab.scripts[0]


def test_prompt_insert_script_accepts_prosemirror_multiline_normalization() -> None:
    client = make_client()

    script = client._build_prompt_insert_script("Первая строка\n\nВторая строка")

    assert "normalizeText" in script
    assert "linesInOrder" in script
    assert "normalizedVisible" in script
    assert "text_matches_after_dom_normalization" in script
    assert "KeyboardEvent('keydown'" in script


def test_fill_prompt_failure_maps_to_prompt_insert_failed() -> None:
    tab = FakeTab(
        {
            "ok": False,
            "error": "prompt_element_not_editable",
            "tagName": "div",
            "id": "prompt-textarea",
            "role": "textbox",
            "isContentEditable": False,
        }
    )
    client = make_client()

    with pytest.raises(NoDriverPromptInsertFailedError) as exc:
        asyncio.run(client._fill_prompt(tab, "Привет"))

    assert exc.value.status == "prompt_insert_failed"
    assert exc.value.stage == "chatgpt.prompt.insert.started"
    assert exc.value.selector
    assert exc.value.details["error"] == "prompt_element_not_editable"
    assert "dom_probe_summary" in exc.value.details


def test_fill_prompt_failure_keeps_insert_diagnostics() -> None:
    tab = FakeTab(
        [
            {
                "ok": False,
                "error": "text_not_visible_after_insert",
                "selector": "#prompt-textarea",
                "method": "exec_command_insert_text",
                "attempts": [{"method": "exec_command_insert_text", "ok": False}],
                "activeElement": {"tagName": "div", "id": "prompt-textarea"},
                "outerHTML": '<div id="prompt-textarea" role="textbox"></div>',
            },
            {
                "readyState": "complete",
                "textareaCount": 1,
                "contenteditableCount": 1,
                "textboxCount": 1,
                "candidate_count": 13,
                "composerFound": True,
                "loginState": "logged_in",
            },
        ]
    )
    client = make_client()

    with pytest.raises(NoDriverPromptInsertFailedError) as exc:
        asyncio.run(client._fill_prompt(tab, "Первая строка\n\nВторая строка"))

    assert exc.value.details["selector"] == "#prompt-textarea"
    assert exc.value.details["attempts"][0]["method"] == "exec_command_insert_text"
    assert exc.value.details["activeElement"]["id"] == "prompt-textarea"
    assert exc.value.details["outerHTML"].startswith("<div")
    assert exc.value.details["dom_probe_summary"]["candidate_count"] == 13
    assert len(tab.scripts) == 4
    assert tab.scripts[0].startswith("\n/* PROMPT_INSERT */")


def test_fill_prompt_result_must_be_object() -> None:
    tab = FakeTab("not an object")
    client = make_client()

    with pytest.raises(NoDriverPromptInsertFailedError) as exc:
        asyncio.run(client._fill_prompt(tab, "Привет"))

    assert exc.value.status == "prompt_insert_failed"
    assert exc.value.details["error"] == "prompt_insert_result_not_object"
