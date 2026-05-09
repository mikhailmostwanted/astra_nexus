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
    def __init__(self, result: object) -> None:
        self.result = result
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
        return self.result

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


def test_fill_prompt_result_must_be_object() -> None:
    tab = FakeTab("not an object")
    client = make_client()

    with pytest.raises(NoDriverPromptInsertFailedError) as exc:
        asyncio.run(client._fill_prompt(tab, "Привет"))

    assert exc.value.status == "prompt_insert_failed"
    assert exc.value.details["error"] == "prompt_insert_result_not_object"
