from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.exceptions import NoDriverProviderError
from astra_nexus.brain.nodriver.selectors import PROMPT_INPUT_SELECTORS
from astra_nexus.config.settings import Settings, load_settings
from astra_nexus.utils.logging import configure_logging

PROMPT_CANDIDATE_MARKER = "data-astra-nexus-composer-candidate"


def choose_visible_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    for candidate in candidates:
        if (
            candidate.get("visible")
            and float(candidate.get("width") or 0) > 0
            and float(candidate.get("height") or 0) > 0
            and candidate.get("display") != "none"
            and candidate.get("visibility") != "hidden"
        ):
            return candidate
    return None


def build_prompt_candidate_probe_script(selectors: list[str] | None = None) -> str:
    selectors = selectors or PROMPT_INPUT_SELECTORS
    selectors_json = json.dumps(selectors, ensure_ascii=False)
    marker_json = json.dumps(PROMPT_CANDIDATE_MARKER)
    return f"""
/* PROMPT_CANDIDATE_PROBE */
(() => {{
  const markerAttr = {marker_json};
  const selectors = {selectors_json};
  const words = ['prompt', 'composer', 'textarea', 'input', 'editor'];
  document.querySelectorAll('[' + markerAttr + ']').forEach((node) => {{
    node.removeAttribute(markerAttr);
  }});

  const seen = new Set();
  const candidates = [];

  function visibleInfo(node) {{
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    const visible =
      rect.width > 0 &&
      rect.height > 0 &&
      style.display !== 'none' &&
      style.visibility !== 'hidden';
    return {{
      visible,
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      display: style.display,
      visibility: style.visibility,
    }};
  }}

  function safeClassName(node) {{
    const className = typeof node.className === 'string' ? node.className : '';
    return className.slice(0, 160);
  }}

  function describe(node, source, selector) {{
    const info = visibleInfo(node);
    const tag = (node.tagName || '').toLowerCase();
    const id = node.id || '';
    const role = node.getAttribute('role') || '';
    const dataTestid = node.getAttribute('data-testid') || '';
    return {{
      source,
      selector,
      tag,
      id,
      role,
      data_testid: dataTestid,
      class_name: safeClassName(node),
      contenteditable: node.getAttribute('contenteditable') || '',
      lexical_editor: node.getAttribute('data-lexical-editor') || '',
      ...info,
    }};
  }}

  function add(node, source, selector) {{
    if (!node || seen.has(node)) {{
      return;
    }}
    seen.add(node);
    candidates.push({{ node, meta: describe(node, source, selector) }});
  }}

  for (const selector of selectors) {{
    try {{
      document.querySelectorAll(selector).forEach((node) => add(node, 'selector', selector));
    }} catch (_error) {{}}
  }}

  document.querySelectorAll('textarea').forEach((node) => add(node, 'textarea', 'textarea'));
  document
    .querySelectorAll('[contenteditable="true"]')
    .forEach((node) => add(node, 'contenteditable', '[contenteditable="true"]'));
  document
    .querySelectorAll('[role="textbox"]')
    .forEach((node) => add(node, 'textbox', '[role="textbox"]'));

  document.querySelectorAll('[id], [class], [data-testid]').forEach((node) => {{
    const haystack = [
      node.id || '',
      typeof node.className === 'string' ? node.className : '',
      node.getAttribute('data-testid') || '',
    ].join(' ').toLowerCase();
    if (words.some((word) => haystack.includes(word))) {{
      add(node, 'keyword', 'id/class/data-testid');
    }}
  }});

  const visibleCandidates = candidates
    .map((candidate) => candidate.meta)
    .filter((candidate) => candidate.visible);
  const chosen = candidates.find((candidate) => candidate.meta.visible);
  if (chosen) {{
    chosen.node.setAttribute(markerAttr, 'true');
  }}

  return {{
    ready_state: document.readyState,
    textarea_count: document.querySelectorAll('textarea').length,
    contenteditable_count: document.querySelectorAll('[contenteditable="true"]').length,
    textbox_count: document.querySelectorAll('[role="textbox"]').length,
    candidate_count: candidates.length,
    candidates: candidates.map((candidate) => candidate.meta).slice(0, 25),
    visible_candidates: visibleCandidates.slice(0, 10),
    marked_selector: chosen ? '[' + markerAttr + '="true"]' : null,
  }};
}})()
"""


LOGIN_STATE_PROBE_SCRIPT = """
/* LOGIN_STATE_PROBE */
(() => {
  const loginWords = /\\b(log in|login|sign in|sign up)\\b/i;
  const visible = (node) => {
    if (!node) {
      return false;
    }
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return (
      rect.width > 0 &&
      rect.height > 0 &&
      style.display !== 'none' &&
      style.visibility !== 'hidden'
    );
  };
  const loginNodes = Array.from(document.querySelectorAll('a, button, input')).filter((node) => {
    const label = [
      node.innerText || '',
      node.value || '',
      node.getAttribute('aria-label') || '',
      node.getAttribute('data-testid') || '',
      node.getAttribute('href') || '',
    ].join(' ');
    return visible(node) && loginWords.test(label);
  });
  const composer = Array.from(
    document.querySelectorAll(
      '#prompt-textarea, textarea, [contenteditable="true"], [role="textbox"]'
    )
  ).find(visible);
  const accountNode = Array.from(
    document.querySelectorAll(
      [
        '[data-testid*="profile"]',
        '[data-testid*="account"]',
        'nav',
        'aside',
        'button[aria-label*="Account"]',
      ].join(', ')
    )
  ).find(visible);
  if (loginNodes.length > 0) {
    return {
      login_required: true,
      login_ok: false,
      reason: 'login_controls_visible',
      login_button_count: loginNodes.length,
      composer_present: Boolean(composer),
      account_present: Boolean(accountNode),
    };
  }
  if (composer) {
    return {
      login_required: false,
      login_ok: true,
      reason: 'composer_visible',
      login_button_count: 0,
      composer_present: true,
      account_present: Boolean(accountNode),
    };
  }
  if (accountNode) {
    return {
      login_required: false,
      login_ok: true,
      reason: 'account_or_nav_visible',
      login_button_count: 0,
      composer_present: false,
      account_present: true,
    };
  }
  return {
    login_required: false,
    login_ok: false,
    reason: 'unknown',
    login_button_count: 0,
    composer_present: false,
    account_present: false,
  };
})()
"""


async def collect_dom_probe(session: BrowserSession) -> dict[str, Any]:
    tab = await session.ensure_chatgpt_page()
    summary = await tab.evaluate(build_prompt_candidate_probe_script())
    payload = dict(summary or {})
    payload["current_url"] = await session.current_url()
    payload["page_title"] = await session.current_title()
    return payload


def write_dom_probe_report(settings: Settings, payload: dict[str, Any]) -> Path:
    path = Path(settings.data_dir).expanduser().resolve() / "debug" / "nodriver" / "dom_probe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path


async def run() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    session = BrowserSession(settings, lifecycle_context="dom_probe")
    try:
        payload = await collect_dom_probe(session)
        report_path = write_dom_probe_report(settings, payload)
    except NoDriverProviderError as exc:
        print(f"status: {exc.error_code}")
        print(f"stage: {exc.stage or 'unknown'}")
        print(f"message: {exc}")
        print(f"action: {exc.action}")
        return 1
    finally:
        await session.stop()

    print("Astra Nexus NoDriver DOM probe")
    print(f"current_url: {payload.get('current_url')}")
    print(f"page_title: {payload.get('page_title')}")
    print(f"ready_state: {payload.get('ready_state')}")
    print(f"textarea_count: {payload.get('textarea_count')}")
    print(f"contenteditable_count: {payload.get('contenteditable_count')}")
    print(f"textbox_count: {payload.get('textbox_count')}")
    print(f"candidate_count: {payload.get('candidate_count')}")
    for candidate in payload.get("candidates", []):
        print(
            "candidate: "
            f"selector={candidate.get('selector')} "
            f"tag={candidate.get('tag')} "
            f"id={candidate.get('id')} "
            f"role={candidate.get('role')} "
            f"data-testid={candidate.get('data_testid')} "
            f"class={candidate.get('class_name')}"
        )
    print(f"report: {report_path}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
