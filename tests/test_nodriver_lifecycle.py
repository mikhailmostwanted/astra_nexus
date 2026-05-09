import os
from pathlib import Path

import pytest

from astra_nexus.brain.nodriver.exceptions import NoDriverProfileLockedError
from astra_nexus.brain.nodriver.lifecycle import (
    CHROME_LOCK_FILES,
    NoDriverLifecycleManager,
    ProcessInfo,
)
from astra_nexus.config.settings import Settings


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        nodriver_user_data_dir=tmp_path / "data/browser_profiles/default",
    )


def test_lifecycle_resolves_user_data_dir_to_absolute_path(tmp_path: Path) -> None:
    manager = NoDriverLifecycleManager(make_settings(tmp_path), context="smoke")

    assert manager.user_data_dir.is_absolute()
    assert manager.user_data_dir == (tmp_path / "data/browser_profiles/default").resolve()
    assert manager.lock_path == (tmp_path / "data/runtime/nodriver/default.lock").resolve()


def test_lifecycle_creates_and_releases_process_lock(tmp_path: Path) -> None:
    manager = NoDriverLifecycleManager(make_settings(tmp_path), context="login")

    lock_info = manager.acquire()

    assert manager.user_data_dir.exists()
    assert manager.lock_path.exists()
    assert lock_info.pid == os.getpid()
    assert lock_info.context == "login"
    assert lock_info.user_data_dir == str(manager.user_data_dir)

    manager.release()

    assert not manager.lock_path.exists()


def test_lifecycle_removes_stale_lock_before_acquire(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    manager = NoDriverLifecycleManager(
        settings,
        context="provider",
        is_pid_alive=lambda pid: False,
    )
    manager.runtime_dir.mkdir(parents=True)
    manager.lock_path.write_text(
        '{"pid": 999999, "started_at": "2026-01-01T00:00:00+00:00", '
        '"user_data_dir": "/tmp/old", "context": "smoke"}',
        encoding="utf-8",
    )

    manager.acquire()

    assert manager.lock_path.exists()
    assert manager.read_lock().pid == os.getpid()

    manager.release()


def test_lifecycle_blocks_live_lock(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    first = NoDriverLifecycleManager(settings, context="login")
    first.acquire()
    second = NoDriverLifecycleManager(
        settings,
        context="smoke",
        is_pid_alive=lambda pid: True,
    )

    with pytest.raises(NoDriverProfileLockedError) as exc:
        second.acquire()

    assert exc.value.status == "profile_locked"
    assert str(first.read_lock().pid) in str(exc.value)

    first.release()


def test_lifecycle_blocks_live_chrome_process_without_lock(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    manager = NoDriverLifecycleManager(
        settings,
        context="provider",
        find_profile_processes=lambda _: [
            ProcessInfo(pid=12345, command="Chrome --user-data-dir=/tmp/profile")
        ],
    )

    with pytest.raises(NoDriverProfileLockedError) as exc:
        manager.acquire()

    assert "12345" in str(exc.value)
    assert not manager.lock_path.exists()


def test_clean_removes_only_safe_files_and_keeps_profile_data(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    manager = NoDriverLifecycleManager(
        settings,
        context="clean",
        find_profile_processes=lambda _: [],
    )
    manager.user_data_dir.mkdir(parents=True)
    for filename in CHROME_LOCK_FILES:
        (manager.user_data_dir / filename).write_text("lock", encoding="utf-8")
    cookies = manager.user_data_dir / "Cookies"
    cookies.write_text("session", encoding="utf-8")

    report = manager.clean()

    assert sorted(report.removed_chrome_lock_files) == sorted(CHROME_LOCK_FILES)
    assert cookies.exists()
    assert manager.user_data_dir.exists()


def test_clean_does_not_remove_chrome_locks_when_profile_has_live_process(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    manager = NoDriverLifecycleManager(
        settings,
        context="clean",
        find_profile_processes=lambda _: [
            ProcessInfo(pid=12345, command="Chrome --user-data-dir=/tmp/profile")
        ],
    )
    manager.user_data_dir.mkdir(parents=True)
    lock_file = manager.user_data_dir / "SingletonLock"
    lock_file.write_text("lock", encoding="utf-8")

    report = manager.clean()

    assert report.live_profile_processes[0].pid == 12345
    assert lock_file.exists()


def test_cleanup_after_start_failure_releases_owned_lock_and_removes_chrome_locks(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    manager = NoDriverLifecycleManager(
        settings,
        context="provider",
        find_profile_processes=lambda _: [],
    )
    manager.acquire()
    for filename in CHROME_LOCK_FILES:
        (manager.user_data_dir / filename).write_text("lock", encoding="utf-8")

    report = manager.cleanup_after_start_failure()

    assert manager.read_lock() is None
    assert sorted(report.removed_chrome_lock_files) == sorted(CHROME_LOCK_FILES)
    assert manager.user_data_dir.exists()


def test_cleanup_after_start_failure_keeps_chrome_locks_for_live_profile_process(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    live_processes: list[ProcessInfo] = []
    manager = NoDriverLifecycleManager(
        settings,
        context="provider",
        find_profile_processes=lambda _: live_processes,
    )
    manager.acquire()
    lock_file = manager.user_data_dir / "SingletonLock"
    lock_file.write_text("lock", encoding="utf-8")
    live_processes.append(ProcessInfo(pid=12345, command="Chrome --user-data-dir=/tmp/profile"))

    report = manager.cleanup_after_start_failure()

    assert manager.read_lock() is None
    assert report.live_profile_processes[0].pid == 12345
    assert lock_file.exists()


def test_cleanup_after_start_failure_terminates_only_new_profile_processes(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    live_pids = {100, 200}
    terminated_pids: list[int] = []
    manager = NoDriverLifecycleManager(
        settings,
        context="provider",
        is_pid_alive=lambda pid: pid in live_pids,
        find_profile_processes=lambda _: [
            ProcessInfo(pid=pid, command="Chrome --user-data-dir=/tmp/profile")
            for pid in sorted(live_pids)
        ],
        terminate_process=lambda pid: (terminated_pids.append(pid), live_pids.discard(pid)),
    )
    manager.user_data_dir.mkdir(parents=True)
    (manager.user_data_dir / "SingletonSocket").write_text("lock", encoding="utf-8")

    report = manager.cleanup_after_start_failure(
        previous_profile_process_pids={100},
        terminate_grace_seconds=0,
    )

    assert terminated_pids == [200]
    assert report.terminated_profile_processes == [
        ProcessInfo(pid=200, command="Chrome --user-data-dir=/tmp/profile")
    ]
    assert report.live_profile_processes == [
        ProcessInfo(pid=100, command="Chrome --user-data-dir=/tmp/profile")
    ]
    assert (manager.user_data_dir / "SingletonSocket").exists()
