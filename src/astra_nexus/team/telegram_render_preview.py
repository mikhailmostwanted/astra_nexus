from __future__ import annotations

import argparse
import json

from astra_nexus.config.settings import load_settings
from astra_nexus.team.intake import detect_requested_output_artifact
from astra_nexus.team.telegram_render import render_answer_for_telegram, strip_telegram_html

SAMPLE_HTML = """
<div data-message-author-role="assistant">
  <h2>Короткий итог</h2>
  <p>Первый абзац с <strong>жирным</strong> словом и
  <a href="https://example.com/report">ссылкой</a>.</p>
  <ul>
    <li>Первый пункт</li>
    <li>Второй пункт с <code>inline_code</code></li>
    <li><span class="source-chip"><a href="https://example.com/source">Shell + 1</a></span></li>
  </ul>
  <pre><code>print("Astra Nexus")</code></pre>
</div>
""".strip()

SAMPLE_TEXT = """
Короткий итог

Первый абзац с жирным словом и ссылкой.

- Первый пункт
- Второй пункт с inline_code

print("Astra Nexus")
""".strip()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = load_settings()
    text = args.text or SAMPLE_TEXT
    structured_answer = {
        "format": "html",
        "source": "preview",
        "text": text,
        "html": args.html or SAMPLE_HTML,
        "sourceLinks": [],
    }
    render = render_answer_for_telegram(text, structured_answer=structured_answer)
    requested, detected_format = detect_requested_output_artifact(args.user_request)
    requested_format = args.requested_format or detected_format
    output_requested_as_file = args.requested_file or requested
    send_internal_artifacts = (
        settings.team_telegram_send_internal_artifacts
        if args.send_internal_artifacts is None
        else args.send_internal_artifacts
    )
    send_requested_files = (
        settings.team_telegram_send_requested_files
        if args.send_requested_files is None
        else args.send_requested_files
    )

    print("extracted_structured_answer:")
    print(json.dumps(structured_answer, ensure_ascii=False, indent=2))
    print("")
    print("telegram_render:")
    print(f"parse_mode: {render.parse_mode or 'plain_text'}")
    print(f"chunks_count: {len(render.chunks)}")
    for index, chunk in enumerate(render.chunks, start=1):
        plain_chars = len(strip_telegram_html(chunk.text))
        print(f"--- chunk {index} chars={len(chunk.text)} plain_chars={plain_chars}")
        print(chunk.text)
    print("")
    print("artifacts:")
    print(f"send_internal_artifacts: {send_internal_artifacts}")
    print(f"send_requested_files: {send_requested_files}")
    print(f"output_requested_as_file: {output_requested_as_file}")
    print(f"requested_output_format: {requested_format}")
    print(
        "will_send_artifacts: "
        f"{bool(send_internal_artifacts or (send_requested_files and output_requested_as_file))}"
    )
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview ChatGPT answer -> Telegram HTML rendering."
    )
    parser.add_argument("--html", default="", help="Rendered assistant answer HTML.")
    parser.add_argument("--text", default="", help="Fallback plain text.")
    parser.add_argument(
        "--user-request",
        default="Ответь списком из трёх пунктов с жирным заголовком.",
        help="Original user request for artifact intent detection.",
    )
    parser.add_argument("--requested-file", action="store_true")
    parser.add_argument("--requested-format", choices=["md", "docx", "pdf", "txt", "unknown"])
    parser.add_argument("--send-internal-artifacts", action=argparse.BooleanOptionalAction)
    parser.add_argument("--send-requested-files", action=argparse.BooleanOptionalAction)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
