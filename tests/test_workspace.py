from pathlib import Path

from astra_nexus.core.workspace import WorkspaceManager


def test_workspace_creates_expected_directories_and_event_log(tmp_path: Path) -> None:
    manager = WorkspaceManager(base_path=tmp_path)

    workspace = manager.create_for_task("task_123")
    manager.append_event("task_123", {"type": "created", "task_id": "task_123"})

    assert workspace.root == tmp_path / "task_123"
    assert workspace.input_dir.is_dir()
    assert workspace.drafts_dir.is_dir()
    assert workspace.artifacts_dir.is_dir()
    assert workspace.events_path.read_text(encoding="utf-8").strip().endswith('"task_123"}')
