from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from astra_nexus.brain.nodriver.exceptions import NoDriverProfileLockedError
from astra_nexus.config.settings import Settings

CHROME_LOCK_FILES = (
    "SingletonLock",
    "SingletonSocket",
    "SingletonCookie",
    "DevToolsActivePort",
)


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    command: str


@dataclass(frozen=True)
class LockInfo:
    pid: int
    started_at: str
    user_data_dir: str
    context: str
    command: str

    @classmethod
    def create(cls, *, user_data_dir: Path, context: str) -> LockInfo:
        return cls(
            pid=os.getpid(),
            started_at=datetime.now(UTC).isoformat(),
            user_data_dir=str(user_data_dir),
            context=context,
            command=context,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LockInfo:
        context = str(payload.get("context") or payload.get("command") or "unknown")
        return cls(
            pid=int(payload["pid"]),
            started_at=str(payload.get("started_at", "")),
            user_data_dir=str(payload["user_data_dir"]),
            context=context,
            command=str(payload.get("command") or context),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "started_at": self.started_at,
            "user_data_dir": self.user_data_dir,
            "context": self.context,
            "command": self.command,
        }


@dataclass(frozen=True)
class LifecycleSnapshot:
    user_data_dir: Path
    lock_path: Path
    user_data_dir_exists: bool
    lock_info: LockInfo | None
    lock_pid_alive: bool
    live_profile_processes: list[ProcessInfo] = field(default_factory=list)
    invalid_lock: bool = False

    @property
    def profile_locked(self) -> bool:
        return self.lock_pid_alive or bool(self.live_profile_processes)


@dataclass(frozen=True)
class CleanReport:
    user_data_dir: Path
    lock_path: Path
    lock_info: LockInfo | None
    lock_pid_alive: bool
    stale_lock_removed: bool
    invalid_lock_removed: bool
    removed_chrome_lock_files: list[str]
    live_profile_processes: list[ProcessInfo]


class NoDriverLifecycleManager:
    def __init__(
        self,
        settings: Settings,
        *,
        context: str,
        is_pid_alive: Callable[[int], bool] | None = None,
        find_profile_processes: Callable[[Path], list[ProcessInfo]] | None = None,
    ) -> None:
        self.settings = settings
        self.context = context
        self.user_data_dir = Path(settings.nodriver_user_data_dir).expanduser().resolve()
        self.runtime_dir = Path(settings.data_dir).expanduser().resolve() / "runtime" / "nodriver"
        self.lock_path = self.runtime_dir / f"{self._profile_lock_name()}.lock"
        self._is_pid_alive = is_pid_alive or pid_is_alive
        self._find_profile_processes = find_profile_processes or find_processes_using_profile
        self._owned_lock: LockInfo | None = None

    def acquire(self) -> LockInfo:
        if self._owned_lock is not None:
            return self._owned_lock

        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        while True:
            lock_info, invalid_lock = self._read_lock_with_state()
            if invalid_lock:
                self._unlink_lock_file()
                continue
            if lock_info is not None:
                if self._is_pid_alive(lock_info.pid):
                    raise NoDriverProfileLockedError(
                        pid=lock_info.pid,
                        context=lock_info.context,
                        user_data_dir=lock_info.user_data_dir,
                        lock_path=str(self.lock_path),
                    )
                self._unlink_lock_file()
                continue

            live_processes = self._find_profile_processes(self.user_data_dir)
            if live_processes:
                process = live_processes[0]
                raise NoDriverProfileLockedError(
                    pid=process.pid,
                    context="chrome",
                    user_data_dir=str(self.user_data_dir),
                    lock_path=str(self.lock_path),
                )

            self._remove_chrome_lock_files()
            new_lock = LockInfo.create(user_data_dir=self.user_data_dir, context=self.context)
            try:
                self._write_lock_atomically(new_lock)
            except FileExistsError:
                continue
            self._owned_lock = new_lock
            return new_lock

    def release(self) -> None:
        if self._owned_lock is None:
            return
        current = self.read_lock()
        if (
            current is not None
            and current.pid == self._owned_lock.pid
            and current.user_data_dir == self._owned_lock.user_data_dir
            and current.context == self._owned_lock.context
        ):
            self._unlink_lock_file()
        self._owned_lock = None

    def inspect(self) -> LifecycleSnapshot:
        lock_info, invalid_lock = self._read_lock_with_state()
        lock_pid_alive = bool(lock_info and self._is_pid_alive(lock_info.pid))
        live_processes = self._find_profile_processes(self.user_data_dir)
        return LifecycleSnapshot(
            user_data_dir=self.user_data_dir,
            lock_path=self.lock_path,
            user_data_dir_exists=self.user_data_dir.exists(),
            lock_info=lock_info,
            lock_pid_alive=lock_pid_alive,
            live_profile_processes=live_processes,
            invalid_lock=invalid_lock,
        )

    def clean(self) -> CleanReport:
        lock_info, invalid_lock = self._read_lock_with_state()
        lock_pid_alive = bool(lock_info and self._is_pid_alive(lock_info.pid))
        stale_lock_removed = False
        invalid_lock_removed = False

        if invalid_lock:
            self._unlink_lock_file()
            invalid_lock_removed = True
            lock_info = None
        elif lock_info is not None and not lock_pid_alive:
            self._unlink_lock_file()
            stale_lock_removed = True

        live_processes = self._find_profile_processes(self.user_data_dir)
        removed_chrome_lock_files: list[str] = []
        if not lock_pid_alive and not live_processes:
            removed_chrome_lock_files = self._remove_chrome_lock_files()

        return CleanReport(
            user_data_dir=self.user_data_dir,
            lock_path=self.lock_path,
            lock_info=lock_info,
            lock_pid_alive=lock_pid_alive,
            stale_lock_removed=stale_lock_removed,
            invalid_lock_removed=invalid_lock_removed,
            removed_chrome_lock_files=removed_chrome_lock_files,
            live_profile_processes=live_processes,
        )

    def read_lock(self) -> LockInfo | None:
        lock_info, _ = self._read_lock_with_state()
        return lock_info

    def _profile_lock_name(self) -> str:
        name = self.user_data_dir.name or "default"
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
        return name or "default"

    def _read_lock_with_state(self) -> tuple[LockInfo | None, bool]:
        if not self.lock_path.exists():
            return None, False
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
            return LockInfo.from_dict(payload), False
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None, True

    def _write_lock_atomically(self, lock_info: LockInfo) -> None:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        fd = os.open(self.lock_path, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(lock_info.as_dict(), file, ensure_ascii=False, indent=2)
                file.write("\n")
        except Exception:
            self._unlink_lock_file()
            raise

    def _unlink_lock_file(self) -> None:
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            return

    def _remove_chrome_lock_files(self) -> list[str]:
        removed: list[str] = []
        for filename in CHROME_LOCK_FILES:
            path = self.user_data_dir / filename
            if not path.exists() and not path.is_symlink():
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            removed.append(filename)
        return removed


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def find_processes_using_profile(user_data_dir: Path) -> list[ProcessInfo]:
    if os.name != "posix":
        return []

    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0:
        return []

    profile = str(user_data_dir.expanduser().resolve())
    processes: list[ProcessInfo] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        if _command_uses_profile(command, profile):
            processes.append(ProcessInfo(pid=pid, command=command))
    return processes


def _command_uses_profile(command: str, user_data_dir: str) -> bool:
    return (
        f"--user-data-dir={user_data_dir}" in command
        or f"--user-data-dir {user_data_dir}" in command
        or user_data_dir in command
    )
