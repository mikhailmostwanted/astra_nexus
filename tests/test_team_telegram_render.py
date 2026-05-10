from __future__ import annotations

from astra_nexus.team.telegram_render import (
    TELEGRAM_HTML_PARSE_MODE,
    render_answer_for_telegram,
    strip_telegram_html,
)


def test_html_answer_with_paragraphs_becomes_telegram_html_with_breaks() -> None:
    render = render_answer_for_telegram(
        "Первый абзац\n\nВторой абзац",
        structured_answer={
            "html": "<div><p>Первый <strong>абзац</strong></p><p>Второй абзац</p></div>"
        },
    )

    assert render.parse_mode == TELEGRAM_HTML_PARSE_MODE
    assert render.chunks[0].text == "Первый <b>абзац</b>\n\nВторой абзац"


def test_lists_and_headings_are_preserved_readably() -> None:
    render = render_answer_for_telegram(
        "",
        structured_answer={"html": "<h2>План</h2><ol><li>Первый</li><li><em>Второй</em></li></ol>"},
    )

    text = render.chunks[0].text
    assert "<b>План</b>" in text
    assert "1. Первый" in text
    assert "2. <i>Второй</i>" in text


def test_code_blocks_and_inline_code_are_preserved() -> None:
    render = render_answer_for_telegram(
        "",
        structured_answer={
            "html": '<p>Команда <code>ruff check</code></p><pre><code>print("ok")</code></pre>'
        },
    )

    text = render.chunks[0].text
    assert "<code>ruff check</code>" in text
    assert "<pre><code>print(&quot;ok&quot;)</code></pre>" in text


def test_long_answer_splits_without_broken_html() -> None:
    paragraphs = "".join(f"<p>Абзац {index} {'текст ' * 20}</p>" for index in range(80))
    render = render_answer_for_telegram("", structured_answer={"html": paragraphs}, chunk_limit=900)

    assert len(render.chunks) > 1
    assert all(chunk.parse_mode == TELEGRAM_HTML_PARSE_MODE for chunk in render.chunks)
    assert all(chunk.text.count("<b>") == chunk.text.count("</b>") for chunk in render.chunks)
    assert all(len(strip_telegram_html(chunk.text)) <= 900 for chunk in render.chunks)


def test_source_chips_do_not_appear_in_middle_of_text() -> None:
    render = render_answer_for_telegram(
        "",
        structured_answer={
            "html": (
                '<p>Ответ до <a class="source-chip" href="https://example.com/s">Shell + 1</a> '
                "после.</p>"
            )
        },
    )

    text = render.chunks[0].text
    assert "Shell + 1" not in text
    assert "Ответ до после." in strip_telegram_html(text)
    assert "https://example.com/s" in text
