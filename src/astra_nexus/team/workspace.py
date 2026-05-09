from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from astra_nexus.team.models import AgentResult, AgentRole, AgentTask, RunEvent, TeamRun


class TeamRunWorkspace:
    def __init__(self, root_path: Path | str = "data/team_runs") -> None:
        self.root_path = Path(root_path)

    def save(self, run: TeamRun) -> Path:
        run_path = self.root_path / run.id
        agent_results_path = run_path / "agent_results"
        agent_results_path.mkdir(parents=True, exist_ok=True)

        self._write_json(run_path / "run.json", self._run_payload(run))
        self._write_events(run_path / "events.jsonl", run.events)
        (run_path / "final.md").write_text(run.final_text or "", encoding="utf-8")

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

    def _run_payload(self, run: TeamRun) -> dict[str, Any]:
        return {
            "run_id": run.id,
            "status": run.status.value,
            "user_task": run.user_task,
            "created_at": self._serialize_datetime(run.created_at),
            "started_at": self._serialize_datetime(run.started_at),
            "finished_at": self._serialize_datetime(run.completed_at),
            "final_result": run.final_text,
            "error_message": run.error_message,
            "agents": self._agent_summaries(run),
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
                    "task_id": task.id,
                    "task_status": task.status.value,
                    "result_id": result.id if result is not None else None,
                    "result_preview": result.content[:240] if result is not None else None,
                    "started_at": self._serialize_datetime(task.started_at),
                    "finished_at": self._serialize_datetime(task.completed_at),
                    "error_message": task.error_message,
                }
            )
        return summaries

    def _write_events(self, path: Path, events: list[RunEvent]) -> None:
        lines = [json.dumps(self._event_payload(event), ensure_ascii=False) for event in events]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def _event_payload(self, event: RunEvent) -> dict[str, Any]:
        return {
            "timestamp": self._serialize_datetime(event.created_at),
            "event_type": event.type.value,
            "run_id": event.run_id,
            "agent_role": event.agent_role.value if event.agent_role is not None else None,
            "message": event.message,
            "details": event.payload,
        }

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

        return "\n".join(
            [
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
        )

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _serialize_datetime(self, value: Any) -> str | None:
        return value.isoformat() if value is not None else None
