from __future__ import annotations

import asyncio
from typing import Any

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.dom_probe import evaluate_script
from astra_nexus.brain.nodriver.turn_probe import (
    build_turn_dump_probe_script,
    normalize_turn_items,
)
from astra_nexus.config.settings import load_settings
from astra_nexus.utils.logging import configure_logging


async def amain() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    session = BrowserSession(settings, lifecycle_context="doctor")
    try:
        await session.start()
        tab = await session.ensure_chatgpt_page()
        payload = await _collect_turn_dump(tab)
        if not payload["turns"]:
            latest_url = await _latest_conversation_url(tab)
            if latest_url:
                tab = await session.open_url(latest_url)
                payload = await _collect_turn_dump(tab)
    finally:
        await session.stop()

    print("status: ok")
    print(f"url: {payload.get('url')}")
    print(f"title: {payload.get('title')}")
    print(f"turn_count: {payload.get('turnCount')}")
    print(f"user_count: {payload.get('userCount')}")
    print(f"assistant_count: {payload.get('assistantCount')}")
    for item in payload["turns"]:
        _print_turn(item)
    return 0


async def _collect_turn_dump(tab: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for _attempt in range(20):
        payload = normalize_turn_items(
            await evaluate_script(tab, build_turn_dump_probe_script(limit=5, include_html=False))
        )
        if payload["turns"] or int(payload.get("turnCount") or 0) > 0:
            return payload
        await asyncio.sleep(0.5)
    return payload


async def _latest_conversation_url(tab: Any) -> str | None:
    result = await evaluate_script(
        tab,
        """
(() => {
  const links = Array.from(document.querySelectorAll('a[href*="/c/"]'))
    .map((node) => node.href || node.getAttribute('href') || '')
    .filter(Boolean);
  return links[0] || null;
})()
""",
    )
    return str(result) if result else None


def _print_turn(item: dict[str, Any]) -> None:
    print("")
    print(f"turn[{item.get('index')}]:")
    print(f"  role: {item.get('role')}")
    print(f"  data_turn: {item.get('dataTurn') or ''}")
    print(f"  data_testid: {item.get('dataTestid') or ''}")
    print(f"  aria_label: {item.get('ariaLabel') or ''}")
    print(f"  text_length: {item.get('textLength')}")
    print(f"  text_preview: {_line(item.get('textPreview'))}")
    print(f"  raw_text_preview: {_line(item.get('rawTextPreview'))}")
    print(f"  html_length: {item.get('htmlLength')}")
    print(f"  class_names: {_line(item.get('classNames'))}")
    print(f"  selector_summary: {_line(item.get('selectorSummary'))}")
    print(f"  markdown_prose_blocks: {bool(item.get('hasMarkdownProseBlocks'))}")
    print(f"  thinking_reasoning_blocks: {bool(item.get('hasThinkingReasoningBlocks'))}")
    print(f"  hidden_aria_hidden_elements: {bool(item.get('hasHiddenAriaHiddenElements'))}")
    if item.get("role") != "assistant":
        return
    selected = item.get("selectedFinalCandidate") or {}
    print(
        "  selected_final_candidate: "
        f"{selected.get('source') or ''} "
        f"{selected.get('textLength') or 0} "
        f"{_line(selected.get('textPreview'))}"
    )
    print("  final_text_candidates:")
    for candidate in _list_of_dicts(item.get("finalCandidatePreviews")):
        print(
            "    - "
            f"{candidate.get('source')}: "
            f"{candidate.get('textLength')} "
            f"{_line(candidate.get('textPreview'))}"
        )
    print("  thought_candidates:")
    for candidate in _list_of_dicts(item.get("thoughtCandidatePreviews")):
        print(
            "    - "
            f"{candidate.get('selector')}: "
            f"{candidate.get('textLength')} "
            f"{_line(candidate.get('textPreview'))}"
        )
    print("  rejected_candidates:")
    for candidate in _list_of_dicts(item.get("rejectedCandidateReasons"))[:12]:
        print(
            "    - "
            f"{candidate.get('source')}: "
            f"{candidate.get('reason')} "
            f"{_line(candidate.get('textPreview'))}"
        )


def _list_of_dicts(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _line(value: object) -> str:
    return " ".join(str(value or "").split())


def main() -> None:
    try:
        raise SystemExit(asyncio.run(amain()))
    except KeyboardInterrupt:
        print("Остановлено пользователем.")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
