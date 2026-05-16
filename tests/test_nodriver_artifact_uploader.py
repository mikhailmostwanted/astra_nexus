from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astra_nexus.brain.nodriver.artifact_uploader import (
    ArtifactUploader,
    NoDriverArtifactInputPromptBoxNotFoundError,
    NoDriverArtifactInputUploadButtonNotFoundError,
    NoDriverArtifactInputUploadTimeoutError,
)


@pytest.fixture
def mock_tab():
    tab = AsyncMock()
    tab.url = AsyncMock(return_value="https://chatgpt.com")
    tab.title = AsyncMock(return_value="ChatGPT")
    tab.get_content.return_value = "<html><body></body></html>"
    return tab


@pytest.mark.asyncio
async def test_upload_no_files(mock_tab):
    uploader = ArtifactUploader(mock_tab)
    assert await uploader.upload([]) is True
    mock_tab.query_selector.assert_not_called()


@pytest.mark.asyncio
async def test_upload_fails_if_no_composer(mock_tab):
    uploader = ArtifactUploader(mock_tab)

    with patch.object(
        ArtifactUploader, "_probe_upload_elements", new_callable=AsyncMock
    ) as mock_probe:
        mock_probe.return_value = {"composer_found": False}

        with pytest.raises(NoDriverArtifactInputPromptBoxNotFoundError):
            await uploader.upload([Path("test.txt")])


@pytest.mark.asyncio
async def test_upload_fails_if_no_attach_button(mock_tab):
    uploader = ArtifactUploader(mock_tab)

    with patch.object(
        ArtifactUploader, "_probe_upload_elements", new_callable=AsyncMock
    ) as mock_probe:
        mock_probe.return_value = {
            "composer_found": True,
            "file_input_selector": None,
            "attach_button_selector": None,
        }

        with pytest.raises(NoDriverArtifactInputUploadButtonNotFoundError):
            await uploader.upload([Path("test.txt")])


@pytest.mark.asyncio
async def test_upload_success_direct_input(mock_tab):
    uploader = ArtifactUploader(mock_tab)
    file_path = Path("test.txt")

    with patch.object(
        ArtifactUploader, "_probe_upload_elements", new_callable=AsyncMock
    ) as mock_probe:
        mock_probe.return_value = {
            "composer_found": True,
            "file_input_selector": "input[type='file']",
            "attach_button_selector": "button[attach]",
        }

        with patch.object(
            ArtifactUploader, "_probe_upload_status", new_callable=AsyncMock
        ) as mock_status:
            mock_status.return_value = {
                "upload_confirmed": True,
                "all_files_matched": True,
                "found_in_chips": ["test.txt"],
            }

            mock_input = AsyncMock()
            mock_tab.query_selector.return_value = mock_input

            assert await uploader.upload([file_path]) is True
            mock_input.send_file.assert_called_once_with(str(file_path))


@pytest.mark.asyncio
async def test_upload_timeout_waiting_for_chips(mock_tab):
    uploader = ArtifactUploader(mock_tab)

    with patch.object(
        ArtifactUploader, "_probe_upload_elements", new_callable=AsyncMock
    ) as mock_probe:
        mock_probe.return_value = {
            "composer_found": True,
            "file_input_selector": "input[type='file']",
            "attach_button_selector": "button[attach]",
        }

        with patch.object(
            ArtifactUploader, "_probe_upload_status", new_callable=AsyncMock
        ) as mock_status:
            mock_status.return_value = {"upload_confirmed": False}

            mock_input = AsyncMock()
            mock_tab.query_selector.return_value = mock_input

            with patch("asyncio.sleep", return_value=None):
                with patch("asyncio.get_running_loop") as mock_loop_getter:
                    mock_loop = MagicMock()
                    # Need enough values to satisfy the while loop and exit with timeout
                    mock_loop.time.side_effect = [0, 5, 10, 15, 20, 25, 30, 35, 40]
                    mock_loop_getter.return_value = mock_loop

                    with pytest.raises(NoDriverArtifactInputUploadTimeoutError):
                        await uploader.upload([Path("test.txt")])


@pytest.mark.asyncio
async def test_selector_escaping(mock_tab):
    uploader = ArtifactUploader(mock_tab)

    with patch.object(
        ArtifactUploader, "_probe_upload_elements", new_callable=AsyncMock
    ) as mock_probe:
        mock_probe.return_value = {
            "composer_found": True,
            "file_input_selector": None,
            "attach_button_selector": "button[aria-label='Attach\\'s file']",
        }

        mock_button = AsyncMock()
        mock_tab.query_selector.return_value = mock_button

        try:
            await uploader.upload([Path("test.txt")])
        except Exception:
            pass

        mock_tab.query_selector.assert_any_call("button[aria-label='Attach\\'s file']")
