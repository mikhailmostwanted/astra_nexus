from astra_nexus.brain.nodriver.dom_probe import choose_visible_candidate
from astra_nexus.brain.nodriver.selectors import PROMPT_INPUT_SELECTORS


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
