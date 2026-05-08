from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TaskWorkspace:
    root: Path
    input_dir: Path
    drafts_dir: Path
    artifacts_dir: Path
    events_path: Path


class WorkspaceManager:
    def __init__(self, base_path: Path | str) -> None:
        self.base_path = Path(base_path)

    def create_for_task(self, task_id: str) -> TaskWorkspace:
        root = self.base_path / task_id
        input_dir = root / "input"
        drafts_dir = root / "drafts"
        artifacts_dir = root / "artifacts"
        events_path = root / "events.jsonl"

        for directory in (input_dir, drafts_dir, artifacts_dir):
            directory.mkdir(parents=True, exist_ok=True)
        events_path.touch(exist_ok=True)

        return TaskWorkspace(
            root=root,
            input_dir=input_dir,
            drafts_dir=drafts_dir,
            artifacts_dir=artifacts_dir,
            events_path=events_path,
        )

    def append_event(self, task_id: str, event: dict[str, Any]) -> None:
        workspace = self.create_for_task(task_id)
        with workspace.events_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, default=str))
            file.write("\n")
