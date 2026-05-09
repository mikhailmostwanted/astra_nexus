from __future__ import annotations

import asyncio
import json
import logging
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
            return await asyncio.wait_for(
                self._ask(prompt, debug_context),
                timeout=self.settings.nodriver_response_timeout_seconds,
            )
        except TimeoutError as exc:
            raise NoDriverTimeoutError(
                stage="chatgpt.response.wait.started",
                url=await self.session.current_url(),
                page_title=await self.session.current_title(),
            ) from exc
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
        await self._wait_for_generation(tab, before_count)
        self._log_stage("chatgpt.response.wait.ok", debug_context)

        self._log_stage("chatgpt.response.parse.started", debug_context)
        try:
            response = parse_last_assistant_response(await self._assistant_messages(tab))
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

    async def _wait_for_generation(self, tab: Any, before_count: int) -> None:
        deadline = (
            asyncio.get_running_loop().time() + self.settings.nodriver_response_timeout_seconds
        )
        while asyncio.get_running_loop().time() < deadline:
            if len(
                await self._assistant_messages(tab)
            ) > before_count and not await self._has_stop_button(tab):
                return
            await asyncio.sleep(1)
        raise NoDriverTimeoutError(
            stage="chatgpt.response.wait.started",
            url=await self.session.current_url(),
            page_title=await self.session.current_title(),
        )

    async def _has_stop_button(self, tab: Any) -> bool:
        for selector in STOP_BUTTON_SELECTORS:
            try:
                if await tab.query_selector(selector) is not None:
                    return True
            except Exception:
                continue
        return False

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
