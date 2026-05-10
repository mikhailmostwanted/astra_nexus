from __future__ import annotations

import asyncio
import inspect
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from astra_nexus.brain.nodriver.artifact_detector import ArtifactCandidate

PARTIAL_DOWNLOAD_SUFFIXES = (
    ".crdownload",
    ".download",
    ".partial",
    ".part",
    ".tmp",
)


@dataclass(frozen=True)
class DownloadWaitResult:
    success: bool
    path: Path | None = None
    size_bytes: int = 0
    extension: str | None = None
    reason: str = ""
    partial_files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "path": str(self.path) if self.path is not None else None,
            "size_bytes": self.size_bytes,
            "extension": self.extension,
            "reason": self.reason,
            "partial_files": self.partial_files,
        }


@dataclass(frozen=True)
class RequestedFileDownloadResult:
    success: bool
    path: Path | None = None
    filename: str | None = None
    size_bytes: int = 0
    extension: str | None = None
    reason: str = ""
    candidate: dict[str, Any] | None = None
    downloads_dir: Path | None = None
    partial_files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "path": str(self.path) if self.path is not None else None,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "extension": self.extension,
            "reason": self.reason,
            "candidate": self.candidate,
            "downloads_dir": str(self.downloads_dir) if self.downloads_dir is not None else None,
            "partial_files": self.partial_files,
        }


class NoDriverDownloadManager:
    def __init__(
        self,
        *,
        downloads_dir: Path,
        requested_dir: Path,
        min_size_bytes: int = 1,
    ) -> None:
        self.downloads_dir = Path(downloads_dir)
        self.requested_dir = Path(requested_dir)
        self.min_size_bytes = max(0, int(min_size_bytes))

    def snapshot_files(self) -> set[Path]:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        return {path for path in self.downloads_dir.iterdir() if path.is_file()}

    async def download_candidate(
        self,
        *,
        tab: Any,
        candidate: ArtifactCandidate,
        expected_extension: str | None,
        timeout_seconds: float = 60.0,
    ) -> RequestedFileDownloadResult:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.requested_dir.mkdir(parents=True, exist_ok=True)
        before_files = self.snapshot_files()
        await self._set_download_path(tab)
        clicked = await self._trigger_download(tab=tab, candidate=candidate)
        if not clicked:
            return RequestedFileDownloadResult(
                success=False,
                reason="download_trigger_failed",
                candidate=candidate.as_dict(),
                downloads_dir=self.downloads_dir,
            )
        wait_result = await self.wait_for_completed_file(
            before_files=before_files,
            expected_extension=expected_extension or candidate.extension,
            timeout_seconds=timeout_seconds,
        )
        if not wait_result.success or wait_result.path is None:
            return RequestedFileDownloadResult(
                success=False,
                reason=wait_result.reason or "download_not_completed",
                candidate=candidate.as_dict(),
                downloads_dir=self.downloads_dir,
                partial_files=wait_result.partial_files,
            )
        final_path = self._move_to_requested_dir(
            wait_result.path,
            preferred_filename=candidate.filename,
        )
        size_bytes = final_path.stat().st_size
        extension = final_path.suffix.lower().lstrip(".") or wait_result.extension
        return RequestedFileDownloadResult(
            success=True,
            path=final_path,
            filename=final_path.name,
            size_bytes=size_bytes,
            extension=extension,
            candidate=candidate.as_dict(),
            downloads_dir=self.downloads_dir,
        )

    async def wait_for_completed_file(
        self,
        *,
        before_files: set[Path],
        expected_extension: str | None,
        timeout_seconds: float = 60.0,
        stable_seconds: float = 0.5,
    ) -> DownloadWaitResult:
        started_at = asyncio.get_running_loop().time()
        deadline = started_at + max(0.0, float(timeout_seconds))
        expected_extension = _normalize_extension(expected_extension)
        last_sizes: dict[Path, tuple[int, float]] = {}
        last_partial_files: list[str] = []

        while True:
            files = self.snapshot_files()
            partial_files = sorted(str(path) for path in files if _is_partial_download(path))
            last_partial_files = partial_files
            candidates = [
                path
                for path in files
                if path not in before_files and path.is_file() and not _is_partial_download(path)
            ]
            if expected_extension:
                candidates = [
                    path
                    for path in candidates
                    if path.suffix.lower().lstrip(".") == expected_extension
                ]
            for path in sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True):
                try:
                    size = path.stat().st_size
                except FileNotFoundError:
                    continue
                if size < self.min_size_bytes:
                    continue
                previous_size, first_seen = last_sizes.get(
                    path, (size, asyncio.get_running_loop().time())
                )
                now = asyncio.get_running_loop().time()
                if size != previous_size:
                    last_sizes[path] = (size, now)
                    continue
                if now - first_seen >= max(0.0, stable_seconds):
                    return DownloadWaitResult(
                        success=True,
                        path=path,
                        size_bytes=size,
                        extension=path.suffix.lower().lstrip(".") or None,
                        partial_files=partial_files,
                    )
                last_sizes[path] = (size, first_seen)

            if asyncio.get_running_loop().time() >= deadline:
                reason = "partial_download_left" if last_partial_files else "download_timeout"
                return DownloadWaitResult(
                    success=False,
                    reason=reason,
                    partial_files=last_partial_files,
                )
            await asyncio.sleep(0.1)

    async def _set_download_path(self, tab: Any) -> None:
        set_download_path = getattr(tab, "set_download_path", None)
        if set_download_path is None:
            return
        result = set_download_path(self.downloads_dir)
        if inspect.isawaitable(result):
            await result

    async def _trigger_download(self, *, tab: Any, candidate: ArtifactCandidate) -> bool:
        if candidate.button_id:
            selector = f'[data-astra-artifact-candidate-id="{candidate.button_id}"]'
            element = await _maybe_await(tab.query_selector(selector))
            if element is not None:
                await _maybe_await(element.click())
                return True
        if candidate.download_url:
            download_file = getattr(tab, "download_file", None)
            if download_file is not None:
                filename = candidate.filename or Path(candidate.download_url).name or None
                await _maybe_await(download_file(candidate.download_url, filename=filename))
                return True
        return False

    def _move_to_requested_dir(self, path: Path, *, preferred_filename: str | None) -> Path:
        filename = Path(preferred_filename or path.name).name or path.name
        destination = self.requested_dir / filename
        if destination.exists() and destination.resolve() != path.resolve():
            destination = _dedupe_path(destination)
        if path.resolve() == destination.resolve():
            return destination
        shutil.move(str(path), str(destination))
        return destination


def requested_file_dirs(workspace_path: Path) -> tuple[Path, Path]:
    requested_dir = Path(workspace_path) / "requested_files"
    downloads_dir = requested_dir / "downloads"
    return requested_dir, downloads_dir


def _is_partial_download(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in PARTIAL_DOWNLOAD_SUFFIXES)


def _normalize_extension(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower().lstrip(".")
    if normalized == "unknown":
        return None
    return normalized or None


def _dedupe_path(path: Path) -> Path:
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 10_000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not create unique file path for {path}")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
