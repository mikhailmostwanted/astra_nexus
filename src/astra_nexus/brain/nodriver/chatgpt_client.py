from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from astra_nexus.brain.nodriver.artifact_detector import (
    ArtifactDetectionResult,
    artifact_detection_from_probe_payload,
    build_artifact_detector_probe_script,
)
from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.dom_probe import (
    LOGIN_STATE_PROBE_SCRIPT,
    build_prompt_candidate_probe_script,
    evaluate_script,
    login_state_from_probe,
    normalize_dom_probe_payload,
)
from astra_nexus.brain.nodriver.download_manager import (
    NoDriverDownloadManager,
    RequestedFileDownloadResult,
    requested_file_dirs,
)
from astra_nexus.brain.nodriver.exceptions import (
    NoDriverArtifactDownloadError,
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
from astra_nexus.brain.nodriver.turn_probe import (
    build_turn_dump_probe_script,
    normalize_turn_items,
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
    assistant_turns: list[dict[str, Any]] = field(default_factory=list)

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
    def latest_assistant_turn(self) -> dict[str, Any]:
        return self.assistant_turns[-1] if self.assistant_turns else {}

    @property
    def raw_assistant_text_preview(self) -> str:
        value = self.latest_assistant_turn.get("rawTextPreview")
        return str(value or self.latest_assistant_text_preview)

    @property
    def final_candidate_previews(self) -> list[dict[str, Any]]:
        value = self.latest_assistant_turn.get("finalCandidatePreviews")
        return list(value) if isinstance(value, list) else []

    @property
    def thought_candidate_previews(self) -> list[dict[str, Any]]:
        value = self.latest_assistant_turn.get("thoughtCandidatePreviews")
        return list(value) if isinstance(value, list) else []

    @property
    def rejected_candidate_reasons(self) -> list[dict[str, Any]]:
        value = self.latest_assistant_turn.get("rejectedCandidateReasons")
        return list(value) if isinstance(value, list) else []

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
    structured_answer: dict[str, Any] = field(default_factory=dict)

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
            "structured_answer": self.structured_answer,
        }


class ChatGPTClient:
    def __init__(self, settings: Settings, session: BrowserSession | None = None) -> None:
        self.settings = settings
        self.session = session or BrowserSession(settings)
        self.last_answer_metadata: dict[str, Any] = {}

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

        wait_result = await self._submit_prompt_and_wait(
            tab,
            prompt=prompt,
            debug_context=debug_context,
            login_state=login_state,
            ensure_model=True,
        )
        response = self._response_from_wait_result(wait_result, debug_context=debug_context)
        answer_metadata: dict[str, Any] = {
            "structured_answer": wait_result.structured_answer,
            "detected_model": wait_result.detected_model,
            "detected_reasoning_mode": wait_result.detected_reasoning_mode,
        }
        if self._requested_file_required(debug_context):
            response, answer_metadata = await self._complete_requested_file_flow(
                tab,
                first_response=response,
                debug_context=debug_context,
                answer_metadata=answer_metadata,
            )
        self.last_answer_metadata = answer_metadata
        self._log_stage("chatgpt.response.parse.ok", debug_context)
        return response

    async def _submit_prompt_and_wait(
        self,
        tab: Any,
        *,
        prompt: str,
        debug_context: dict[str, Any],
        login_state: dict[str, Any] | None = None,
        ensure_model: bool = False,
    ) -> ResponseWaitResult:
        turn_baseline = ResponseTurnBaseline.from_snapshot(
            await self._safe_response_wait_snapshot(tab)
        )
        before_count = turn_baseline.assistant_count_before
        if ensure_model:
            await self._ensure_preferred_model(tab, debug_context)
        self._log_stage("chatgpt.prompt_box.search.started", debug_context)
        await self._wait_for_prompt_box(tab, debug_context, login_state or {})

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
        return wait_result

    def _response_from_wait_result(
        self,
        wait_result: ResponseWaitResult,
        *,
        debug_context: dict[str, Any],
    ) -> str:
        self._log_stage("chatgpt.response.parse.started", debug_context)
        response = wait_result.final_answer
        if not response.strip():
            try:
                response = parse_last_assistant_response(wait_result.assistant_segments)
            except NoDriverSelectorNotFoundError as exc:
                exc.stage = exc.stage or "chatgpt.response.parse.started"
                raise
        return response

    def _requested_file_required(self, debug_context: dict[str, Any]) -> bool:
        return bool(debug_context.get("output_requested_as_file"))

    async def _complete_requested_file_flow(
        self,
        tab: Any,
        *,
        first_response: str,
        debug_context: dict[str, Any],
        answer_metadata: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        workspace_path = self._requested_file_workspace_path(debug_context)
        workspace_path.mkdir(parents=True, exist_ok=True)
        requested_dir, downloads_dir = requested_file_dirs(workspace_path)
        requested_dir.mkdir(parents=True, exist_ok=True)
        downloads_dir.mkdir(parents=True, exist_ok=True)
        request_payload = self._requested_file_request_payload(debug_context)
        self._write_requested_file_json(
            workspace_path / "requested_file_request.json",
            request_payload,
        )

        attempts: list[dict[str, Any]] = []
        response = first_response
        last_download_result = RequestedFileDownloadResult(
            success=False,
            reason="not_attempted",
            downloads_dir=downloads_dir,
        )
        for attempt_number in (1, 2):
            detection = await self._detect_requested_file_artifacts(tab)
            attempts.append(
                {
                    "attempt_number": attempt_number,
                    "detector": detection.as_dict(),
                }
            )
            self._write_artifact_detector_debug(
                workspace_path=workspace_path,
                attempts=attempts,
            )
            if detection.selected is not None:
                last_download_result = await self._download_requested_file_candidate(
                    tab=tab,
                    detection=detection,
                    workspace_path=workspace_path,
                    expected_extension=str(debug_context.get("requested_output_format") or ""),
                )
                self._write_requested_file_download_result(
                    workspace_path=workspace_path,
                    result=last_download_result,
                    attempts=attempts,
                )
                if last_download_result.success:
                    metadata = {
                        **answer_metadata,
                        "requested_file_download_result": last_download_result.as_dict(),
                        "requested_file_request_path": str(
                            workspace_path / "requested_file_request.json"
                        ),
                        "requested_file_download_result_path": str(
                            workspace_path / "requested_file_download_result.json"
                        ),
                        "artifact_detector_debug_path": str(
                            workspace_path / "artifact_detector_debug.json"
                        ),
                    }
                    return response, metadata
            else:
                last_download_result = RequestedFileDownloadResult(
                    success=False,
                    reason="no_download_candidate",
                    downloads_dir=downloads_dir,
                )
                self._write_requested_file_download_result(
                    workspace_path=workspace_path,
                    result=last_download_result,
                    attempts=attempts,
                )

            if attempt_number == 1:
                retry_context = {**debug_context, "requested_file_retry": True}
                retry_prompt = self._requested_file_retry_prompt(debug_context)
                retry_wait_result = await self._submit_prompt_and_wait(
                    tab,
                    prompt=retry_prompt,
                    debug_context=retry_context,
                    ensure_model=False,
                )
                response = self._response_from_wait_result(
                    retry_wait_result,
                    debug_context=retry_context,
                )
                answer_metadata = {
                    **answer_metadata,
                    "structured_answer": retry_wait_result.structured_answer,
                    "detected_model": retry_wait_result.detected_model,
                    "detected_reasoning_mode": retry_wait_result.detected_reasoning_mode,
                }

        details = {
            "requested_file_request": request_payload,
            "download_result": last_download_result.as_dict(),
            "artifact_detector_debug_path": str(workspace_path / "artifact_detector_debug.json"),
            "requested_file_download_result_path": str(
                workspace_path / "requested_file_download_result.json"
            ),
            "attempts": attempts,
        }
        raise NoDriverArtifactDownloadError(
            "ChatGPT Web не вернул реальный скачиваемый файл после повторной попытки.",
            stage="chatgpt.artifact.download",
            url=await self.session.current_url(),
            page_title=await self.session.current_title(),
            details=details,
        )

    async def _detect_requested_file_artifacts(self, tab: Any) -> ArtifactDetectionResult:
        payload = await evaluate_script(tab, build_artifact_detector_probe_script())
        return artifact_detection_from_probe_payload(payload)

    async def _download_requested_file_candidate(
        self,
        *,
        tab: Any,
        detection: ArtifactDetectionResult,
        workspace_path: Path,
        expected_extension: str | None,
    ) -> RequestedFileDownloadResult:
        if detection.selected is None:
            requested_dir, downloads_dir = requested_file_dirs(workspace_path)
            return RequestedFileDownloadResult(
                success=False,
                reason="no_download_candidate",
                downloads_dir=downloads_dir,
            )
        requested_dir, downloads_dir = requested_file_dirs(workspace_path)
        manager = NoDriverDownloadManager(
            downloads_dir=downloads_dir,
            requested_dir=requested_dir,
        )
        return await manager.download_candidate(
            tab=tab,
            candidate=detection.selected,
            expected_extension=expected_extension,
        )

    def _requested_file_workspace_path(self, debug_context: dict[str, Any]) -> Path:
        workspace_path = debug_context.get("workspace_path")
        if workspace_path is not None:
            return Path(workspace_path)
        run_id = str(debug_context.get("run_id") or "manual")
        return Path(self.settings.data_dir) / "debug" / "nodriver" / "requested_files" / run_id

    def _requested_file_request_payload(self, debug_context: dict[str, Any]) -> dict[str, Any]:
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "run_id": debug_context.get("run_id"),
            "task_id": debug_context.get("task_id"),
            "task_prompt": debug_context.get("task_prompt"),
            "agent_id": debug_context.get("agent_id"),
            "agent_task_id": debug_context.get("agent_task_id"),
            "requested_output_format": debug_context.get("requested_output_format"),
            "output_requested_as_file": bool(debug_context.get("output_requested_as_file")),
            "workspace_path": str(self._requested_file_workspace_path(debug_context)),
        }

    def _requested_file_retry_prompt(self, debug_context: dict[str, Any]) -> str:
        output_format = str(debug_context.get("requested_output_format") or "file").lstrip(".")
        return "\n".join(
            [
                "The previous response did not include a real downloadable file.",
                "",
                "Create an actual downloadable file in ChatGPT Web now.",
                f"Required format/extension: .{output_format}.",
                "Do not only describe the file.",
                "Do not paste the file contents as the final answer.",
                "Use ChatGPT's file creation/download UI so the result appears as a file card, "
                "attachment, filename chip, or download button.",
                "Return a short note only after the downloadable file is attached.",
            ]
        )

    def _write_artifact_detector_debug(
        self,
        *,
        workspace_path: Path,
        attempts: list[dict[str, Any]],
    ) -> Path | None:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "attempts": attempts,
            "latest": attempts[-1] if attempts else None,
        }
        return self._write_requested_file_json(
            workspace_path / "artifact_detector_debug.json",
            payload,
        )

    def _write_requested_file_download_result(
        self,
        *,
        workspace_path: Path,
        result: RequestedFileDownloadResult,
        attempts: list[dict[str, Any]],
    ) -> Path | None:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            **result.as_dict(),
            "attempts": attempts,
        }
        return self._write_requested_file_json(
            workspace_path / "requested_file_download_result.json",
            payload,
        )

    def _write_requested_file_json(self, path: Path, payload: dict[str, Any]) -> Path | None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            return path
        except OSError:
            logger.warning("Could not write requested file debug JSON: %s", path, exc_info=True)
            return None

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
        empty_idle_started_at: float | None = None
        empty_idle_reload_attempted = False
        empty_idle_reload_grace_seconds = max(5.0, idle_confirm_seconds)
        empty_idle_fail_seconds = max_empty_wait if max_empty_wait is not None else 45.0

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
            latest_turn = self._latest_current_assistant_turn(
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
                "raw_assistant_text_preview": _turn_raw_text_preview(
                    latest_turn,
                    fallback=snapshot.raw_assistant_text_preview,
                ),
                "final_candidate_previews": _turn_list(
                    latest_turn,
                    "finalCandidatePreviews",
                ),
                "thought_candidate_previews": _turn_list(
                    latest_turn,
                    "thoughtCandidatePreviews",
                ),
                "rejected_candidate_reasons": _turn_list(
                    latest_turn,
                    "rejectedCandidateReasons",
                ),
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
                current_assistant_turns = self._assistant_turns_for_current_turn(
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
                    debug_path = self._response_wait_debug_path(debug_context)
                    if snapshot.final_idle and current_assistant_turns:
                        details.update(
                            await self._write_idle_without_final_text_artifacts(
                                tab,
                                debug_context=debug_context,
                                turn_baseline=turn_baseline,
                            )
                        )
                        details["detected_phase"] = "stuck_unknown"
                    if debug_path is not None:
                        details["debug_artifact_path"] = str(debug_path)
                    self._write_response_wait_debug(debug_context, details, path=debug_path)
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
                    timeout_reason = (
                        "idle_without_final_text"
                        if snapshot.final_idle and current_assistant_turns
                        else "empty_wait_timeout"
                    )
                    await record(ResponseWaitState.FAILED, snapshot, reason=timeout_reason)
                    details = self._response_wait_debug_payload(
                        response_count_before=response_count_before,
                        turn_baseline=turn_baseline,
                        snapshot=snapshot,
                        segments=segments,
                        timeline=timeline,
                        final_idle_detected=False,
                        timeout_reason=timeout_reason,
                        detected_phase=(
                            "stuck_unknown" if timeout_reason == "idle_without_final_text" else None
                        ),
                    )
                    debug_path = self._response_wait_debug_path(debug_context)
                    if timeout_reason == "idle_without_final_text":
                        details.update(
                            await self._write_idle_without_final_text_artifacts(
                                tab,
                                debug_context=debug_context,
                                turn_baseline=turn_baseline,
                            )
                        )
                    if debug_path is not None:
                        details["debug_artifact_path"] = str(debug_path)
                    self._write_response_wait_debug(debug_context, details, path=debug_path)
                    message = "ChatGPT Web не показал новый assistant segment за отведённое время."
                    if timeout_reason == "idle_without_final_text":
                        message = (
                            "ChatGPT Web завершил UI idle, но финальный текст assistant "
                            "не найден в DOM."
                        )
                    if debug_path is not None:
                        message = f"{message} Debug: {debug_path}"
                    raise NoDriverTimeoutError(
                        message,
                        stage="chatgpt.response.wait.started",
                        url=await self.session.current_url(),
                        page_title=await self.session.current_title(),
                        details=details,
                    )

                state = self._response_wait_state(snapshot, segments=segments)
                await record(state, snapshot)
                if not segments and snapshot.final_idle and current_assistant_turns:
                    if empty_idle_started_at is None:
                        empty_idle_started_at = now
                    empty_idle_elapsed = now - empty_idle_started_at
                    if (
                        not empty_idle_reload_attempted
                        and empty_idle_elapsed >= empty_idle_reload_grace_seconds
                    ):
                        empty_idle_reload_attempted = True
                        await record(
                            ResponseWaitState.WAITING_FOR_FINAL_IDLE,
                            snapshot,
                            reason="reload_after_empty_idle",
                        )
                        tab = await self._reload_current_response_page(tab, debug_context)
                        idle_started_at = None
                        await self._response_wait_sleep(2.0)
                        continue
                    if empty_idle_elapsed >= empty_idle_fail_seconds:
                        await record(
                            ResponseWaitState.FAILED,
                            snapshot,
                            reason="idle_without_final_text",
                        )
                        details = self._response_wait_debug_payload(
                            response_count_before=response_count_before,
                            turn_baseline=turn_baseline,
                            snapshot=snapshot,
                            segments=segments,
                            timeline=timeline,
                            final_idle_detected=True,
                            timeout_reason="idle_without_final_text",
                            detected_phase="stuck_unknown",
                        )
                        debug_path = self._response_wait_debug_path(debug_context)
                        details.update(
                            await self._write_idle_without_final_text_artifacts(
                                tab,
                                debug_context=debug_context,
                                turn_baseline=turn_baseline,
                            )
                        )
                        if debug_path is not None:
                            details["debug_artifact_path"] = str(debug_path)
                        self._write_response_wait_debug(debug_context, details, path=debug_path)
                        message = (
                            "ChatGPT Web завершил UI idle, но финальный текст assistant "
                            "не найден в DOM."
                        )
                        if debug_path is not None:
                            message = f"{message} Debug: {debug_path}"
                        exc = NoDriverTimeoutError(
                            message,
                            stage="chatgpt.response.wait.started",
                            url=await self.session.current_url(),
                            page_title=await self.session.current_title(),
                            details=details,
                        )
                        if debug_path is not None:
                            exc.debug_report_path = str(debug_path)
                        raise exc
                else:
                    empty_idle_started_at = None
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
                        final_turn = self._final_answer_turn(
                            current_assistant_turns,
                            fallback_text=segments[-1],
                        )
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
                            structured_answer=_structured_answer_from_turn(
                                final_turn,
                                fallback_text=segments[-1],
                            ),
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
        turns = self._assistant_turns_for_current_turn(
            snapshot,
            turn_baseline=turn_baseline,
        )
        if turns:
            segments = [str(turn.get("finalText") or turn.get("text") or "") for turn in turns]
        else:
            segments = []
        if not turns and not snapshot.assistant_turns:
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

    def _assistant_turns_for_current_turn(
        self,
        snapshot: ResponseWaitSnapshot,
        *,
        turn_baseline: ResponseTurnBaseline,
    ) -> list[dict[str, Any]]:
        if not snapshot.assistant_turns:
            return []
        if (
            snapshot.user_messages_count > turn_baseline.user_count_before
            and snapshot.last_user_message_index is not None
        ):
            return [
                turn
                for turn in snapshot.assistant_turns
                if _int_or_default(turn.get("index"), -1) > int(snapshot.last_user_message_index)
            ]
        if (
            len(snapshot.assistant_turns) == len(snapshot.assistant_messages)
            and turn_baseline.assistant_count_before >= 0
        ):
            return snapshot.assistant_turns[turn_baseline.assistant_count_before :]
        return snapshot.assistant_turns

    def _latest_current_assistant_turn(
        self,
        snapshot: ResponseWaitSnapshot,
        *,
        turn_baseline: ResponseTurnBaseline,
    ) -> dict[str, Any]:
        turns = self._assistant_turns_for_current_turn(
            snapshot,
            turn_baseline=turn_baseline,
        )
        if turns:
            return turns[-1]
        return snapshot.latest_assistant_turn

    def _final_answer_turn(
        self,
        turns: list[dict[str, Any]],
        *,
        fallback_text: str,
    ) -> dict[str, Any]:
        fallback_normalized = _normalize_answer_for_match(fallback_text)
        for turn in reversed(turns):
            text = str(turn.get("finalText") or turn.get("text") or "")
            if not text.strip() or _is_thinking_label_only(text):
                continue
            if not fallback_normalized or _normalize_answer_for_match(text) == fallback_normalized:
                return turn
        return turns[-1] if turns else {}

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
        turn_payload = normalize_turn_items(result.get("turnProbe") or {})
        assistant_turns = turn_payload["assistant_items"]
        user_turns = turn_payload["user_items"]
        assistant_messages = [
            str(item.get("finalText") or item.get("text") or "") for item in assistant_turns
        ]
        assistant_indexes = [
            _int_or_default(item.get("index"), index) for index, item in enumerate(assistant_turns)
        ]
        last_user = user_turns[-1] if user_turns else None
        return ResponseWaitSnapshot(
            assistant_messages=assistant_messages,
            is_generating=bool(result.get("isGenerating")),
            stop_button_visible=bool(result.get("stopButtonVisible")),
            prompt_available=bool(result.get("promptAvailable")),
            send_button_idle=bool(result.get("sendButtonIdle")),
            visible_indicators=[str(item) for item in result.get("visibleIndicators") or []],
            continue_required=bool(result.get("continueRequired")),
            detected_model=_str_or_none(result.get("detectedModel")),
            detected_reasoning_mode=_str_or_none(result.get("detectedReasoningMode")),
            assistant_message_ids=[
                str(item.get("id") or f"assistant:{index}")
                for index, item in enumerate(assistant_turns)
            ],
            assistant_message_indexes=assistant_indexes,
            user_messages_count=len(user_turns),
            last_user_message_id=(
                _str_or_none(last_user.get("id")) if isinstance(last_user, dict) else None
            ),
            last_user_message_index=(
                _optional_int(last_user.get("index")) if isinstance(last_user, dict) else None
            ),
            current_turn_id=(
                _str_or_none(last_user.get("id")) if isinstance(last_user, dict) else None
            ),
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
            assistant_turns=assistant_turns,
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
        latest_turn = self._latest_current_assistant_turn(
            snapshot,
            turn_baseline=turn_baseline,
        )
        current_turns = self._assistant_turns_for_current_turn(
            snapshot,
            turn_baseline=turn_baseline,
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
            "raw_assistant_text_preview": _turn_raw_text_preview(
                latest_turn,
                fallback=snapshot.raw_assistant_text_preview,
            ),
            "final_candidate_previews": _turn_list(latest_turn, "finalCandidatePreviews"),
            "thought_candidate_previews": _turn_list(latest_turn, "thoughtCandidatePreviews"),
            "rejected_candidate_reasons": _turn_list(latest_turn, "rejectedCandidateReasons"),
            "structured_answer": _structured_answer_from_turn(
                latest_turn,
                fallback_text=segments[-1] if segments else "",
            ),
            "current_assistant_turns": [
                _compact_turn_debug(turn) for turn in current_turns[-5:] if isinstance(turn, dict)
            ],
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
        *,
        path: Path | None = None,
    ) -> Path | None:
        path = path or self._response_wait_debug_path(debug_context)
        if path is None:
            return None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
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
            return path
        except OSError:
            logger.warning("Could not write NoDriver response wait debug report", exc_info=True)
            return None

    def _response_wait_debug_path(self, debug_context: dict[str, Any]) -> Path | None:
        workspace_path = debug_context.get("workspace_path")
        agent_id = str(debug_context.get("agent_id") or "manual")
        step_id = str(debug_context.get("step_id") or "")
        debug_dir = (
            Path(workspace_path) / "debug"
            if workspace_path is not None
            else self.settings.data_dir / "debug" / "nodriver"
        )
        filename_stem = f"{agent_id}_{step_id}" if step_id else agent_id
        safe_filename_stem = "".join(
            character if character.isalnum() or character in {"_", "-"} else "_"
            for character in filename_stem
        )
        return debug_dir / f"nodriver_response_wait_{safe_filename_stem}.json"

    async def _write_idle_without_final_text_artifacts(
        self,
        tab: Any,
        *,
        debug_context: dict[str, Any],
        turn_baseline: ResponseTurnBaseline,
    ) -> dict[str, str]:
        paths: dict[str, str] = {}
        latest_turn: dict[str, Any] = {}
        try:
            result = await evaluate_script(
                tab,
                build_turn_dump_probe_script(limit=12, include_html=True),
            )
            turn_payload = normalize_turn_items(result)
            user_items = turn_payload["user_items"]
            assistant_items = turn_payload["assistant_items"]
            last_user_index = (
                _optional_int(user_items[-1].get("index"))
                if user_items and isinstance(user_items[-1], dict)
                else None
            )
            if last_user_index is not None and len(user_items) > turn_baseline.user_count_before:
                assistant_items = [
                    item
                    for item in assistant_items
                    if _int_or_default(item.get("index"), -1) > last_user_index
                ]
            latest_turn = assistant_items[-1] if assistant_items else {}
        except Exception as exc:
            paths["assistant_turn_dom_error"] = f"{type(exc).__name__}: {exc}"

        debug_path = self._response_wait_debug_path(debug_context)
        html_path = debug_path.with_suffix(".html") if debug_path is not None else None
        outer_html = str(latest_turn.get("outerHTML") or "")
        if html_path is not None and outer_html:
            try:
                html_path.parent.mkdir(parents=True, exist_ok=True)
                html_path.write_text(outer_html, encoding="utf-8")
                paths["assistant_turn_html_path"] = str(html_path)
            except OSError as exc:
                paths["assistant_turn_html_error"] = f"{type(exc).__name__}: {exc}"

        screenshot_path = await self._maybe_save_response_wait_screenshot(
            debug_context,
            tab,
        )
        if screenshot_path is not None:
            paths["screenshot_path"] = str(screenshot_path)
        return paths

    async def _reload_current_response_page(
        self,
        tab: Any,
        debug_context: dict[str, Any],
    ) -> Any:
        current_url = await self.session.current_url()
        if current_url:
            self._log_stage(
                "chatgpt.response.wait.reload_after_empty_idle",
                debug_context,
                url=current_url,
            )
            try:
                return await self.session.open_url(current_url)
            except NoDriverProviderError:
                raise
            except Exception as exc:
                logger.warning("Could not reload ChatGPT response page: %s", exc)
        try:
            await evaluate_script(tab, "window.location.reload()")
        except Exception as exc:
            logger.warning("Could not request ChatGPT page reload: %s", exc)
        return tab

    async def _maybe_save_response_wait_screenshot(
        self,
        debug_context: dict[str, Any],
        tab: Any,
    ) -> Path | None:
        if not self.settings.nodriver_debug_screenshots:
            return None
        debug_path = self._response_wait_debug_path(debug_context)
        if debug_path is None:
            return None
        workspace_path = debug_context.get("workspace_path")
        if workspace_path is not None:
            screenshot_path = debug_path.with_suffix(".png")
        else:
            screenshot_dir = Path(self.settings.nodriver_screenshots_dir).expanduser().resolve()
            screenshot_path = screenshot_dir / f"{debug_path.stem}.png"
        try:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            for method_name in ("save_screenshot", "get_screenshot_as_file"):
                method = getattr(tab, method_name, None)
                if method is None:
                    continue
                result = method(str(screenshot_path))
                if asyncio.iscoroutine(result):
                    await result
                return screenshot_path
        except Exception:
            logger.warning("Could not write NoDriver response wait screenshot", exc_info=True)
        return None

    def _build_response_wait_probe_script(self) -> str:
        turn_probe_script = build_turn_dump_probe_script(limit=0, include_html=False)
        prompt_selectors_json = json.dumps(PROMPT_INPUT_SELECTORS, ensure_ascii=False)
        send_selectors_json = json.dumps(SEND_BUTTON_SELECTORS, ensure_ascii=False)
        stop_selectors_json = json.dumps(STOP_BUTTON_SELECTORS, ensure_ascii=False)
        return f"""
/* RESPONSE_WAIT_PROBE */
(() => {{
  const promptSelectors = {prompt_selectors_json};
  const sendSelectors = {send_selectors_json};
  const stopSelectors = {stop_selectors_json};
  const turnProbe = {turn_probe_script};

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

  const messages = Array.isArray(turnProbe.turns) ? turnProbe.turns : [];
  const assistantItems = Array.isArray(turnProbe.assistantItems) ? turnProbe.assistantItems : [];
  const userItems = Array.isArray(turnProbe.userItems) ? turnProbe.userItems : [];
  const assistantMessages = assistantItems.map((item) => item.finalText || item.text || '');
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
    turnProbe,
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


def _turn_raw_text_preview(turn: dict[str, Any], *, fallback: str = "") -> str:
    value = turn.get("rawTextPreview") if isinstance(turn, dict) else None
    return str(value or fallback)


def _turn_list(turn: dict[str, Any], key: str) -> list[dict[str, Any]]:
    if not isinstance(turn, dict):
        return []
    value = turn.get(key)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _structured_answer_from_turn(
    turn: dict[str, Any],
    *,
    fallback_text: str,
) -> dict[str, Any]:
    if isinstance(turn, dict):
        structured = turn.get("structuredAnswer")
        if isinstance(structured, dict):
            payload = dict(structured)
            payload.setdefault("text", fallback_text)
            payload.setdefault("format", "html")
            source_links = payload.get("sourceLinks")
            payload["sourceLinks"] = (
                [item for item in source_links if isinstance(item, dict)]
                if isinstance(source_links, list)
                else []
            )
            return payload
    return {
        "format": "plain_text",
        "source": "fallback_text",
        "text": fallback_text,
        "html": "",
        "sourceLinks": [],
    }


def _normalize_answer_for_match(text: str) -> str:
    return " ".join(str(text or "").split())


def _compact_turn_debug(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": turn.get("index"),
        "role": turn.get("role"),
        "id": turn.get("id"),
        "data_turn": turn.get("dataTurn"),
        "data_testid": turn.get("dataTestid"),
        "aria_label": turn.get("ariaLabel"),
        "text_length": turn.get("textLength"),
        "text_preview": turn.get("textPreview"),
        "raw_text_length": turn.get("rawTextLength"),
        "raw_text_preview": turn.get("rawTextPreview"),
        "html_length": turn.get("htmlLength"),
        "class_names": turn.get("classNames"),
        "selector_summary": turn.get("selectorSummary"),
        "has_markdown_prose_blocks": bool(turn.get("hasMarkdownProseBlocks")),
        "has_thinking_reasoning_blocks": bool(turn.get("hasThinkingReasoningBlocks")),
        "has_hidden_aria_hidden_elements": bool(turn.get("hasHiddenAriaHiddenElements")),
        "selected_final_candidate": turn.get("selectedFinalCandidate"),
        "structured_answer": _structured_answer_from_turn(
            turn,
            fallback_text=str(turn.get("finalText") or turn.get("text") or ""),
        ),
        "final_candidate_previews": _turn_list(turn, "finalCandidatePreviews"),
        "thought_candidate_previews": _turn_list(turn, "thoughtCandidatePreviews"),
        "rejected_candidate_reasons": _turn_list(turn, "rejectedCandidateReasons"),
    }


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
