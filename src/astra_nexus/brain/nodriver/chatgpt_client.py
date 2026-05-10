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

        before_count = len(await self._assistant_messages(tab))
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
        details = await self._insert_prompt_with_js(tab, prompt)
        if details.get("ok"):
            return details
        details = await self._prompt_insert_failure_details(tab, details)
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

  const found = selectors
    .map((selector) => {{
      try {{
        return {{ selector, node: document.querySelector(selector) }};
      }} catch (_error) {{
        return null;
      }}
    }})
    .find((entry) => entry && entry.node && visible(entry.node));
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
        debug_context: dict[str, Any],
    ) -> ResponseWaitResult:
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
            segments = self._new_assistant_segments(
                snapshot.assistant_messages,
                response_count_before=response_count_before,
            )
            entry = {
                "state": state.value,
                "elapsed_seconds": elapsed,
                "response_count": len(snapshot.assistant_messages),
                "assistant_segments_count": len(segments),
                "is_generating": snapshot.is_generating,
                "stop_button_visible": snapshot.stop_button_visible,
                "prompt_available": snapshot.prompt_available,
                "send_button_idle": snapshot.send_button_idle,
                "visible_indicators": list(snapshot.visible_indicators),
                "continue_required": snapshot.continue_required,
            }
            if reason:
                entry["reason"] = reason
            timeline.append(entry)

        try:
            while True:
                snapshot = await self._response_wait_snapshot(tab)
                segments = self._new_assistant_segments(
                    snapshot.assistant_messages,
                    response_count_before=response_count_before,
                )
                now = loop.time()

                if hard_timeout > 0 and now - started_at >= hard_timeout:
                    await record(ResponseWaitState.FAILED, snapshot, reason="hard_timeout")
                    details = self._response_wait_debug_payload(
                        response_count_before=response_count_before,
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
                    self._log_stage(
                        "chatgpt.response.wait.progress",
                        debug_context,
                        wait_state=state.value,
                        assistant_segments_count=len(segments),
                        elapsed_seconds=round(now - started_at, 3),
                        generating=snapshot.is_generating,
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
                        self._write_response_wait_debug(debug_context, result.debug_payload)
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
                    snapshot=cancelled_snapshot,
                    segments=self._new_assistant_segments(
                        cancelled_snapshot.assistant_messages,
                        response_count_before=response_count_before,
                    ),
                    timeline=timeline,
                    final_idle_detected=False,
                    timeout_reason="cancelled",
                )
                self._write_response_wait_debug(debug_context, details)
            raise

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

    def _new_assistant_segments(
        self,
        messages: list[str],
        *,
        response_count_before: int,
    ) -> list[str]:
        return [message for message in messages[response_count_before:] if message.strip()]

    def _response_wait_debug_payload(
        self,
        *,
        response_count_before: int,
        snapshot: ResponseWaitSnapshot,
        segments: list[str],
        timeline: list[dict[str, Any]],
        final_idle_detected: bool,
        timeout_reason: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "response_count_before": response_count_before,
            "response_count_after": len(snapshot.assistant_messages),
            "assistant_segments_count": len(segments),
            "assistant_segments_lengths": [len(segment) for segment in segments],
            "final_segment_index": len(segments) - 1 if segments else None,
            "wait_state_timeline": timeline,
            "final_idle_detected": final_idle_detected,
            "detected_model": snapshot.detected_model,
            "detected_reasoning_mode": snapshot.detected_reasoning_mode,
        }
        if timeout_reason:
            payload["timeout_reason"] = timeout_reason
        return payload

    def _write_response_wait_debug(
        self,
        debug_context: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        workspace_path = debug_context.get("workspace_path")
        agent_id = str(debug_context.get("agent_id") or "manual")
        debug_dir = (
            Path(workspace_path) / "debug"
            if workspace_path is not None
            else self.settings.data_dir / "debug" / "nodriver"
        )
        try:
            debug_dir.mkdir(parents=True, exist_ok=True)
            safe_agent_id = "".join(
                character if character.isalnum() or character in {"_", "-"} else "_"
                for character in agent_id
            )
            path = debug_dir / f"nodriver_response_wait_{safe_agent_id}.json"
            path.write_text(
                json.dumps(
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
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
        stop_selectors_json = json.dumps(STOP_BUTTON_SELECTORS, ensure_ascii=False)
        return f"""
/* RESPONSE_WAIT_PROBE */
(() => {{
  const promptSelectors = {prompt_selectors_json};
  const stopSelectors = {stop_selectors_json};
  const assistantMessages = ({assistant_query});

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

  const stopButton = firstVisible(stopSelectors);
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
  for (const selector of indicatorSelectors) {{
    try {{
      for (const node of document.querySelectorAll(selector)) {{
        if (!visible(node)) continue;
        const name = indicatorName(node);
        if (name && !visibleIndicators.includes(name)) visibleIndicators.push(name);
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
  const sendButtonIdle = Boolean(prompt) &&
    !stopButton &&
    visibleIndicators.length === 0 &&
    !continueRequired;
  const modelButtons = Array.from(document.querySelectorAll('button, [role="button"]'))
    .map((node) => (node.innerText || node.getAttribute('aria-label') || '').trim())
    .filter(Boolean);
  const detectedModel = modelButtons.find((text) => /gpt|model|thinking/i.test(text)) || null;

  return {{
    assistantMessages,
    stopButtonVisible: Boolean(stopButton),
    isGenerating: Boolean(stopButton) || visibleIndicators.length > 0,
    promptAvailable: Boolean(prompt),
    sendButtonIdle,
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
