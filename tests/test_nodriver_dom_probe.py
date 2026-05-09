import asyncio

from astra_nexus.brain.nodriver import dom_probe
from astra_nexus.brain.nodriver.dom_probe import (
    choose_visible_candidate,
    normalize_dom_probe_payload,
)
from astra_nexus.brain.nodriver.selectors import PROMPT_INPUT_SELECTORS
from astra_nexus.config.settings import Settings


def test_prompt_input_selectors_include_current_chatgpt_fallbacks() -> None:
    expected = {
        "#prompt-textarea",
        "textarea#prompt-textarea",
        'textarea[data-testid="prompt-textarea"]',
        '[data-testid="composer-textarea"]',
        '[data-testid="composer-text-input"]',
        "div#prompt-textarea",
        'div[contenteditable="true"][data-lexical-editor="true"]',
        '[contenteditable="true"][role="textbox"]',
        '[role="textbox"]',
        'div[contenteditable="true"]',
        "textarea",
    }

    assert expected.issubset(set(PROMPT_INPUT_SELECTORS))


def test_choose_visible_candidate_skips_hidden_elements() -> None:
    candidates = [
        {
            "selector": "#hidden",
            "tag": "textarea",
            "visible": False,
            "width": 200,
            "height": 30,
            "display": "none",
            "visibility": "visible",
        },
        {
            "selector": "#composer",
            "tag": "div",
            "visible": True,
            "width": 420,
            "height": 80,
            "display": "block",
            "visibility": "visible",
        },
    ]

    assert choose_visible_candidate(candidates)["selector"] == "#composer"


def test_choose_visible_candidate_returns_none_without_visible_elements() -> None:
    candidates = [
        {
            "selector": "#zero",
            "tag": "textarea",
            "visible": True,
            "width": 0,
            "height": 0,
            "display": "block",
            "visibility": "visible",
        }
    ]

    assert choose_visible_candidate(candidates) is None


def test_dom_probe_normalizes_string_candidate_without_crashing() -> None:
    payload = normalize_dom_probe_payload(
        {
            "ready_state": {"type": "string", "value": "complete"},
            "candidate_count": {"type": "number", "value": 1},
            "candidates": ["unexpected"],
        }
    )

    assert payload["ready_state"] == "complete"
    assert payload["candidate_count"] == 1
    assert payload["candidates"] == [{"raw": "unexpected"}]


def test_dom_probe_normalizes_candidates_to_list_of_dicts() -> None:
    payload = normalize_dom_probe_payload(
        {
            "candidates": {
                "selectorHint": {"type": "string", "value": "#prompt-textarea"},
                "isVisible": {"type": "boolean", "value": True},
            }
        }
    )

    assert payload["candidates"][0]["selector"] == "#prompt-textarea"
    assert payload["candidates"][0]["visible"] is True


def test_dom_probe_plain_object_does_not_turn_counts_into_none() -> None:
    payload = normalize_dom_probe_payload(
        {
            "url": "https://chatgpt.com/",
            "title": "ChatGPT",
            "readyState": "complete",
            "textareaCount": 1,
            "contenteditableCount": 0,
            "textboxCount": 1,
            "loginButtonCount": 0,
            "candidate_count": 1,
            "loginState": "logged_in",
            "composerFound": True,
            "candidates": [],
        }
    )

    assert payload["current_url"] == "https://chatgpt.com/"
    assert payload["page_title"] == "ChatGPT"
    assert payload["ready_state"] == "complete"
    assert payload["textarea_count"] == 1
    assert payload["contenteditable_count"] == 0
    assert payload["textbox_count"] == 1
    assert payload["login_buttons_count"] == 0
    assert payload["candidate_count"] == 1
    assert payload["login_state"] == "logged_in"


def test_dom_probe_run_stops_session_when_probe_raises(monkeypatch, tmp_path, capsys) -> None:
    stopped = False

    class FakeSession:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def stop(self) -> None:
            nonlocal stopped
            stopped = True

    async def failing_collect(_session) -> dict:
        raise AttributeError("'str' object has no attribute 'get'")

    monkeypatch.setattr(
        dom_probe,
        "load_settings",
        lambda: Settings(
            data_dir=tmp_path / "data",
            nodriver_user_data_dir=tmp_path / "profile",
            nodriver_keep_browser_open_on_error=False,
        ),
    )
    monkeypatch.setattr(dom_probe, "BrowserSession", FakeSession)
    monkeypatch.setattr(dom_probe, "collect_dom_probe", failing_collect)

    exit_code = asyncio.run(dom_probe.run())

    output = capsys.readouterr().out
    assert exit_code == 1
    assert stopped is True
    assert "status: dom_probe_failed" in output


def test_dom_probe_cancelled_error_returns_without_traceback(monkeypatch, tmp_path, capsys) -> None:
    stopped = False

    class FakeSession:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def stop(self) -> None:
            nonlocal stopped
            stopped = True

    async def cancelled_collect(_session) -> dict:
        raise asyncio.CancelledError

    monkeypatch.setattr(
        dom_probe,
        "load_settings",
        lambda: Settings(
            data_dir=tmp_path / "data",
            nodriver_user_data_dir=tmp_path / "profile",
            nodriver_keep_browser_open_on_error=False,
        ),
    )
    monkeypatch.setattr(dom_probe, "BrowserSession", FakeSession)
    monkeypatch.setattr(dom_probe, "collect_dom_probe", cancelled_collect)

    exit_code = asyncio.run(dom_probe.run())

    output = capsys.readouterr().out
    assert exit_code == 130
    assert stopped is True
    assert "Остановлено пользователем." in output
