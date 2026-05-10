from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from astra_nexus.team.attachments import (
    attachment_from_payload,
    attachment_payload,
    attachments_markdown,
    save_attachments_to_workspace,
)
from astra_nexus.team.dialogue import (
    dialogue_markdown,
    dialogue_transcript_payload,
    dialogue_turn_from_payload,
)
from astra_nexus.team.execution_plan import (
    TeamExecutionMode,
    execution_plan_from_payload,
    execution_plan_payload,
    execution_timeline_markdown,
)
from astra_nexus.team.messages import TeamMessage, TeamMessageChannel, TeamMessageType
from astra_nexus.team.models import (
    AgentResult,
    AgentRole,
    AgentTask,
    AgentTaskStatus,
    RunEvent,
    RunEventType,
    RunStatus,
    TeamRun,
)
from astra_nexus.team.profiles import default_profiles_by_role
from astra_nexus.team.review_protocol import (
    final_package_from_payload,
    final_package_payload,
    quality_criterion_from_payload,
    quality_criterion_payload,
    review_decision_from_payload,
    review_decision_payload,
    review_note_from_payload,
    review_note_payload,
    review_protocol_markdown,
    revision_request_from_payload,
    revision_request_payload,
    task_brief_from_payload,
    task_brief_payload,
)


class TeamRunWorkspace:
    def __init__(self, root_path: Path | str = "data/team_runs") -> None:
        self.root_path = Path(root_path)

    def save(self, run: TeamRun) -> Path:
        run_path = self.root_path / run.id
        agent_results_path = run_path / "agent_results"
        agent_results_path.mkdir(parents=True, exist_ok=True)
        save_attachments_to_workspace(run.attachments, run_path=run_path)

        self._write_json(run_path / "run.json", self._run_payload(run, run_path=run_path))
        self._write_json(run_path / "attachments.json", self._attachments_payload(run))
        self._write_json(run_path / "tasks.json", self._tasks_payload(run.tasks))
        self._write_json(run_path / "results.json", self._results_payload(run.results))
        self._write_json(run_path / "events.json", self._events_payload(run.events))
        self._write_json(run_path / "messages.json", self._messages_payload(run.messages))
        self._write_json(run_path / "task_brief.json", task_brief_payload(run.task_brief))
        self._write_json(
            run_path / "quality_criteria.json",
            [quality_criterion_payload(criterion) for criterion in run.quality_criteria],
        )
        self._write_json(
            run_path / "review_notes.json",
            [review_note_payload(note) for note in run.review_notes],
        )
        self._write_json(
            run_path / "revision_requests.json",
            [revision_request_payload(request) for request in run.revision_requests],
        )
        self._write_json(
            run_path / "review_decision.json",
            review_decision_payload(run.review_decision),
        )
        self._write_json(
            run_path / "final_package.json",
            final_package_payload(run.final_package),
        )
        if run.execution_plan is not None:
            self._write_json(
                run_path / "execution_plan.json",
                execution_plan_payload(run.execution_plan),
            )
        self._write_json(
            run_path / "team_chat.json",
            dialogue_transcript_payload(run.dialogue_turns, run_id=run.id),
        )
        self._write_events(run_path / "events.jsonl", run.events)
        (run_path / "messages.md").write_text(
            self._messages_markdown(run.messages),
            encoding="utf-8",
        )
        (run_path / "team_chat.md").write_text(
            dialogue_markdown(run.dialogue_turns),
            encoding="utf-8",
        )
        (run_path / "execution_timeline.md").write_text(
            execution_timeline_markdown(run),
            encoding="utf-8",
        )
        (run_path / "review_protocol.md").write_text(
            review_protocol_markdown(run),
            encoding="utf-8",
        )
        (run_path / "final.md").write_text(run.final_text or "", encoding="utf-8")
        (run_path / "attachments.md").write_text(
            attachments_markdown(run.attachments),
            encoding="utf-8",
        )

        tasks_by_role = {task.profile.role: task for task in run.tasks}
        results_by_role = {result.profile.role: result for result in run.results}
        for role in AgentRole:
            task = tasks_by_role.get(role)
            result = results_by_role.get(role)
            (agent_results_path / f"{role.value}.md").write_text(
                self._agent_result_markdown(run=run, role=role, task=task, result=result),
                encoding="utf-8",
            )

        return run_path

    def load(self, run_id: str) -> TeamRun:
        run_path = self.root_path / run_id
        run_payload = self._read_json(run_path / "run.json")
        tasks_payload = self._read_json(run_path / "tasks.json")
        results_payload = self._read_json(run_path / "results.json")
        events_payload = self._read_json(run_path / "events.json")
        attachments_payload = (
            self._read_json(run_path / "attachments.json")
            if (run_path / "attachments.json").exists()
            else {"attachments": []}
        )
        messages_payload = (
            self._read_json(run_path / "messages.json")
            if (run_path / "messages.json").exists()
            else []
        )
        dialogue_payload = (
            self._read_json(run_path / "team_chat.json")
            if (run_path / "team_chat.json").exists()
            else {"turns": []}
        )
        execution_plan_payload_data = (
            self._read_json(run_path / "execution_plan.json")
            if (run_path / "execution_plan.json").exists()
            else None
        )
        task_brief_payload_data = (
            self._read_json(run_path / "task_brief.json")
            if (run_path / "task_brief.json").exists()
            else None
        )
        quality_criteria_payload_data = (
            self._read_json(run_path / "quality_criteria.json")
            if (run_path / "quality_criteria.json").exists()
            else []
        )
        review_notes_payload_data = (
            self._read_json(run_path / "review_notes.json")
            if (run_path / "review_notes.json").exists()
            else []
        )
        revision_requests_payload_data = (
            self._read_json(run_path / "revision_requests.json")
            if (run_path / "revision_requests.json").exists()
            else []
        )
        review_decision_payload_data = (
            self._read_json(run_path / "review_decision.json")
            if (run_path / "review_decision.json").exists()
            else None
        )
        final_package_payload_data = (
            self._read_json(run_path / "final_package.json")
            if (run_path / "final_package.json").exists()
            else None
        )
        profiles = default_profiles_by_role()

        run = TeamRun(
            id=run_payload["run_id"],
            user_task=run_payload["user_task"],
            status=RunStatus(run_payload["status"]),
            final_text=run_payload.get("final_result"),
            error_message=run_payload.get("error_message"),
            created_at=self._parse_datetime(run_payload.get("created_at")),
            started_at=self._parse_optional_datetime(run_payload.get("started_at")),
            completed_at=self._parse_optional_datetime(run_payload.get("finished_at")),
        )
        runtime_metadata = run_payload.get("runtime_metadata")
        if not isinstance(runtime_metadata, dict):
            runtime_metadata = {}
        run.runtime_metadata = {
            **runtime_metadata,
            **{
                key: run_payload.get(key)
                for key in (
                    "session_id",
                    "chat_id",
                    "job_id",
                    "provider",
                    "intent",
                    "execution_mode",
                )
                if run_payload.get(key) is not None
            },
        }
        run.execution_mode = TeamExecutionMode(run_payload.get("execution_mode", "sequential"))
        run.review_protocol_enabled = run_payload.get("review_protocol_enabled", True)
        run.revision_loops_count = run_payload.get("revision_loops_count", 0)
        if execution_plan_payload_data is not None:
            run.execution_plan = execution_plan_from_payload(execution_plan_payload_data)
        run.task_brief = task_brief_from_payload(task_brief_payload_data)
        run.quality_criteria = [
            quality_criterion_from_payload(criterion)
            for criterion in quality_criteria_payload_data or []
        ]
        run.review_notes = [
            review_note_from_payload(note) for note in review_notes_payload_data or []
        ]
        run.revision_requests = [
            revision_request_from_payload(request)
            for request in revision_requests_payload_data or []
        ]
        run.review_decision = review_decision_from_payload(review_decision_payload_data)
        run.final_package = final_package_from_payload(final_package_payload_data)
        run.attachments = [
            attachment_from_payload(attachment)
            for attachment in attachments_payload.get("attachments", [])
        ]
        run.tasks = [
            AgentTask(
                id=task["task_id"],
                run_id=run.id,
                profile=profiles[AgentRole(task["role"])],
                user_task=run.user_task,
                status=AgentTaskStatus(task["status"]),
                created_at=self._parse_datetime(task["created_at"]),
                started_at=self._parse_optional_datetime(task.get("started_at")),
                completed_at=self._parse_optional_datetime(task.get("finished_at")),
                error_message=task.get("error_message"),
                dependencies=tuple(AgentRole(role) for role in task.get("dependencies", [])),
                execution_step_id=task.get("execution_step_id"),
                execution_mode=task.get("execution_mode"),
            )
            for task in tasks_payload
        ]
        run.results = [
            AgentResult(
                id=result["result_id"],
                run_id=run.id,
                task_id=result["task_id"],
                profile=profiles[AgentRole(result["role"])],
                content=result["content"],
                created_at=self._parse_datetime(result["created_at"]),
                metadata=result.get("metadata", {}),
            )
            for result in results_payload
        ]
        run.events = [
            RunEvent(
                id=event["event_id"],
                run_id=run.id,
                type=RunEventType(event["event_type"]),
                message=event["message"],
                agent_role=AgentRole(event["agent_role"]) if event.get("agent_role") else None,
                agent_task_id=event.get("agent_task_id"),
                payload=event.get("details", {}),
                created_at=self._parse_datetime(event["timestamp"]),
            )
            for event in events_payload
        ]
        run.messages = [
            TeamMessage(
                id=message["message_id"],
                run_id=run.id,
                channel=TeamMessageChannel(message["channel"]),
                type=TeamMessageType(message["message_type"]),
                text=message["text"],
                author_name=message.get("author_name"),
                author_role=AgentRole(message["author_role"])
                if message.get("author_role")
                else None,
                event_id=message.get("event_id"),
                agent_task_id=message.get("agent_task_id"),
                metadata=message.get("metadata", {}),
                created_at=self._parse_datetime(message["timestamp"]),
            )
            for message in messages_payload
        ]
        run.dialogue_turns = [
            dialogue_turn_from_payload(turn) for turn in dialogue_payload.get("turns", [])
        ]
        return run

    def _run_payload(self, run: TeamRun, *, run_path: Path) -> dict[str, Any]:
        metadata = dict(run.runtime_metadata)
        return {
            "run_id": run.id,
            "status": run.status.value,
            "user_task": run.user_task,
            "title": _title_from_task(run.user_task),
            "created_at": self._serialize_datetime(run.created_at),
            "started_at": self._serialize_datetime(run.started_at),
            "finished_at": self._serialize_datetime(run.completed_at),
            "final_result": run.final_text,
            "error_message": run.error_message,
            "workspace_path": str(run_path),
            "session_id": metadata.get("session_id"),
            "chat_id": metadata.get("chat_id"),
            "job_id": metadata.get("job_id"),
            "provider": metadata.get("provider"),
            "intent": metadata.get("intent"),
            "runtime_metadata": metadata,
            "attachments_count": len(run.attachments),
            "dialogue_turns_count": len(run.dialogue_turns),
            "execution_mode": TeamExecutionMode(run.execution_mode).value,
            "review_protocol_enabled": run.review_protocol_enabled,
            "revision_loops_count": run.revision_loops_count,
            "review_notes_count": len(run.review_notes),
            "final_approved": run.review_decision.approved
            if run.review_decision is not None
            else None,
            "agents": self._agent_summaries(run),
        }

    def _attachments_payload(self, run: TeamRun) -> dict[str, Any]:
        return {
            "run_id": run.id,
            "attachments": [attachment_payload(attachment) for attachment in run.attachments],
        }

    def _agent_summaries(self, run: TeamRun) -> list[dict[str, Any]]:
        results_by_task_id = {result.task_id: result for result in run.results}
        summaries = []
        for task in run.tasks:
            result = results_by_task_id.get(task.id)
            summaries.append(
                {
                    "role": task.profile.role.value,
                    "display_name": task.profile.display_name,
                    "short_description": task.profile.short_description,
                    "task_id": task.id,
                    "task_status": task.status.value,
                    "result_id": result.id if result is not None else None,
                    "result_preview": result.content[:240] if result is not None else None,
                    "started_at": self._serialize_datetime(task.started_at),
                    "finished_at": self._serialize_datetime(task.completed_at),
                    "error_message": task.error_message,
                    "dependencies": [role.value for role in task.dependencies],
                    "execution_step_id": task.execution_step_id,
                    "execution_mode": task.execution_mode,
                }
            )
        return summaries

    def _tasks_payload(self, tasks: list[AgentTask]) -> list[dict[str, Any]]:
        return [
            {
                "task_id": task.id,
                "run_id": task.run_id,
                "role": task.profile.role.value,
                "display_name": task.profile.display_name,
                "status": task.status.value,
                "created_at": self._serialize_datetime(task.created_at),
                "started_at": self._serialize_datetime(task.started_at),
                "finished_at": self._serialize_datetime(task.completed_at),
                "error_message": task.error_message,
                "dependencies": [role.value for role in task.dependencies],
                "execution_step_id": task.execution_step_id,
                "execution_mode": task.execution_mode,
            }
            for task in tasks
        ]

    def _results_payload(self, results: list[AgentResult]) -> list[dict[str, Any]]:
        return [
            {
                "result_id": result.id,
                "run_id": result.run_id,
                "task_id": result.task_id,
                "role": result.profile.role.value,
                "display_name": result.profile.display_name,
                "content": result.content,
                "created_at": self._serialize_datetime(result.created_at),
                "dependencies": result.metadata.get("dependencies", []),
                "execution_step_id": result.metadata.get("execution_step_id"),
                "execution_mode": result.metadata.get("execution_mode"),
                "metadata": result.metadata,
            }
            for result in results
        ]

    def _events_payload(self, events: list[RunEvent]) -> list[dict[str, Any]]:
        return [self._event_payload(event) for event in events]

    def _messages_payload(self, messages: list[TeamMessage]) -> list[dict[str, Any]]:
        return [self._message_payload(message) for message in messages]

    def _write_events(self, path: Path, events: list[RunEvent]) -> None:
        lines = [json.dumps(self._event_payload(event), ensure_ascii=False) for event in events]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def _event_payload(self, event: RunEvent) -> dict[str, Any]:
        return {
            "event_id": event.id,
            "timestamp": self._serialize_datetime(event.created_at),
            "event_type": event.type.value,
            "run_id": event.run_id,
            "agent_role": event.agent_role.value if event.agent_role is not None else None,
            "agent_task_id": event.agent_task_id,
            "message": event.message,
            "details": event.payload,
        }

    def _message_payload(self, message: TeamMessage) -> dict[str, Any]:
        return {
            "message_id": message.id,
            "timestamp": self._serialize_datetime(message.created_at),
            "run_id": message.run_id,
            "channel": message.channel.value,
            "message_type": message.type.value,
            "author_name": message.author_name,
            "author_role": message.author_role.value if message.author_role is not None else None,
            "event_id": message.event_id,
            "agent_task_id": message.agent_task_id,
            "text": message.text,
            "metadata": message.metadata,
        }

    def _messages_markdown(self, messages: list[TeamMessage]) -> str:
        main_messages = [
            message for message in messages if message.channel == TeamMessageChannel.MAIN_CHAT
        ]
        log_messages = [
            message for message in messages if message.channel == TeamMessageChannel.LOG_CHAT
        ]
        debug_messages = [
            message for message in messages if message.channel == TeamMessageChannel.DEBUG
        ]
        sections = ["# Team Messages", ""]
        sections.extend(self._message_channel_markdown("Main Chat", main_messages))
        sections.extend(self._message_channel_markdown("Log Chat", log_messages))
        if debug_messages:
            sections.extend(self._message_channel_markdown("Debug", debug_messages))
        return "\n".join(sections).rstrip() + "\n"

    def _message_channel_markdown(
        self,
        title: str,
        messages: list[TeamMessage],
    ) -> list[str]:
        sections = [f"## {title}", ""]
        for message in messages:
            author = message.author_name or "Лог"
            timestamp = self._serialize_datetime(message.created_at)
            sections.extend(
                [
                    f"- `{timestamp}` [{author}] {message.text}",
                ]
            )
        sections.append("")
        return sections

    def _agent_result_markdown(
        self,
        *,
        run: TeamRun,
        role: AgentRole,
        task: AgentTask | None,
        result: AgentResult | None,
    ) -> str:
        profile = (
            task.profile if task is not None else result.profile if result is not None else None
        )
        display_name = profile.display_name if profile is not None else role.value
        status = task.status.value if task is not None else "not_started"
        content = result.content if result is not None else ""
        prompt_section = self._prompt_markdown(result) if result is not None else ""

        sections = [
            f"# {role.value}",
            "",
            f"Имя: {display_name}",
            f"Роль: {role.value}",
            "",
            "## Задача",
            "",
            run.user_task,
            "",
            "## Статус",
            "",
            status,
            "",
            "## Результат",
            "",
            content,
            "",
        ]
        if prompt_section:
            sections.extend([prompt_section, ""])
        return "\n".join(sections)

    def _prompt_markdown(self, result: AgentResult) -> str:
        prompt = result.metadata.get("prompt")
        if not isinstance(prompt, dict):
            return ""
        system_prompt = prompt.get("system_prompt", "")
        user_prompt = prompt.get("user_prompt", "")
        if not system_prompt and not user_prompt:
            return ""
        return "\n".join(
            [
                "## Внутренний prompt",
                "",
                "### System",
                "",
                "```text",
                str(system_prompt),
                "```",
                "",
                "### User",
                "",
                "```text",
                str(user_prompt),
                "```",
            ]
        )

    def _write_json(self, path: Path, payload: Any) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _read_json(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def _serialize_datetime(self, value: Any) -> str | None:
        return value.isoformat() if value is not None else None

    def _parse_datetime(self, value: str | None) -> datetime:
        if value is None:
            raise ValueError("datetime value is required")
        return datetime.fromisoformat(value)

    def _parse_optional_datetime(self, value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None


def _title_from_task(task: str, *, limit: int = 96) -> str:
    title = " ".join(task.split())
    if len(title) <= limit:
        return title
    return f"{title[: limit - 1].rstrip()}..."
