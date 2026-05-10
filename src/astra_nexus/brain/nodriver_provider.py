from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from astra_nexus.brain.base import BrainProvider, BrainResponse
from astra_nexus.brain.nodriver.chatgpt_client import ChatGPTClient
from astra_nexus.brain.nodriver.exceptions import NoDriverProviderError
from astra_nexus.config.settings import Settings, load_settings

logger = logging.getLogger(__name__)


class NoDriverProvider(BrainProvider):
    name = "nodriver"

    def __init__(
        self, settings: Settings | None = None, client: ChatGPTClient | None = None
    ) -> None:
        self.settings = settings or load_settings()
        self.client = client or ChatGPTClient(self.settings)
        self._ask_lock = threading.Lock()

    async def ask(
        self,
        agent_id: str,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> BrainResponse:
        context = context or {}
        debug_context = self._debug_context(agent_id=agent_id, context=context)
        full_prompt = self._build_prompt(agent_id=agent_id, prompt=prompt, context=context)

        await asyncio.to_thread(self._ask_lock.acquire)
        try:
            self._log_stage("provider.ask.started", debug_context)
            content = await self.client.ask(full_prompt, debug_context=debug_context)
            answer_metadata = getattr(self.client, "last_answer_metadata", {})
            self._log_stage("provider.ask.finished", debug_context)
        except NoDriverProviderError as exc:
            report_path = await self._write_debug_report(exc, debug_context)
            if report_path is not None:
                exc.debug_report_path = str(report_path)
            logger.exception(
                "NoDriverProvider недоступен",
                extra={
                    "task_id": debug_context.get("task_id"),
                    "run_id": debug_context.get("run_id"),
                    "agent_id": agent_id,
                    "stage": exc.stage,
                    "url": exc.url,
                    "error_code": exc.error_code,
                    "error_message": str(exc),
                },
            )
            raise
        except Exception as exc:
            wrapped = NoDriverProviderError(
                "Внутренняя ошибка NoDriverProvider.",
                stage="provider.ask.started",
                details={"exception_type": type(exc).__name__},
            )
            report_path = await self._write_debug_report(wrapped, debug_context)
            if report_path is not None:
                wrapped.debug_report_path = str(report_path)
            logger.exception(
                "Внутренняя ошибка NoDriverProvider",
                extra={
                    "task_id": debug_context.get("task_id"),
                    "run_id": debug_context.get("run_id"),
                    "agent_id": agent_id,
                    "stage": wrapped.stage,
                    "error_code": wrapped.error_code,
                    "error_message": str(wrapped),
                },
            )
            raise wrapped from exc
        finally:
            self._ask_lock.release()

        return BrainResponse(
            content=content,
            provider=self.name,
            metadata={
                "agent_id": agent_id,
                "mode": self.settings.nodriver_agent_mode,
                "chatgpt_url": self.settings.nodriver_chatgpt_url,
                **(answer_metadata if isinstance(answer_metadata, dict) else {}),
            },
        )

    def _build_prompt(self, agent_id: str, prompt: str, context: dict[str, Any]) -> str:
        if context.get("direct_prompt"):
            return prompt
        previous_messages = context.get("previous_messages", [])
        task_prompt = context.get("task_prompt", "")
        return (
            "Ты агент в системе Astra Nexus.\n"
            f"agent_id: {agent_id}\n"
            f"Задача пользователя: {task_prompt}\n"
            f"Контекстных сообщений: {len(previous_messages)}\n\n"
            "Ответь по своей роли кратко, структурированно и без лишней вводной.\n\n"
            f"{prompt}"
        )

    def _debug_context(self, *, agent_id: str, context: dict[str, Any]) -> dict[str, Any]:
        task_id = context.get("task_id")
        workspace_path = context.get("workspace_path")
        if workspace_path is None and task_id:
            workspace_path = Path(self.settings.workspace_base_path) / str(task_id)
        return {
            "task_id": task_id,
            "run_id": context.get("run_id"),
            "task_prompt": context.get("task_prompt"),
            "agent_id": agent_id,
            "step_id": context.get("step_id"),
            "agent_task_id": context.get("agent_task_id"),
            "attempt_number": context.get("attempt_number"),
            "provider": self.name,
            "workspace_path": workspace_path,
            "output_requested_as_file": bool(context.get("output_requested_as_file")),
            "requested_output_format": context.get("requested_output_format"),
        }

    async def _write_debug_report(
        self,
        exc: NoDriverProviderError,
        debug_context: dict[str, Any],
    ) -> Path | None:
        task_id = debug_context.get("task_id")
        run_id = debug_context.get("run_id")
        workspace_path = debug_context.get("workspace_path")
        if not task_id or not run_id or workspace_path is None:
            return None

        debug_dir = Path(workspace_path) / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = await self._maybe_save_screenshot(debug_dir)
        payload: dict[str, Any] = {
            "task_id": task_id,
            "run_id": run_id,
            "agent_id": debug_context.get("agent_id"),
            "step_id": debug_context.get("step_id"),
            "agent_task_id": debug_context.get("agent_task_id"),
            "stage": exc.stage,
            "provider": self.name,
            "error_code": exc.error_code,
            "message": str(exc),
            "url": exc.url,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if exc.selector:
            payload["selector"] = exc.selector
        if exc.page_title:
            payload["page_title"] = exc.page_title
        if exc.details:
            payload["details"] = exc.details
            for key in (
                "ready_state",
                "textarea_count",
                "contenteditable_count",
                "textbox_count",
                "candidate_count",
                "selectors_tried",
                "visible_candidates",
                "activeElement",
                "outerHTML",
                "dom_probe_summary",
                "attempts",
                "method",
            ):
                if key in exc.details:
                    payload[key] = exc.details[key]
        if screenshot_path is not None:
            payload["screenshot_path"] = str(screenshot_path)

        report_path = debug_dir / "nodriver_error.json"
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return report_path

    async def _maybe_save_screenshot(self, debug_dir: Path) -> Path | None:
        if not self.settings.nodriver_debug_screenshots:
            return None
        session = getattr(self.client, "session", None)
        tab = getattr(session, "tab", None)
        if tab is None:
            return None

        screenshot_path = debug_dir / "nodriver_error.png"
        for method_name in ("save_screenshot", "get_screenshot_as_file"):
            method = getattr(tab, method_name, None)
            if method is None:
                continue
            result = method(str(screenshot_path))
            if asyncio.iscoroutine(result):
                await result
            return screenshot_path
        return None

    def _log_stage(self, stage: str, debug_context: dict[str, Any]) -> None:
        logger.info(
            stage,
            extra={
                "task_id": debug_context.get("task_id"),
                "run_id": debug_context.get("run_id"),
                "agent_id": debug_context.get("agent_id"),
                "step_id": debug_context.get("step_id"),
                "stage": stage,
                "provider": self.name,
            },
        )
