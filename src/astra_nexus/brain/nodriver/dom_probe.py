from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.evaluate import evaluate_value, unwrap_remote_value
from astra_nexus.brain.nodriver.exceptions import NoDriverPageLoadError, NoDriverProviderError
from astra_nexus.brain.nodriver.selectors import PROMPT_INPUT_SELECTORS
from astra_nexus.config.settings import Settings, load_settings
from astra_nexus.utils.logging import configure_logging

PROMPT_CANDIDATE_MARKER = "data-astra-nexus-composer-candidate"
POST_READY_PROBE_DELAY_SECONDS = 3.0

logger = logging.getLogger(__name__)


def choose_visible_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if (
            candidate.get("visible")
            and float(candidate.get("width") or 0) > 0
            and float(candidate.get("height") or 0) > 0
            and candidate.get("display") != "none"
            and candidate.get("visibility") != "hidden"
        ):
            return candidate
    return None


def normalize_candidate(candidate: Any) -> dict[str, Any]:
    candidate = unwrap_remote_value(candidate)
    if isinstance(candidate, dict):
        payload = {str(key): unwrap_remote_value(value) for key, value in candidate.items()}
        payload.setdefault("tag", str(payload.get("tagName") or "").lower())
        payload.setdefault("tag_name", payload.get("tagName") or payload.get("tag"))
        payload.setdefault("data_testid", payload.get("data-testid") or payload.get("dataTestid"))
        payload.setdefault("aria_label", payload.get("aria-label") or payload.get("ariaLabel"))
        payload.setdefault("class_name", payload.get("className"))
        payload.setdefault("selector", payload.get("selectorHint") or payload.get("selector_hint"))
        payload.setdefault("selector_hint", payload.get("selector"))
        payload.setdefault("visible", payload.get("isVisible"))
        payload.setdefault("is_visible", payload.get("visible"))
        return payload
    return {"raw": "" if candidate is None else str(candidate)}


def normalize_candidates(candidates: Any) -> list[dict[str, Any]]:
    candidates = unwrap_remote_value(candidates)
    if candidates is None:
        return []
    if not isinstance(candidates, list):
        candidates = [candidates]
    return [normalize_candidate(candidate) for candidate in candidates]


def normalize_dom_probe_payload(summary: Any) -> dict[str, Any]:
    payload = unwrap_remote_value(summary)
    if not isinstance(payload, dict):
        payload = {"raw": payload}
    else:
        payload = {str(key): unwrap_remote_value(value) for key, value in payload.items()}

    payload["candidates"] = normalize_candidates(payload.get("candidates"))
    payload["visible_candidates"] = normalize_candidates(
        _first_present(payload, "visible_candidates", "visibleCandidates")
    )
    payload["current_url"] = _first_present(payload, "current_url", "url")
    payload["page_title"] = _first_present(payload, "page_title", "title")
    payload["ready_state"] = _first_present(payload, "ready_state", "readyState")
    payload["textarea_count"] = _first_present(payload, "textarea_count", "textareaCount")
    payload["contenteditable_count"] = _first_present(
        payload,
        "contenteditable_count",
        "contenteditableCount",
    )
    payload["textbox_count"] = _first_present(payload, "textbox_count", "textboxCount")
    payload["login_button_count"] = _first_present(
        payload,
        "login_button_count",
        "loginButtonCount",
    )
    payload["login_buttons_count"] = payload["login_button_count"]
    payload["account_proof_count"] = _first_present(
        payload,
        "account_proof_count",
        "accountProofCount",
    )
    if payload["account_proof_count"] is None and "account_present" in payload:
        payload["account_proof_count"] = 1 if payload["account_present"] else 0
    payload["composer_found"] = bool(
        _first_present(payload, "composer_found", "composerFound", "composer_present")
    )
    payload["marked_selector"] = _first_present(payload, "marked_selector", "markedSelector")
    payload["login_state"] = _first_present(payload, "login_state", "loginState")
    if not payload["login_state"]:
        if payload.get("login_required"):
            payload["login_state"] = "login_required"
        elif payload.get("login_ok"):
            payload["login_state"] = "logged_in"
        else:
            payload["login_state"] = "unknown"
    if "candidate_count" not in payload:
        payload["candidate_count"] = len(payload["candidates"])
    return payload


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def is_chatgpt_composer_ready(payload: dict[str, Any]) -> bool:
    return bool(payload.get("composer_found") or payload.get("marked_selector"))


def is_login_required(payload: dict[str, Any]) -> bool:
    return (
        payload.get("login_state") == "login_required"
        or int(payload.get("login_button_count") or 0) > 0
    )


def login_state_from_probe(payload: dict[str, Any]) -> dict[str, Any]:
    login_state = str(payload.get("login_state") or "unknown")
    composer_ready = is_chatgpt_composer_ready(payload)
    login_required = is_login_required(payload)
    if composer_ready:
        reason = "composer_visible"
    elif login_required:
        reason = "login_controls_visible"
    elif login_state == "logged_in":
        reason = "account_proof_visible"
    else:
        reason = login_state
    return {
        "status": login_state,
        "login_required": login_required,
        "login_ok": login_state == "logged_in",
        "reason": reason,
        "ready_state": payload.get("ready_state"),
        "current_url": payload.get("current_url"),
        "page_title": payload.get("page_title"),
        "login_button_count": payload.get("login_button_count") or 0,
        "composer_present": composer_ready,
        "account_present": int(payload.get("account_proof_count") or 0) > 0,
    }


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
  const loginWords = /\\b(log in|login|sign in|sign up)\\b/i;
  const accountWords = /\\b(account|profile|settings|user menu|avatar)\\b/i;
  document.querySelectorAll('[' + markerAttr + ']').forEach((node) => {{
    node.removeAttribute(markerAttr);
  }});

  const seen = new Set();
  const candidates = [];

  function visibleInfo(node) {{
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    const isVisible =
      rect.width > 0 &&
      rect.height > 0 &&
      style.display !== 'none' &&
      style.visibility !== 'hidden';
    return {{
      isVisible,
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
    const tagName = (node.tagName || '').toLowerCase();
    const id = node.id || '';
    const role = node.getAttribute('role') || '';
    const dataTestid = node.getAttribute('data-testid') || '';
    return {{
      source,
      selectorHint: selector,
      tagName,
      id,
      role,
      dataTestid,
      ariaLabel: node.getAttribute('aria-label') || '',
      className: safeClassName(node),
      contenteditable: node.getAttribute('contenteditable') || '',
      lexicalEditor: node.getAttribute('data-lexical-editor') || '',
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

  function labelFor(node) {{
    return [
      node.innerText || '',
      node.value || '',
      node.getAttribute('aria-label') || '',
      node.getAttribute('data-testid') || '',
      node.getAttribute('href') || '',
      node.id || '',
      typeof node.className === 'string' ? node.className : '',
    ].join(' ');
  }}

  function isVisible(node) {{
    return visibleInfo(node).isVisible;
  }}

  const loginButtons = Array.from(
    document.querySelectorAll('a, button, input, [role="button"]')
  ).filter((node) => isVisible(node) && loginWords.test(labelFor(node)));
  const accountProofNodes = Array.from(
    document.querySelectorAll('button, a, [role="button"], [aria-label], [data-testid]')
  ).filter((node) => isVisible(node) && accountWords.test(labelFor(node)));
  const visibleCandidates = candidates
    .map((candidate) => candidate.meta)
    .filter((candidate) => candidate.isVisible);
  const chosen = candidates.find((candidate) => candidate.meta.isVisible);
  if (chosen) {{
    chosen.node.setAttribute(markerAttr, 'true');
  }}
  let loginState = 'unknown';
  if (chosen) {{
    loginState = 'logged_in';
  }} else if (loginButtons.length > 0) {{
    loginState = 'login_required';
  }} else if (accountProofNodes.length > 0) {{
    loginState = 'logged_in';
  }} else if (document.readyState !== 'complete') {{
    loginState = 'page_loading';
  }} else {{
    loginState = 'chatgpt_ui_not_ready';
  }}

  return {{
    url: window.location.href,
    title: document.title || '',
    readyState: document.readyState,
    textareaCount: document.querySelectorAll('textarea').length,
    contenteditableCount: document.querySelectorAll('[contenteditable="true"]').length,
    textboxCount: document.querySelectorAll('[role="textbox"]').length,
    loginButtonCount: loginButtons.length,
    accountProofCount: accountProofNodes.length,
    candidate_count: candidates.length,
    composerFound: Boolean(chosen),
    loginState,
    candidates: candidates.map((candidate) => candidate.meta).slice(0, 25),
    visibleCandidates: visibleCandidates.slice(0, 10),
    markedSelector: chosen ? '[' + markerAttr + '="true"]' : null,
  }};
}})()
"""


LOGIN_STATE_PROBE_SCRIPT = "/* LOGIN_STATE_PROBE */\n" + build_prompt_candidate_probe_script()


async def evaluate_script(tab: Any, script: str) -> Any:
    return await evaluate_value(tab, script)


async def read_ready_state(tab: Any) -> str:
    try:
        value = await evaluate_script(tab, "document.readyState")
    except Exception:
        return "unknown"
    return str(value or "unknown")


async def wait_for_page_ready_state_complete(tab: Any, timeout_seconds: float) -> str:
    logger.info(
        "page.ready_state.wait.started",
        extra={"stage": "page.ready_state.wait.started"},
    )
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_ready_state = "unknown"

    while asyncio.get_running_loop().time() <= deadline:
        last_ready_state = await read_ready_state(tab)
        if last_ready_state == "complete":
            logger.info(
                "page.ready_state.complete",
                extra={
                    "stage": "page.ready_state.complete",
                    "ready_state": last_ready_state,
                },
            )
            return last_ready_state
        await asyncio.sleep(0.5)

    raise NoDriverPageLoadError(
        "ChatGPT Web не дошёл до document.readyState === 'complete' за отведённое время.",
        stage="page.ready_state.wait.started",
        details={"ready_state": last_ready_state},
    )


async def collect_dom_probe(session: BrowserSession) -> dict[str, Any]:
    tab = await session.ensure_chatgpt_page()
    await wait_for_page_ready_state_complete(
        tab,
        session.settings.nodriver_page_load_timeout_seconds,
    )
    await asyncio.sleep(POST_READY_PROBE_DELAY_SECONDS)
    logger.info("dom.probe.started", extra={"stage": "dom.probe.started"})
    summary = await evaluate_script(tab, build_prompt_candidate_probe_script())
    payload = normalize_dom_probe_payload(summary)
    payload["current_url"] = await session.current_url()
    payload["page_title"] = await session.current_title()
    logger.info(
        "dom.probe.finished",
        extra={
            "stage": "dom.probe.finished",
            "candidate_count": payload.get("candidate_count"),
        },
    )
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
    payload: dict[str, Any] | None = None
    report_path: Path | None = None
    error: Exception | None = None
    interrupted = False
    try:
        payload = await collect_dom_probe(session)
        report_path = write_dom_probe_report(settings, payload)
    except (KeyboardInterrupt, asyncio.CancelledError):
        interrupted = True
        error = KeyboardInterrupt()
        print("Остановлено пользователем.")
    except NoDriverProviderError as exc:
        error = exc
        print(f"status: {exc.error_code}")
        print(f"stage: {exc.stage or 'unknown'}")
        print(f"message: {exc}")
        print(f"action: {exc.action}")
    except Exception as exc:
        error = exc
        print("status: dom_probe_failed")
        print("stage: dom.probe.started")
        print(f"message: {exc}")
        print("action: проверь страницу ChatGPT и повтори astra-nexus-nodriver-dom-probe")
    finally:
        if error is not None and not interrupted and settings.nodriver_keep_browser_open_on_error:
            await asyncio.to_thread(
                input,
                "Браузер оставлен открытым. Проверь страницу и нажми Enter для закрытия.",
            )
        await session.stop()

    if interrupted:
        return 130
    if error is not None:
        return 1
    if payload is None or report_path is None:
        print("status: dom_probe_failed")
        print("stage: dom.probe.finished")
        print("message: DOM probe не вернул результат.")
        return 1

    print("Astra Nexus NoDriver DOM probe")
    print(f"current_url: {payload.get('current_url')}")
    print(f"page_title: {payload.get('page_title')}")
    print(f"ready_state: {payload.get('ready_state')}")
    print(f"textarea_count: {payload.get('textarea_count')}")
    print(f"contenteditable_count: {payload.get('contenteditable_count')}")
    print(f"textbox_count: {payload.get('textbox_count')}")
    print(f"login_buttons_count: {payload.get('login_buttons_count')}")
    print(f"candidate_count: {payload.get('candidate_count')}")
    print(f"login_state: {payload.get('login_state')}")
    for candidate in normalize_candidates(payload.get("candidates", [])):
        print(
            "candidate: "
            f"selector={candidate.get('selector')} "
            f"tag={candidate.get('tag')} "
            f"id={candidate.get('id')} "
            f"role={candidate.get('role')} "
            f"data-testid={candidate.get('data_testid')} "
            f"class={candidate.get('class_name')} "
            f"raw={candidate.get('raw')}"
        )
    print(f"report: {report_path}")
    return 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(run()))
    except KeyboardInterrupt:
        print("Остановлено пользователем.")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
