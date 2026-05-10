from __future__ import annotations

import asyncio

from astra_nexus.brain.nodriver.download_manager import NoDriverDownloadManager


def test_download_manager_waits_until_partial_file_is_complete(tmp_path) -> None:
    async def scenario() -> None:
        downloads_dir = tmp_path / "downloads"
        downloads_dir.mkdir()
        manager = NoDriverDownloadManager(downloads_dir=downloads_dir, requested_dir=tmp_path)

        wait_task = asyncio.create_task(
            manager.wait_for_completed_file(
                before_files=set(),
                expected_extension="pdf",
                timeout_seconds=2,
                stable_seconds=0.05,
            )
        )
        partial_path = downloads_dir / "report.pdf.crdownload"
        partial_path.write_bytes(b"partial")
        await asyncio.sleep(0.1)
        final_path = downloads_dir / "report.pdf"
        partial_path.rename(final_path)
        final_path.write_bytes(b"%PDF-1.4\ncontent")

        result = await wait_task

        assert result.success is True
        assert result.path == final_path
        assert result.size_bytes == final_path.stat().st_size
        assert result.extension == "pdf"

    asyncio.run(scenario())
