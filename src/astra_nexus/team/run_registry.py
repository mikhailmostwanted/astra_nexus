from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from astra_nexus.config.settings import load_settings

TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}


@dataclass(frozen=True)
class TeamRunRegistryEntry:
    run_id: str
    status: str
    user_task: str
    workspace_path: Path
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    session_id: str | None = None
    chat_id: str | None = None
    job_id: str | None = None
    provider: str | None = None
    execution_mode: str | None = None
    final_result: str | None = None
    error_message: str | None = None
    corrupted: bool = False
    invalid: bool = False
    registry_error: str | None = None

    @property
    def title(self) -> str:
        return _preview_text(self.user_task, limit=80) if self.user_task else self.run_id

    @property
    def sort_time(self) -> datetime | None:
        return self.finished_at or self.started_at or self.created_at

    @property
    def is_valid(self) -> bool:
        return not self.corrupted and not self.invalid


class TeamRunRegistry:
    def __init__(self, root_path: Path | str = "data/team_runs") -> None:
        self.root_path = Path(root_path)

    def index(self) -> list[TeamRunRegistryEntry]:
        entries = [self._read_entry(path) for path in sorted(self.root_path.glob("*/run.json"))]
        return sorted(entries, key=self._sort_key, reverse=True)

    def find(self, run_id: str) -> TeamRunRegistryEntry | None:
        run_path = self.root_path / run_id / "run.json"
        if run_path.exists():
            return self._read_entry(run_path)
        for entry in self.index():
            if entry.run_id == run_id:
                return entry
        return None

    def latest_runs(
        self,
        *,
        session_id: str | None = None,
        chat_id: int | str | None = None,
        limit: int = 5,
        include_invalid: bool = False,
    ) -> list[TeamRunRegistryEntry]:
        entries = [
            entry
            for entry in self.index()
            if (include_invalid or entry.is_valid)
            and self._matches_session(entry, session_id=session_id, chat_id=chat_id)
        ]
        return entries[: max(0, limit)]

    def last_completed(
        self,
        *,
        session_id: str | None = None,
        chat_id: int | str | None = None,
    ) -> TeamRunRegistryEntry | None:
        return self.last_by_status("completed", session_id=session_id, chat_id=chat_id)

    def last_failed(
        self,
        *,
        session_id: str | None = None,
        chat_id: int | str | None = None,
    ) -> TeamRunRegistryEntry | None:
        return self.last_by_status("failed", session_id=session_id, chat_id=chat_id)

    def last_cancelled(
        self,
        *,
        session_id: str | None = None,
        chat_id: int | str | None = None,
    ) -> TeamRunRegistryEntry | None:
        return self.last_by_status("cancelled", session_id=session_id, chat_id=chat_id)

    def last_by_status(
        self,
        status: str,
        *,
        session_id: str | None = None,
        chat_id: int | str | None = None,
    ) -> TeamRunRegistryEntry | None:
        normalized = status.strip().lower()
        for entry in self.index():
            if not entry.is_valid:
                continue
            if not self._matches_session(entry, session_id=session_id, chat_id=chat_id):
                continue
            if entry.status == normalized:
                return entry
        return None

    def last_terminal_run(
        self,
        *,
        session_id: str | None = None,
        chat_id: int | str | None = None,
    ) -> TeamRunRegistryEntry | None:
        for entry in self.index():
            if not entry.is_valid:
                continue
            if not self._matches_session(entry, session_id=session_id, chat_id=chat_id):
                continue
            if entry.status in TERMINAL_RUN_STATUSES:
                return entry
        return None

    def _read_entry(self, run_json_path: Path) -> TeamRunRegistryEntry:
        workspace_path = run_json_path.parent
        try:
            payload = json.loads(run_json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return TeamRunRegistryEntry(
                run_id=workspace_path.name,
                status="corrupted",
                user_task="",
                workspace_path=workspace_path,
                corrupted=True,
                invalid=True,
                registry_error=str(exc),
            )

        if not isinstance(payload, dict):
            return TeamRunRegistryEntry(
                run_id=workspace_path.name,
                status="invalid",
                user_task="",
                workspace_path=workspace_path,
                invalid=True,
                registry_error="run.json root is not an object",
            )

        try:
            run_id = str(payload["run_id"])
            status = str(payload["status"]).strip().lower()
            user_task = str(payload.get("user_task") or "")
        except Exception as exc:
            return TeamRunRegistryEntry(
                run_id=str(payload.get("run_id") or workspace_path.name),
                status="invalid",
                user_task=str(payload.get("user_task") or ""),
                workspace_path=workspace_path,
                invalid=True,
                registry_error=str(exc),
            )

        metadata = payload.get("runtime_metadata") if isinstance(payload, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}

        return TeamRunRegistryEntry(
            run_id=run_id,
            status=status,
            user_task=user_task,
            workspace_path=workspace_path,
            created_at=_parse_datetime(payload.get("created_at")),
            started_at=_parse_datetime(payload.get("started_at")),
            finished_at=_parse_datetime(payload.get("finished_at")),
            session_id=_string_or_none(payload.get("session_id") or metadata.get("session_id")),
            chat_id=_string_or_none(payload.get("chat_id") or metadata.get("chat_id")),
            job_id=_string_or_none(payload.get("job_id") or metadata.get("job_id")),
            provider=_string_or_none(payload.get("provider") or metadata.get("provider")),
            execution_mode=_string_or_none(
                payload.get("execution_mode") or metadata.get("execution_mode")
            ),
            final_result=_string_or_none(payload.get("final_result")),
            error_message=_string_or_none(payload.get("error_message")),
        )

    def _matches_session(
        self,
        entry: TeamRunRegistryEntry,
        *,
        session_id: str | None,
        chat_id: int | str | None,
    ) -> bool:
        if session_id is not None and entry.session_id != str(session_id):
            return False
        if chat_id is not None and entry.chat_id != str(chat_id):
            return False
        return True

    def _sort_key(self, entry: TeamRunRegistryEntry) -> tuple[float, str]:
        timestamp = entry.sort_time.timestamp() if entry.sort_time is not None else 0.0
        return (timestamp, entry.run_id)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = load_settings()
    registry = TeamRunRegistry(args.workspace_root or settings.team_runs_dir)
    runs = registry.latest_runs(
        session_id=args.session_id,
        limit=args.limit,
        include_invalid=args.include_invalid,
    )
    if not runs:
        print("Сохранённых запусков пока нет.")
        return 0

    for entry in runs:
        print(_format_entry(entry))
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview persistent AI Team run registry.")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--include-invalid", action="store_true")
    parser.add_argument("--workspace-root", type=Path, default=None)
    return parser.parse_args(argv)


def _format_entry(entry: TeamRunRegistryEntry) -> str:
    finished = entry.finished_at.isoformat() if entry.finished_at is not None else "нет"
    created = entry.created_at.isoformat() if entry.created_at is not None else "нет"
    return "\n".join(
        [
            f"- {entry.status}: {entry.title}",
            f"  run_id: {entry.run_id}",
            f"  created: {created}",
            f"  finished: {finished}",
            f"  workspace: {entry.workspace_path}",
        ]
    )


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _preview_text(text: str, *, limit: int = 180) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1].rstrip()}..."


if __name__ == "__main__":
    raise SystemExit(main())
