from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.dom_probe import (
    LOGIN_STATE_PROBE_SCRIPT,
    build_prompt_candidate_probe_script,
    evaluate_script,
    login_state_from_probe,
    normalize_dom_probe_payload,
)
from astra_nexus.brain.nodriver.exceptions import (
    NoDriverChatGPTUINotReadyError,
    NoDriverLoginRequiredError,
    NoDriverPreferredModelError,
    NoDriverPromptBoxNotFoundError,
    NoDriverPromptInsertFailedError,
    NoDriverProviderError,
    NoDriverSelectorNotFoundError,
    NoDriverTimeoutError,
)
from astra_nexus.brain.nodriver.response_parser import parse_last_assistant_response
from astra_nexus.brain.nodriver.selectors import (
    ASSISTANT_MESSAGE_QUERY,
    PROMPT_INPUT_SELECTORS,
    SEND_BUTTON_SELECTORS,
    STOP_BUTTON_SELECTORS,
)
from astra_nexus.config.settings import Settings

logger = logging.getLogger(__name__)


class ResponseWaitState(StrEnum):
    PROMPT_SUBMITTED = "prompt_submitted"
    GENERATION_STARTED = "generation_started"
    ASSISTANT_SEGMENT_SEEN = "assistant_segment_seen"
    THINKING_OR_STREAMING = "thinking_or_streaming"
    INTERMEDIATE_RESPONSE_SEEN = "intermediate_response_seen"
    WAITING_FOR_FINAL_IDLE = "waiting_for_final_idle"
    FINAL_RESPONSE_READY = "final_response_ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ResponseTurnBaseline:
    assistant_count_before: int
    user_count_before: int = 0
    last_user_message_id: str | None = None
    last_user_message_index: int | None = None

    @classmethod
    def from_snapshot(cls, snapshot: ResponseWaitSnapshot) -> ResponseTurnBaseline:
        return cls(
            assistant_count_before=len(snapshot.assistant_messages),
            user_count_before=snapshot.user_messages_count,
            last_user_message_id=snapshot.last_user_message_id,
            last_user_message_index=snapshot.last_user_message_index,
        )


@dataclass(frozen=True)
class ResponseWaitSnapshot:
    assistant_messages: list[str]
    is_generating: bool
    stop_button_visible: bool
    prompt_available: bool
    send_button_idle: bool
    visible_indicators: list[str] = field(default_factory=list)
    continue_required: bool = False
    detected_model: str | None = None
    detected_reasoning_mode: str | None = None
    assistant_message_ids: list[str] = field(default_factory=list)
    assistant_message_indexes: list[int] = field(default_factory=list)
    user_messages_count: int = 0
    last_user_message_id: str | None = None
    last_user_message_index: int | None = None
    current_turn_id: str | None = None
    stop_button_count: int = 0
    send_button_state: str = "unknown"
    composer_disabled: bool = False
    composer_editable: bool = False
    aria_busy: bool = False
    streaming_indicators_count: int = 0
    thinking_indicators_count: int = 0

    @property
    def latest_assistant_text(self) -> str:
        return self.assistant_messages[-1] if self.assistant_messages else ""

    @property
    def latest_assistant_text_chars(self) -> int:
        return len(self.latest_assistant_text)

    @property
    def latest_assistant_text_preview(self) -> str:
        return _compact_preview(self.latest_assistant_text, limit=180)

    @property
    def final_idle(self) -> bool:
        return (
            not self.is_generating
            and not self.stop_button_visible
            and self.prompt_available
            and self.send_button_idle
            and not self.visible_indicators
            and not self.continue_required
        )


@dataclass(frozen=True)
class ResponseWaitResult:
    final_answer: str
    assistant_segments: list[str]
    response_count_before: int
    response_count_after: int
    final_segment_index: int
    wait_state_timeline: list[dict[str, Any]]
    final_idle_detected: bool
    detected_model: str | None = None
    detected_reasoning_mode: str | None = None

    @property
    def debug_payload(self) -> dict[str, Any]:
        return {
            "response_count_before": self.response_count_before,
            "response_count_after": self.response_count_after,
            "assistant_segments_count": len(self.assistant_segments),
            "assistant_segments_lengths": [len(segment) for segment in self.assistant_segments],
            "final_segment_index": self.final_segment_index,
            "wait_state_timeline": self.wait_state_timeline,
            "final_idle_detected": self.final_idle_detected,
            "detected_model": self.detected_model,
            "detected_reasoning_mode": self.detected_reasoning_mode,
        }


class ChatGPTClient:
    def __init__(self, settings: Settings, session: BrowserSession | None = None) -> None:
        self.settings = settings
        self.session = session or BrowserSession(settings)

    async def ask(
        self,
        prompt: str,
        *,
        debug_context: dict[str, Any] | None = None,
    ) -> str:
        debug_context = debug_context or {}
        try:
            return await self._ask(prompt, debug_context)
        except asyncio.CancelledError:
            self._log_stage("chatgpt.cancelled", debug_context)
            raise
        except NoDriverProviderError as exc:
            await self._enrich_error(exc)
            self._log_stage(
                "chatgpt.error",
                debug_context,
                error_code=exc.error_code,
                error_message=str(exc),
                url=exc.url,
            )
            raise

    async def _ask(self, prompt: str, debug_context: dict[str, Any]) -> str:
        self._log_stage("browser.session.ensure_started", debug_context)
        await self.session.start()

        self._log_stage("chatgpt.page.open", debug_context)
        tab = await self.session.ensure_chatgpt_page()
        self._log_stage("chatgpt.page.loaded", debug_context, url=await self.session.current_url())

        self._log_stage("chatgpt.login.check.started", debug_context)
        login_state = await self._login_state(tab)
        if login_state.get("login_required"):
            raise NoDriverLoginRequiredError(
                stage="chatgpt.login.check.started",
                url=await self.session.current_url(),
                page_title=await self.session.current_title(),
                details=await self._page_diagnostics(tab, login_state=login_state),
            )
        if login_state.get("login_ok"):
            self._log_stage(
                "chatgpt.login.check.ok",
                debug_context,
                reason=login_state.get("reason"),
            )
        else:
            self._log_stage(
                "chatgpt.login.check.unknown",
                debug_context,
                reason=login_state.get("reason"),
            )

        turn_baseline = ResponseTurnBaseline.from_snapshot(
            await self._safe_response_wait_snapshot(tab)
        )
        before_count = turn_baseline.assistant_count_before
        await self._ensure_preferred_model(tab, debug_context)
        self._log_stage("chatgpt.prompt_box.search.started", debug_context)
        await self._wait_for_prompt_box(tab, debug_context, login_state)

        self._log_stage("chatgpt.prompt.insert.started", debug_context)
        await self._fill_prompt(tab, prompt)
        self._log_stage("chatgpt.prompt.insert.ok", debug_context)

        self._log_stage("chatgpt.send.started", debug_context)
        send_button = await self._first_selector(
            tab,
            SEND_BUTTON_SELECTORS,
            stage="chatgpt.send.started",
            kind="send_button",
        )
        await send_button.click()
        self._log_stage("chatgpt.send.ok", debug_context)

        self._log_stage("chatgpt.response.wait.started", debug_context)
        wait_result = await self._wait_for_response_completion(
            tab,
            response_count_before=before_count,
            turn_baseline=turn_baseline,
            debug_context=debug_context,
        )
        self._log_stage("chatgpt.response.wait.ok", debug_context)

        self._log_stage("chatgpt.response.parse.started", debug_context)
        response = wait_result.final_answer
        if not response.strip():
            try:
                response = parse_last_assistant_response(wait_result.assistant_segments)
            except NoDriverSelectorNotFoundError as exc:
                exc.stage = exc.stage or "chatgpt.response.parse.started"
                raise
        self._log_stage("chatgpt.response.parse.ok", debug_context)
        return response

    async def _login_state(self, tab: Any) -> dict[str, Any]:
        try:
            result = await evaluate_script(tab, LOGIN_STATE_PROBE_SCRIPT)
        except Exception:
            return {"login_required": False, "login_ok": False, "reason": "probe_failed"}
        return login_state_from_probe(normalize_dom_probe_payload(result))

    async def _assistant_messages(self, tab: Any) -> list[str]:
        result = await evaluate_script(tab, ASSISTANT_MESSAGE_QUERY)
        return list(result or [])

    async def _first_selector(
        self,
        tab: Any,
        selectors: list[str],
        *,
        stage: str,
        kind: str,
    ) -> Any:
        for selector in selectors:
            try:
                element = await tab.query_selector(selector)
            except Exception:
                element = None
            if element is not None:
                return element

        message = f"Не найден ни один selector: {', '.join(selectors)}"
        kwargs = {
            "stage": stage,
            "url": await self.session.current_url(),
            "page_title": await self.session.current_title(),
            "selector": ", ".join(selectors),
            "details": await self._page_diagnostics(tab),
        }
        if kind == "prompt_box":
            raise NoDriverPromptBoxNotFoundError(
                "Поле ввода ChatGPT не найдено.",
                **kwargs,
            )
        raise NoDriverSelectorNotFoundError(message, **kwargs)

    async def _wait_for_prompt_box(
        self,
        tab: Any,
        debug_context: dict[str, Any],
        login_state: dict[str, Any],
    ) -> Any:
        self._log_stage("chatgpt.ui.wait.started", debug_context)
        deadline = (
            asyncio.get_running_loop().time() + self.settings.nodriver_page_load_timeout_seconds
        )
        attempts = 0
        last_summary: dict[str, Any] = {}
        last_login_state = login_state

        while asyncio.get_running_loop().time() <= deadline:
            attempts += 1
            ready_state = await self._ready_state(tab)
            self._log_stage(
                "chatgpt.ui.wait.ready_state",
                debug_context,
                ready_state=ready_state,
                attempts=attempts,
            )
            if ready_state != "complete":
                await self._sleep_until_next_attempt(deadline)
                continue

            last_login_state = await self._login_state(tab)
            if last_login_state.get("login_required"):
                raise NoDriverLoginRequiredError(
                    stage="chatgpt.login.check.started",
                    url=await self.session.current_url(),
                    page_title=await self.session.current_title(),
                    details=await self._page_diagnostics(tab, login_state=last_login_state),
                )

            last_summary = await self._prompt_candidate_summary(tab)
            marked_selector = last_summary.get("marked_selector")
            if marked_selector:
                element = await tab.query_selector(str(marked_selector))
                if element is not None:
                    self._log_stage(
                        "chatgpt.prompt_box.found",
                        debug_context,
                        attempts=attempts,
                        candidate_count=last_summary.get("candidate_count"),
                    )
                    return element

            self._log_stage(
                "chatgpt.prompt_box.search.retry",
                debug_context,
                attempts=attempts,
                ready_state=ready_state,
                candidate_count=last_summary.get("candidate_count", 0),
            )
            await self._sleep_until_next_attempt(deadline)

        details = {
            **last_summary,
            "selectors_tried": PROMPT_INPUT_SELECTORS,
            "login_state": last_login_state,
            "attempts": attempts,
        }
        raise NoDriverChatGPTUINotReadyError(
            "Интерфейс ChatGPT Web не готов: composer не найден, login controls не видны.",
            stage="chatgpt.prompt_box.search.started",
            url=await self.session.current_url(),
            page_title=await self.session.current_title(),
            selector=", ".join(PROMPT_INPUT_SELECTORS),
            details=details,
        )

    async def _ready_state(self, tab: Any) -> str:
        try:
            value = await evaluate_script(tab, "document.readyState")
        except Exception:
            return "unknown"
        return str(value or "unknown")

    async def _prompt_candidate_summary(self, tab: Any) -> dict[str, Any]:
        try:
            result = await evaluate_script(tab, build_prompt_candidate_probe_script())
        except Exception:
            return {
                "ready_state": "unknown",
                "textarea_count": 0,
                "contenteditable_count": 0,
                "textbox_count": 0,
                "candidate_count": 0,
                "visible_candidates": [],
                "marked_selector": None,
            }
        return normalize_dom_probe_payload(result)

    async def _sleep_until_next_attempt(self, deadline: float) -> None:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return
        await asyncio.sleep(min(0.5, remaining))

    async def _fill_prompt(self, tab: Any, prompt: str) -> dict[str, Any]:
        failed_attempts: list[dict[str, Any]] = []
        details: dict[str, Any] = {}
        for attempt_number in range(1, 4):
            details = await self._insert_prompt_with_js(tab, prompt)
            details["prompt_insert_attempt_number"] = attempt_number
            if details.get("ok"):
                return details
            failed_attempts.append(details)
            if attempt_number < 3:
                await asyncio.sleep(0.5)
        details = next(
            (
                attempt
                for attempt in reversed(failed_attempts)
                if attempt.get("error")
                not in {"prompt_insert_result_not_object", "prompt_insert_result_missing_ok"}
            ),
            failed_attempts[-1] if failed_attempts else details,
        )
        details = await self._prompt_insert_failure_details(tab, details)
        details["prompt_insert_failed_attempts"] = failed_attempts
        raise NoDriverPromptInsertFailedError(
            "Не удалось вставить prompt в поле ввода ChatGPT.",
            stage="chatgpt.prompt.insert.started",
            url=await self.session.current_url(),
            page_title=await self.session.current_title(),
            selector=", ".join(PROMPT_INPUT_SELECTORS),
            details=details,
        )

    async def _prompt_insert_failure_details(
        self,
        tab: Any,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        enriched = dict(details)
        enriched.setdefault("selector", ", ".join(PROMPT_INPUT_SELECTORS))
        enriched["url"] = await self.session.current_url()
        enriched["page_title"] = await self.session.current_title()
        if "dom_probe_summary" not in enriched:
            enriched["dom_probe_summary"] = await self._page_diagnostics(tab)
        return enriched

    async def _insert_prompt_with_js(self, tab: Any, prompt: str) -> dict[str, Any]:
        script = self._build_prompt_insert_script(prompt)
        try:
            result = await evaluate_script(tab, script)
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "exception_type": type(exc).__name__,
                "selector": ", ".join(PROMPT_INPUT_SELECTORS),
            }
        if not isinstance(result, dict):
            return {
                "ok": False,
                "error": "prompt_insert_result_not_object",
                "raw_result": result,
                "selector": ", ".join(PROMPT_INPUT_SELECTORS),
            }
        if "ok" not in result:
            return {
                "ok": False,
                "error": "prompt_insert_result_missing_ok",
                "raw_result": result,
                "selector": ", ".join(PROMPT_INPUT_SELECTORS),
            }
        return result

    def _build_prompt_insert_script(self, prompt: str) -> str:
        prompt_json = json.dumps(prompt, ensure_ascii=False)
        selectors_json = json.dumps(PROMPT_INPUT_SELECTORS, ensure_ascii=False)
        return f"""
/* PROMPT_INSERT */
(() => {{
  const prompt = {prompt_json};
  const selectors = {selectors_json};

  function visible(node) {{
    if (!node) {{
      return false;
    }}
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return (
      rect.width > 0 &&
      rect.height > 0 &&
      style.display !== 'none' &&
      style.visibility !== 'hidden'
    );
  }}

  function redactedSample(value, limit = 2000) {{
    return String(value || '')
      .replace(/sk-[A-Za-z0-9_-]{{12,}}/g, '[redacted-openai-key]')
      .replace(/Bearer\\s+[A-Za-z0-9._-]+/gi, 'Bearer [redacted]')
      .replace(/[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+/g, '[redacted-jwt]')
      .slice(0, limit);
  }}

  function textOf(node) {{
    if (!node) {{
      return '';
    }}
    if ('value' in node) {{
      return node.value || '';
    }}
    return node.innerText || node.textContent || '';
  }}

  function normalizeText(value) {{
    return String(value || '')
      .replace(/\\r\\n/g, '\\n')
      .replace(/\\u00a0/g, ' ')
      .replace(/\\u200b/g, '')
      .replace(/[ \\t]+\\n/g, '\\n')
      .replace(/\\n[ \\t]+/g, '\\n')
      .replace(/[ \\t]{{2,}}/g, ' ')
      .trim();
  }}

  function linesInOrder(visibleText, expectedText) {{
    const visibleNormalized = normalizeText(visibleText);
    const expectedLines = normalizeText(expectedText)
      .split('\\n')
      .map((line) => line.trim())
      .filter(Boolean);
    if (expectedLines.length === 0) {{
      return visibleNormalized.length === 0;
    }}
    let cursor = 0;
    for (const line of expectedLines) {{
      const index = visibleNormalized.indexOf(line, cursor);
      if (index < 0) {{
        return false;
      }}
      cursor = index + line.length;
    }}
    return true;
  }}

  function matchInsertedText(visibleText) {{
    const normalizedVisible = normalizeText(visibleText);
    const normalizedPrompt = normalizeText(prompt);
    if (normalizedVisible === normalizedPrompt) {{
      return {{
        ok: true,
        reason: 'text_matches_exact_normalized',
        normalizedVisible,
        normalizedPrompt,
      }};
    }}
    if (linesInOrder(visibleText, prompt)) {{
      return {{
        ok: true,
        reason: 'text_matches_after_dom_normalization',
        normalizedVisible,
        normalizedPrompt,
      }};
    }}
    return {{
      ok: false,
      reason: 'text_not_visible_after_insert',
      normalizedVisible,
      normalizedPrompt,
    }};
  }}

  function describeElement(node) {{
    if (!node) {{
      return null;
    }}
    const rect = node.getBoundingClientRect ? node.getBoundingClientRect() : null;
    return {{
      tagName: (node.tagName || '').toLowerCase(),
      id: node.id || '',
      role: node.getAttribute ? node.getAttribute('role') || '' : '',
      dataTestid: node.getAttribute ? node.getAttribute('data-testid') || '' : '',
      className: typeof node.className === 'string' ? node.className.slice(0, 160) : '',
      isContentEditable: Boolean(node.isContentEditable),
      width: rect ? Math.round(rect.width) : 0,
      height: rect ? Math.round(rect.height) : 0,
    }};
  }}

  function describeActiveElement() {{
    return describeElement(document.activeElement);
  }}

  function outerHTMLSample(node) {{
    return redactedSample(node && node.outerHTML ? node.outerHTML : '', 2000);
  }}

  function dispatchBeforeInput(node, inputType) {{
    try {{
      node.dispatchEvent(
        new InputEvent('beforeinput', {{
          bubbles: true,
          cancelable: true,
          inputType,
          data: prompt,
        }})
      );
    }} catch (_error) {{}}
  }}

  function dispatchTextEvents(node) {{
    try {{
      node.dispatchEvent(
        new InputEvent('input', {{
          bubbles: true,
          cancelable: true,
          inputType: 'insertText',
          data: prompt,
        }})
      );
    }} catch (_error) {{
      node.dispatchEvent(new Event('input', {{ bubbles: true }}));
    }}
    node.dispatchEvent(new Event('change', {{ bubbles: true }}));
  }}

  function dispatchKeyboardPasteHint(node) {{
    try {{
      node.dispatchEvent(
        new KeyboardEvent('keydown', {{
          bubbles: true,
          cancelable: true,
          key: 'v',
          code: 'KeyV',
          metaKey: true,
        }})
      );
      node.dispatchEvent(
        new KeyboardEvent('keyup', {{
          bubbles: true,
          cancelable: true,
          key: 'v',
          code: 'KeyV',
          metaKey: true,
        }})
      );
    }} catch (_error) {{}}
  }}

  function clearEditable(node) {{
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(node);
    if (selection) {{
      selection.removeAllRanges();
      selection.addRange(range);
    }}
    const deleted = document.execCommand('delete', false, null);
    if (!deleted && textOf(node)) {{
      node.textContent = '';
    }}
  }}

  function snapshot(node, method, extra = {{}}) {{
    const visibleText = textOf(node);
    const match = matchInsertedText(visibleText);
    return {{
      method,
      ok: match.ok,
      reason: match.reason,
      textLength: visibleText.length,
      visibleText: redactedSample(visibleText, 2000),
      normalizedVisible: redactedSample(match.normalizedVisible, 2000),
      expectedLength: prompt.length,
      ...extra,
    }};
  }}

  function candidateRank(node) {{
    const tagName = (node.tagName || '').toLowerCase();
    const role = node.getAttribute('role') || '';
    const className = String(node.className || '').toLowerCase();
    const name = String(node.getAttribute('name') || '').toLowerCase();
    if (node.isContentEditable) {{
      return 0;
    }}
    if (role === 'textbox' && tagName !== 'textarea') {{
      return 1;
    }}
    if (tagName === 'textarea' && !className.includes('fallback') && !name.includes('fallback')) {{
      return 2;
    }}
    if (tagName === 'input') {{
      return 3;
    }}
    if (role === 'textbox') {{
      return 4;
    }}
    return className.includes('fallback') || name.includes('fallback') ? 50 : 5;
  }}

  const activeElement = document.activeElement
    ? [{{ selector: 'document.activeElement', node: document.activeElement }}]
    : [];
  const selectorCandidates = selectors
    .flatMap((selector) => {{
      try {{
        return Array.from(document.querySelectorAll(selector))
          .map((node) => {{ return {{ selector, node }}; }});
      }} catch (_error) {{
        return [];
      }}
    }});
  const found = activeElement
    .concat(selectorCandidates)
    .filter((entry) => entry && entry.node && visible(entry.node))
    .sort((left, right) => candidateRank(left.node) - candidateRank(right.node))[0];
  const target = found ? found.node : null;
  const matchedSelector = found ? found.selector : selectors.join(', ');

  if (!target) {{
    return {{
      ok: false,
      error: 'prompt_element_not_found',
      selector: selectors.join(', '),
      activeElement: describeActiveElement(),
    }};
  }}

  target.focus();
  const tagName = (target.tagName || '').toLowerCase();
  const role = target.getAttribute('role') || '';
  const isTextInput =
    tagName === 'textarea' ||
    (tagName === 'input' && ['text', 'search', ''].includes(target.type || ''));
  const isContentEditable = Boolean(target.isContentEditable);
  const attempts = [];

  if (isTextInput) {{
    const prototype = tagName === 'textarea'
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
    if (descriptor && descriptor.set) {{
      descriptor.set.call(target, prompt);
    }} else {{
      target.value = prompt;
    }}
    dispatchBeforeInput(target, 'insertText');
    dispatchTextEvents(target);
    attempts.push(snapshot(target, 'native_value_setter'));
  }} else if (isContentEditable) {{
    clearEditable(target);
    dispatchBeforeInput(target, 'insertText');
    const inserted = document.execCommand('insertText', false, prompt);
    dispatchTextEvents(target);
    attempts.push(snapshot(target, 'exec_command_insert_text', {{ inserted }}));

    if (!attempts[attempts.length - 1].ok) {{
      clearEditable(target);
      dispatchKeyboardPasteHint(target);
      let dispatched = false;
      let pasteError = '';
      try {{
        const data = new DataTransfer();
        data.setData('text/plain', prompt);
        const event = new ClipboardEvent('paste', {{
          bubbles: true,
          cancelable: true,
          clipboardData: data,
        }});
        dispatched = target.dispatchEvent(event);
      }} catch (error) {{
        pasteError = error && error.message ? error.message : String(error);
      }}
      dispatchTextEvents(target);
      attempts.push(snapshot(target, 'synthetic_clipboard_paste', {{ dispatched, pasteError }}));
    }}

    if (!attempts[attempts.length - 1].ok) {{
      clearEditable(target);
      dispatchBeforeInput(target, 'insertText');
      target.textContent = prompt;
      dispatchTextEvents(target);
      attempts.push(snapshot(target, 'text_content_input_events'));
    }}
    target.focus();
  }} else {{
    return {{
      ok: false,
      error: 'prompt_element_not_editable',
      tagName,
      id: target.id || '',
      role,
      isContentEditable,
      selector: matchedSelector,
      activeElement: describeActiveElement(),
      outerHTML: outerHTMLSample(target),
    }};
  }}

  const visibleText = textOf(target);
  const match = matchInsertedText(visibleText);
  const bestAttempt = attempts.find((attempt) => attempt.ok) || attempts[attempts.length - 1];
  return {{
    ok: match.ok,
    error: match.ok ? null : match.reason,
    method: bestAttempt ? bestAttempt.method : null,
    attempts,
    textLength: visibleText.length,
    visibleText: redactedSample(visibleText, 2000),
    normalizedVisible: redactedSample(match.normalizedVisible, 2000),
    expectedLength: prompt.length,
    tagName,
    id: target.id || '',
    role,
    isContentEditable,
    selector: matchedSelector,
    activeElement: describeActiveElement(),
    outerHTML: outerHTMLSample(target),
  }};
}})()
"""

    async def _wait_for_response_completion(
        self,
        tab: Any,
        *,
        response_count_before: int,
        turn_baseline: ResponseTurnBaseline | None = None,
        debug_context: dict[str, Any],
    ) -> ResponseWaitResult:
        turn_baseline = turn_baseline or ResponseTurnBaseline(
            assistant_count_before=response_count_before
        )
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        hard_timeout = float(self.settings.nodriver_response_timeout_seconds)
        idle_confirm_seconds = max(
            0.0,
            float(self.settings.nodriver_response_idle_confirm_seconds),
        )
        progress_log_interval = max(
            0.0,
            float(self.settings.nodriver_response_progress_log_interval_seconds),
        )
        max_empty_wait = self.settings.nodriver_response_max_empty_wait_seconds
        max_empty_wait = None if max_empty_wait is None else float(max_empty_wait)
        idle_started_at: float | None = None
        next_progress_log_at = started_at + progress_log_interval
        timeline: list[dict[str, Any]] = []

        async def record(
            state: ResponseWaitState,
            snapshot: ResponseWaitSnapshot,
            *,
            reason: str | None = None,
        ) -> None:
            elapsed = round(loop.time() - started_at, 3)
            segments = self._assistant_segments_for_current_turn(
                snapshot,
                turn_baseline=turn_baseline,
            )
            entry = {
                "state": state.value,
                "elapsed_seconds": elapsed,
                "response_count": len(snapshot.assistant_messages),
                "response_count_before": turn_baseline.assistant_count_before,
                "assistant_segments_count": len(segments),
                "is_generating": snapshot.is_generating,
                "stop_button_visible": snapshot.stop_button_visible,
                "stop_button_count": snapshot.stop_button_count,
                "prompt_available": snapshot.prompt_available,
                "send_button_idle": snapshot.send_button_idle,
                "send_button_state": snapshot.send_button_state,
                "composer_disabled": snapshot.composer_disabled,
                "composer_editable": snapshot.composer_editable,
                "aria_busy": snapshot.aria_busy,
                "streaming_indicators_count": snapshot.streaming_indicators_count,
                "thinking_indicators_count": snapshot.thinking_indicators_count,
                "visible_indicators": list(snapshot.visible_indicators),
                "continue_required": snapshot.continue_required,
                "current_turn_id": snapshot.current_turn_id,
                "latest_assistant_text_chars": snapshot.latest_assistant_text_chars,
                "latest_assistant_text_preview": snapshot.latest_assistant_text_preview,
            }
            if reason:
                entry["reason"] = reason
            timeline.append(entry)

        try:
            while True:
                snapshot = await self._response_wait_snapshot(tab)
                segments = self._assistant_segments_for_current_turn(
                    snapshot,
                    turn_baseline=turn_baseline,
                )
                now = loop.time()

                if hard_timeout > 0 and now - started_at >= hard_timeout:
                    await record(ResponseWaitState.FAILED, snapshot, reason="hard_timeout")
                    details = self._response_wait_debug_payload(
                        response_count_before=response_count_before,
                        turn_baseline=turn_baseline,
                        snapshot=snapshot,
                        segments=segments,
                        timeline=timeline,
                        final_idle_detected=False,
                        timeout_reason="hard_timeout",
                    )
                    self._write_response_wait_debug(debug_context, details)
                    raise NoDriverTimeoutError(
                        "Истекло время ожидания финального idle-состояния ChatGPT Web.",
                        stage="chatgpt.response.wait.started",
                        url=await self.session.current_url(),
                        page_title=await self.session.current_title(),
                        details=details,
                    )

                if (
                    not segments
                    and max_empty_wait is not None
                    and max_empty_wait > 0
                    and now - started_at >= max_empty_wait
                ):
                    await record(ResponseWaitState.FAILED, snapshot, reason="empty_wait_timeout")
                    details = self._response_wait_debug_payload(
                        response_count_before=response_count_before,
                        turn_baseline=turn_baseline,
                        snapshot=snapshot,
                        segments=segments,
                        timeline=timeline,
                        final_idle_detected=False,
                        timeout_reason="empty_wait_timeout",
                    )
                    self._write_response_wait_debug(debug_context, details)
                    raise NoDriverTimeoutError(
                        "ChatGPT Web не показал новый assistant segment за отведённое время.",
                        stage="chatgpt.response.wait.started",
                        url=await self.session.current_url(),
                        page_title=await self.session.current_title(),
                        details=details,
                    )

                state = self._response_wait_state(snapshot, segments=segments)
                await record(state, snapshot)
                if progress_log_interval > 0 and now >= next_progress_log_at:
                    progress_payload = self._response_wait_progress_payload(
                        debug_context=debug_context,
                        turn_baseline=turn_baseline,
                        snapshot=snapshot,
                        segments=segments,
                        state=state,
                        elapsed_seconds=round(now - started_at, 3),
                        response_timeout_seconds=hard_timeout,
                    )
                    self._log_response_wait_progress(progress_payload)
                    self._write_response_wait_debug(
                        debug_context,
                        self._response_wait_debug_payload(
                            response_count_before=response_count_before,
                            turn_baseline=turn_baseline,
                            snapshot=snapshot,
                            segments=segments,
                            timeline=timeline,
                            final_idle_detected=False,
                            detected_phase=progress_payload["detected_phase"],
                        ),
                    )
                    next_progress_log_at = now + progress_log_interval

                if segments and snapshot.final_idle:
                    if idle_started_at is None:
                        idle_started_at = now
                        await record(ResponseWaitState.WAITING_FOR_FINAL_IDLE, snapshot)
                    if now - idle_started_at >= idle_confirm_seconds:
                        await record(ResponseWaitState.FINAL_RESPONSE_READY, snapshot)
                        result = ResponseWaitResult(
                            final_answer=segments[-1],
                            assistant_segments=segments,
                            response_count_before=response_count_before,
                            response_count_after=len(snapshot.assistant_messages),
                            final_segment_index=len(segments) - 1,
                            wait_state_timeline=timeline,
                            final_idle_detected=True,
                            detected_model=snapshot.detected_model,
                            detected_reasoning_mode=snapshot.detected_reasoning_mode,
                        )
                        self._write_response_wait_debug(
                            debug_context,
                            self._response_wait_debug_payload(
                                response_count_before=response_count_before,
                                turn_baseline=turn_baseline,
                                snapshot=snapshot,
                                segments=segments,
                                timeline=timeline,
                                final_idle_detected=True,
                                detected_phase="idle_with_answer",
                            ),
                        )
                        return result
                else:
                    idle_started_at = None

                await self._response_wait_sleep(0.5)
        except asyncio.CancelledError:
            try:
                await self._try_stop_generation(tab)
            finally:
                cancelled_snapshot = await self._safe_response_wait_snapshot(tab)
                await record(ResponseWaitState.CANCELLED, cancelled_snapshot, reason="cancelled")
                details = self._response_wait_debug_payload(
                    response_count_before=response_count_before,
                    turn_baseline=turn_baseline,
                    snapshot=cancelled_snapshot,
                    segments=self._assistant_segments_for_current_turn(
                        cancelled_snapshot,
                        turn_baseline=turn_baseline,
                    ),
                    timeline=timeline,
                    final_idle_detected=False,
                    timeout_reason="cancelled",
                )
                self._write_response_wait_debug(debug_context, details)
            raise

    def _log_response_wait_progress(self, payload: dict[str, Any]) -> None:
        logger.info(
            "chatgpt.response.wait.progress %s",
            json.dumps(payload, ensure_ascii=False, default=str),
            extra={
                "task_id": payload.get("task_id"),
                "run_id": payload.get("run_id"),
                "agent_id": payload.get("agent_id"),
                "stage": "chatgpt.response.wait.progress",
                **payload,
            },
        )

    def _response_wait_progress_payload(
        self,
        *,
        debug_context: dict[str, Any],
        turn_baseline: ResponseTurnBaseline,
        snapshot: ResponseWaitSnapshot,
        segments: list[str],
        state: ResponseWaitState,
        elapsed_seconds: float,
        response_timeout_seconds: float,
    ) -> dict[str, Any]:
        return {
            "task_id": debug_context.get("task_id"),
            "run_id": debug_context.get("run_id"),
            "agent_id": debug_context.get("agent_id"),
            "step_id": debug_context.get("step_id"),
            "current_turn_id": snapshot.current_turn_id,
            "before_count": turn_baseline.assistant_count_before,
            "assistant_count_after_send": len(snapshot.assistant_messages),
            "latest_assistant_text_chars": snapshot.latest_assistant_text_chars,
            "latest_assistant_text_preview": snapshot.latest_assistant_text_preview,
            "stop_button_count": snapshot.stop_button_count,
            "send_button_state": snapshot.send_button_state,
            "composer_disabled": snapshot.composer_disabled,
            "composer_editable": snapshot.composer_editable,
            "aria_busy": snapshot.aria_busy,
            "streaming_indicators_count": snapshot.streaming_indicators_count,
            "thinking_indicators_count": snapshot.thinking_indicators_count,
            "detected_phase": self._detected_response_phase(
                snapshot,
                segments=segments,
                state=state,
            ),
            "elapsed_seconds": elapsed_seconds,
            "response_timeout_seconds": response_timeout_seconds,
        }

    def _detected_response_phase(
        self,
        snapshot: ResponseWaitSnapshot,
        *,
        segments: list[str],
        state: ResponseWaitState,
    ) -> str:
        if not segments:
            if snapshot.final_idle and snapshot.user_messages_count > 0:
                return "stuck_unknown"
            if snapshot.thinking_indicators_count:
                return "thinking"
            if snapshot.is_generating or snapshot.stop_button_visible:
                return "waiting_for_first_segment"
            return "waiting_for_first_segment"
        if snapshot.thinking_indicators_count:
            return "thinking"
        if (
            snapshot.is_generating
            or snapshot.stop_button_visible
            or snapshot.streaming_indicators_count
            or state == ResponseWaitState.THINKING_OR_STREAMING
        ):
            return "streaming"
        if snapshot.final_idle:
            return "idle_with_answer"
        return "stuck_unknown"

    def _assistant_segments_for_current_turn(
        self,
        snapshot: ResponseWaitSnapshot,
        *,
        turn_baseline: ResponseTurnBaseline,
    ) -> list[str]:
        messages = snapshot.assistant_messages
        indexes = snapshot.assistant_message_indexes
        if (
            snapshot.user_messages_count > turn_baseline.user_count_before
            and snapshot.last_user_message_index is not None
            and len(indexes) == len(messages)
        ):
            segments = [
                message
                for message, index in zip(messages, indexes, strict=False)
                if index > int(snapshot.last_user_message_index)
            ]
        else:
            segments = messages[turn_baseline.assistant_count_before :]
        return [
            segment
            for segment in segments
            if segment.strip() and not _is_thinking_label_only(segment)
        ]

    def _response_wait_state(
        self,
        snapshot: ResponseWaitSnapshot,
        *,
        segments: list[str],
    ) -> ResponseWaitState:
        if not segments:
            if (
                snapshot.is_generating
                or snapshot.stop_button_visible
                or snapshot.visible_indicators
            ):
                return ResponseWaitState.GENERATION_STARTED
            return ResponseWaitState.PROMPT_SUBMITTED
        if len(segments) > 1:
            return ResponseWaitState.INTERMEDIATE_RESPONSE_SEEN
        if snapshot.is_generating or snapshot.stop_button_visible or snapshot.visible_indicators:
            return ResponseWaitState.THINKING_OR_STREAMING
        return ResponseWaitState.ASSISTANT_SEGMENT_SEEN

    async def _response_wait_snapshot(self, tab: Any) -> ResponseWaitSnapshot:
        result = await evaluate_script(tab, self._build_response_wait_probe_script())
        if not isinstance(result, dict):
            messages = await self._assistant_messages(tab)
            return ResponseWaitSnapshot(
                assistant_messages=messages,
                is_generating=await self._has_stop_button(tab),
                stop_button_visible=await self._has_stop_button(tab),
                prompt_available=False,
                send_button_idle=False,
            )
        return ResponseWaitSnapshot(
            assistant_messages=[str(message) for message in result.get("assistantMessages") or []],
            is_generating=bool(result.get("isGenerating")),
            stop_button_visible=bool(result.get("stopButtonVisible")),
            prompt_available=bool(result.get("promptAvailable")),
            send_button_idle=bool(result.get("sendButtonIdle")),
            visible_indicators=[str(item) for item in result.get("visibleIndicators") or []],
            continue_required=bool(result.get("continueRequired")),
            detected_model=_str_or_none(result.get("detectedModel")),
            detected_reasoning_mode=_str_or_none(result.get("detectedReasoningMode")),
            assistant_message_ids=[str(item) for item in result.get("assistantMessageIds") or []],
            assistant_message_indexes=[
                _int_or_default(item, index)
                for index, item in enumerate(result.get("assistantMessageIndexes") or [])
            ],
            user_messages_count=_int_or_default(result.get("userMessagesCount"), 0),
            last_user_message_id=_str_or_none(result.get("lastUserMessageId")),
            last_user_message_index=_optional_int(result.get("lastUserMessageIndex")),
            current_turn_id=_str_or_none(result.get("currentTurnId")),
            stop_button_count=_int_or_default(result.get("stopButtonCount"), 0),
            send_button_state=str(result.get("sendButtonState") or "unknown"),
            composer_disabled=bool(result.get("composerDisabled")),
            composer_editable=bool(result.get("composerEditable")),
            aria_busy=bool(result.get("ariaBusy")),
            streaming_indicators_count=_int_or_default(
                result.get("streamingIndicatorsCount"),
                0,
            ),
            thinking_indicators_count=_int_or_default(
                result.get("thinkingIndicatorsCount"),
                0,
            ),
        )

    async def _safe_response_wait_snapshot(self, tab: Any) -> ResponseWaitSnapshot:
        try:
            return await self._response_wait_snapshot(tab)
        except Exception:
            return ResponseWaitSnapshot(
                assistant_messages=[],
                is_generating=False,
                stop_button_visible=False,
                prompt_available=False,
                send_button_idle=False,
            )

    async def _response_wait_sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def _response_wait_debug_payload(
        self,
        *,
        response_count_before: int,
        turn_baseline: ResponseTurnBaseline | None = None,
        snapshot: ResponseWaitSnapshot,
        segments: list[str],
        timeline: list[dict[str, Any]],
        final_idle_detected: bool,
        timeout_reason: str | None = None,
        detected_phase: str | None = None,
    ) -> dict[str, Any]:
        turn_baseline = turn_baseline or ResponseTurnBaseline(
            assistant_count_before=response_count_before
        )
        payload: dict[str, Any] = {
            "response_count_before": response_count_before,
            "response_count_after": len(snapshot.assistant_messages),
            "user_count_before": turn_baseline.user_count_before,
            "user_count_after": snapshot.user_messages_count,
            "current_turn_id": snapshot.current_turn_id,
            "assistant_segments_count": len(segments),
            "assistant_segments_lengths": [len(segment) for segment in segments],
            "assistant_segments": segments,
            "combined_assistant_transcript": "\n\n".join(segments),
            "final_segment_index": len(segments) - 1 if segments else None,
            "latest_assistant_text_chars": snapshot.latest_assistant_text_chars,
            "latest_assistant_text_preview": snapshot.latest_assistant_text_preview,
            "last_snapshot": {
                "stop_button_count": snapshot.stop_button_count,
                "send_button_state": snapshot.send_button_state,
                "composer_disabled": snapshot.composer_disabled,
                "composer_editable": snapshot.composer_editable,
                "aria_busy": snapshot.aria_busy,
                "streaming_indicators_count": snapshot.streaming_indicators_count,
                "thinking_indicators_count": snapshot.thinking_indicators_count,
                "visible_indicators": list(snapshot.visible_indicators),
                "continue_required": snapshot.continue_required,
            },
            "wait_state_timeline": timeline,
            "final_idle_detected": final_idle_detected,
            "detected_model": snapshot.detected_model,
            "detected_reasoning_mode": snapshot.detected_reasoning_mode,
        }
        if timeout_reason:
            payload["timeout_reason"] = timeout_reason
        if detected_phase:
            payload["detected_phase"] = detected_phase
        return payload

    def _write_response_wait_debug(
        self,
        debug_context: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        workspace_path = debug_context.get("workspace_path")
        agent_id = str(debug_context.get("agent_id") or "manual")
        step_id = str(debug_context.get("step_id") or "")
        debug_dir = (
            Path(workspace_path) / "debug"
            if workspace_path is not None
            else self.settings.data_dir / "debug" / "nodriver"
        )
        try:
            debug_dir.mkdir(parents=True, exist_ok=True)
            filename_stem = f"{agent_id}_{step_id}" if step_id else agent_id
            safe_filename_stem = "".join(
                character if character.isalnum() or character in {"_", "-"} else "_"
                for character in filename_stem
            )
            path = debug_dir / f"nodriver_response_wait_{safe_filename_stem}.json"
            path.write_text(
                json.dumps(
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "task_id": debug_context.get("task_id"),
                        "run_id": debug_context.get("run_id"),
                        "agent_id": debug_context.get("agent_id"),
                        "step_id": debug_context.get("step_id"),
                        "agent_task_id": debug_context.get("agent_task_id"),
                        **payload,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            logger.warning("Could not write NoDriver response wait debug report", exc_info=True)

    def _build_response_wait_probe_script(self) -> str:
        assistant_query = ASSISTANT_MESSAGE_QUERY
        prompt_selectors_json = json.dumps(PROMPT_INPUT_SELECTORS, ensure_ascii=False)
        send_selectors_json = json.dumps(SEND_BUTTON_SELECTORS, ensure_ascii=False)
        stop_selectors_json = json.dumps(STOP_BUTTON_SELECTORS, ensure_ascii=False)
        return f"""
/* RESPONSE_WAIT_PROBE */
(() => {{
  const promptSelectors = {prompt_selectors_json};
  const sendSelectors = {send_selectors_json};
  const stopSelectors = {stop_selectors_json};
  const fallbackAssistantMessages = ({assistant_query});

  function visible(node) {{
    if (!node) return false;
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 &&
      style.display !== 'none' && style.visibility !== 'hidden';
  }}

  function firstVisible(selectors) {{
    for (const selector of selectors) {{
      try {{
        const node = document.querySelector(selector);
        if (visible(node)) return node;
      }} catch (_error) {{}}
    }}
    return null;
  }}

  function visibleAll(selectors) {{
    const nodes = [];
    for (const selector of selectors) {{
      try {{
        for (const node of document.querySelectorAll(selector)) {{
          if (visible(node)) nodes.push(node);
        }}
      }} catch (_error) {{}}
    }}
    return nodes;
  }}

  function textOf(node) {{
    return (node && (node.innerText || node.textContent || '') || '').trim();
  }}

  function cleanMessageText(node, role) {{
    if (!node) return '';
    const clone = node.cloneNode(true);
    for (const removable of clone.querySelectorAll('script, style, .sr-only')) {{
      removable.remove();
    }}
    if (role === 'assistant') {{
      for (const button of clone.querySelectorAll('button')) {{
        const text = textOf(button).toLowerCase();
        if (text.startsWith('thought for ') || text === 'thinking' || text === 'думаю') {{
          button.remove();
        }}
      }}
      for (const thinking of clone.querySelectorAll('.result-thinking')) {{
        const text = textOf(thinking).toLowerCase();
        if (!text || text.startsWith('thought for ') || text === 'думаю') {{
          thinking.remove();
        }}
      }}
    }}
    return textOf(clone);
  }}

  function stableId(node, index) {{
    return node.getAttribute('data-turn-id') ||
      node.getAttribute('data-turn-id-container') ||
      node.getAttribute('data-message-id') ||
      node.getAttribute('data-testid') ||
      node.id ||
      `${{node.getAttribute('data-message-author-role') ||
        node.getAttribute('data-turn') ||
        'message'}}:${{index}}`;
  }}

  function messageItems() {{
    let nodes = Array.from(
      document.querySelectorAll('[data-turn="user"], [data-turn="assistant"]')
    );
    const usesTurnContainers = nodes.length > 0;
    if (!nodes.length) {{
      nodes = Array.from(document.querySelectorAll('[data-message-author-role]'));
    }}
    const hasExplicitRoles = nodes.length > 0;
    if (!nodes.length) {{
      nodes = Array.from(document.querySelectorAll('article'));
    }}
    if (!nodes.length) {{
      return (fallbackAssistantMessages || []).map((text, index) => ({{
        id: `assistant-fallback:${{index}}`,
        role: 'assistant',
        text,
        index,
      }}));
    }}
    return nodes
      .map((node, index) => {{
        const explicitRole = node.getAttribute('data-turn') ||
          node.getAttribute('data-message-author-role');
        const role = explicitRole ||
          (hasExplicitRoles ? '' : 'assistant');
        return {{
          id: stableId(node, index),
          role,
          text: cleanMessageText(node, role),
          index,
        }};
      }})
      .filter((item) => item.text || (usesTurnContainers && item.role === 'assistant'));
  }}

  function nodeLabel(node) {{
    const text = (node.innerText || node.textContent || '').trim();
    return [
      node.getAttribute('data-testid') || '',
      node.getAttribute('aria-label') || '',
      node.getAttribute('aria-live') || '',
      String(node.className || ''),
      text.length <= 160 ? text : '',
    ].filter(Boolean).join(' ').toLowerCase();
  }}

  function indicatorName(node) {{
    const label = nodeLabel(node);
    if (!label) return null;
    if (node.getAttribute('role') === 'progressbar') return 'progressbar';
    if (/result-streaming|animate-spin|spinner|progress/.test(label)) return 'progress';
    if (/\\b(generating|streaming|searching|working)\\b/.test(label)) return 'generating';
    if (/\\b(thinking|tool|processing)\\b/.test(label)) return 'thinking';
    return null;
  }}

  const messages = messageItems();
  const assistantItems = messages.filter((item) => item.role === 'assistant');
  const userItems = messages.filter((item) => item.role === 'user');
  const assistantMessages = assistantItems.map((item) => item.text);
  const assistantMessageIds = assistantItems.map((item) => item.id);
  const assistantMessageIndexes = assistantItems.map((item) => item.index);
  const lastUser = userItems.length ? userItems[userItems.length - 1] : null;
  const stopButtons = visibleAll(stopSelectors);
  const stopButton = stopButtons[0] || null;
  const sendButtons = visibleAll(sendSelectors);
  const sendButton = sendButtons[0] || null;
  const prompt = firstVisible(promptSelectors);
  const bodyText = document.body ? document.body.innerText.toLowerCase() : '';
  const indicatorSelectors = [
    '[role="status"]',
    '[role="progressbar"]',
    '[aria-live]',
    '[data-testid]',
    '[class*="result-streaming"]',
    '[class*="animate-spin"]',
    '[class*="spinner"]',
  ];
  const visibleIndicators = [];
  let streamingIndicatorsCount = 0;
  let thinkingIndicatorsCount = 0;
  for (const selector of indicatorSelectors) {{
    try {{
      for (const node of document.querySelectorAll(selector)) {{
        if (!visible(node)) continue;
        const name = indicatorName(node);
        if (!name) continue;
        if (!visibleIndicators.includes(name)) visibleIndicators.push(name);
        if (name === 'thinking') {{
          thinkingIndicatorsCount += 1;
        }} else {{
          streamingIndicatorsCount += 1;
        }}
      }}
    }} catch (_error) {{}}
  }}
  const actionTexts = Array.from(document.querySelectorAll('button, [role="button"]'))
    .filter(visible)
    .map((node) => (node.innerText || node.getAttribute('aria-label') || '').trim().toLowerCase())
    .filter(Boolean);
  const continueRequired =
    actionTexts.some((text) => text.includes('continue generating')) ||
    actionTexts.some((text) => text === 'resume' || text.includes('resume generation')) ||
    actionTexts.some((text) => text === 'try again' || text.includes('regenerate'));
  const composerDisabled = Boolean(prompt) && (
    prompt.disabled ||
    prompt.readOnly ||
    prompt.getAttribute('aria-disabled') === 'true'
  );
  const composerEditable = Boolean(prompt) && (
    prompt.isContentEditable ||
    prompt.tagName === 'TEXTAREA' ||
    prompt.tagName === 'INPUT' ||
    prompt.getAttribute('role') === 'textbox'
  ) && !composerDisabled;
  const ariaBusy = Array.from(document.querySelectorAll('[aria-busy="true"]')).some(visible);
  const sendButtonDisabled = Boolean(sendButton) && (
    sendButton.disabled ||
    sendButton.getAttribute('aria-disabled') === 'true'
  );
  const sendButtonState = stopButton ? 'stop_visible' :
    sendButton ? (sendButtonDisabled ? 'send_disabled' : 'send_enabled') :
    (prompt ? 'send_hidden' : 'missing');
  const sendButtonIdle = Boolean(prompt) &&
    !stopButton &&
    !ariaBusy &&
    visibleIndicators.length === 0 &&
    !continueRequired &&
    composerEditable;
  const modelButtons = Array.from(document.querySelectorAll('button, [role="button"]'))
    .map((node) => (node.innerText || node.getAttribute('aria-label') || '').trim())
    .filter(Boolean);
  const detectedModel = modelButtons.find((text) => /gpt|model|thinking/i.test(text)) || null;

  return {{
    assistantMessages,
    assistantMessageIds,
    assistantMessageIndexes,
    userMessagesCount: userItems.length,
    lastUserMessageId: lastUser ? lastUser.id : null,
    lastUserMessageIndex: lastUser ? lastUser.index : null,
    currentTurnId: lastUser ? lastUser.id : null,
    stopButtonVisible: Boolean(stopButton),
    stopButtonCount: stopButtons.length,
    isGenerating: Boolean(stopButton) || ariaBusy || visibleIndicators.length > 0,
    promptAvailable: Boolean(prompt),
    sendButtonIdle,
    sendButtonState,
    composerDisabled,
    composerEditable,
    ariaBusy,
    streamingIndicatorsCount,
    thinkingIndicatorsCount,
    visibleIndicators,
    continueRequired,
    detectedModel,
    detectedReasoningMode: bodyText.includes('extended') ? 'extended' : null,
  }};
}})()
"""

    async def _has_stop_button(self, tab: Any) -> bool:
        for selector in STOP_BUTTON_SELECTORS:
            try:
                if await tab.query_selector(selector) is not None:
                    return True
            except Exception:
                continue
        return False

    async def _try_stop_generation(self, tab: Any) -> bool:
        for selector in STOP_BUTTON_SELECTORS:
            try:
                element = await tab.query_selector(selector)
            except Exception:
                element = None
            if element is None:
                continue
            click = getattr(element, "click", None)
            if click is None:
                continue
            result = click()
            if asyncio.iscoroutine(result):
                await result
            return True
        return False

    async def _ensure_preferred_model(
        self,
        tab: Any,
        debug_context: dict[str, Any],
    ) -> dict[str, Any]:
        detected = (
            {
                "current_model": debug_context.get("current_model"),
                "reasoning_mode": debug_context.get("reasoning_mode"),
            }
            if "current_model" in debug_context or "reasoning_mode" in debug_context
            else await self._detect_current_model(tab)
        )
        preferred_model = self.settings.nodriver_preferred_model_name.strip()
        preferred_reasoning = self.settings.nodriver_preferred_reasoning_mode.strip()
        if preferred_model or preferred_reasoning:
            self._log_stage(
                "chatgpt.model.detected",
                debug_context,
                preferred_model=preferred_model,
                preferred_reasoning_mode=preferred_reasoning,
                detected_model=detected.get("current_model"),
                detected_reasoning_mode=detected.get("reasoning_mode"),
            )
        if not self.settings.nodriver_require_preferred_model:
            return detected
        detected_model = str(detected.get("current_model") or "").strip()
        detected_reasoning = str(detected.get("reasoning_mode") or "").strip()
        if preferred_model and (
            not detected_model or not _model_name_matches(detected_model, preferred_model)
        ):
            raise NoDriverPreferredModelError(
                "preferred model not active",
                stage="chatgpt.model.detected",
                url=await self.session.current_url(),
                page_title=await self.session.current_title(),
                details={
                    "preferred_model": preferred_model,
                    "preferred_reasoning_mode": preferred_reasoning,
                    "detected_model": detected_model or None,
                    "detected_reasoning_mode": detected.get("reasoning_mode"),
                },
            )
        if preferred_reasoning and (
            not detected_reasoning
            or not _model_name_matches(detected_reasoning, preferred_reasoning)
        ):
            raise NoDriverPreferredModelError(
                "preferred reasoning mode not active",
                stage="chatgpt.model.detected",
                url=await self.session.current_url(),
                page_title=await self.session.current_title(),
                details={
                    "preferred_model": preferred_model,
                    "preferred_reasoning_mode": preferred_reasoning,
                    "detected_model": detected_model or None,
                    "detected_reasoning_mode": detected_reasoning or None,
                },
            )
        return detected

    async def _detect_current_model(self, tab: Any) -> dict[str, Any]:
        try:
            result = await evaluate_script(tab, self._build_model_detection_script())
        except Exception:
            return {"current_model": None, "reasoning_mode": None}
        if not isinstance(result, dict):
            return {"current_model": None, "reasoning_mode": None}
        return {
            "current_model": _str_or_none(result.get("currentModel")),
            "reasoning_mode": _str_or_none(result.get("reasoningMode")),
        }

    def _build_model_detection_script(self) -> str:
        return """
/* MODEL_DETECTION */
(() => {
  const candidates = Array.from(document.querySelectorAll('button, [role="button"], [aria-label]'))
    .map((node) => (node.innerText || node.getAttribute('aria-label') || '').trim())
    .filter(Boolean);
  const currentModel = candidates.find((text) => /gpt|model|thinking/i.test(text)) || null;
  const bodyText = document.body ? document.body.innerText.toLowerCase() : '';
  return {
    currentModel,
    reasoningMode: bodyText.includes('extended') ? 'extended' : null,
  };
})()
"""

    async def _enrich_error(self, exc: NoDriverProviderError) -> None:
        if exc.url is None:
            exc.url = await self.session.current_url()
        if exc.page_title is None:
            exc.page_title = await self.session.current_title()

    async def _page_diagnostics(
        self,
        tab: Any,
        *,
        login_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            result = await evaluate_script(tab, build_prompt_candidate_probe_script())
        except Exception:
            result = {}
        diagnostics = normalize_dom_probe_payload(result)
        if login_state is not None:
            diagnostics["login_state"] = login_state
        return diagnostics

    def _log_stage(
        self,
        stage: str,
        debug_context: dict[str, Any],
        **extra: Any,
    ) -> None:
        logger.info(
            stage,
            extra={
                "task_id": debug_context.get("task_id"),
                "run_id": debug_context.get("run_id"),
                "agent_id": debug_context.get("agent_id"),
                "stage": stage,
                **extra,
            },
        )


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_or_default(value: object, default: int) -> int:
    parsed = _optional_int(value)
    return default if parsed is None else parsed


def _compact_preview(text: str, *, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1].rstrip()}..."


def _is_thinking_label_only(text: str) -> bool:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return True
    if normalized in {"thinking", "думаю", "думает"}:
        return True
    return normalized.startswith("thought for ") and len(normalized) <= 80


def _model_name_matches(detected: str, preferred: str) -> bool:
    def normalize(value: str) -> str:
        return "".join(character.lower() for character in value if character.isalnum())

    detected_normalized = normalize(detected)
    preferred_normalized = normalize(preferred)
    return bool(
        detected_normalized
        and preferred_normalized
        and (
            detected_normalized == preferred_normalized
            or preferred_normalized in detected_normalized
            or detected_normalized in preferred_normalized
        )
    )
