from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from astra_nexus.brain.nodriver.artifact_uploader import ArtifactUploader
from astra_nexus.brain.nodriver.exceptions import (
    NoDriverArtifactInputUploadButtonNotFoundError,
    NoDriverArtifactInputUploadFilenameMismatchError,
    NoDriverArtifactInputUploadTimeoutError,
)


@pytest.fixture
def mock_tab():
    tab = AsyncMock()
    # Mock evaluate to return a success structure by default
    tab.evaluate.return_value = {
        "composer_found": True,
        "file_input_selector": "input[type='file']",
        "debug": {},
    }
    return tab


@pytest.fixture
def uploader(mock_tab):
    return ArtifactUploader(mock_tab)


@pytest.mark.asyncio
async def test_upload_success(uploader, mock_tab):
    # Mocking successful sequence
    with patch("pathlib.Path.exists", return_value=True):
        with patch("pathlib.Path.absolute", return_value=Path("/tmp/test.txt")):
            # 1. find_upload_element -> returns input
            # 2. wait_for_chips -> returns success
            mock_tab.evaluate.side_effect = [
                {"composer_found": True, "file_input_selector": "input[type='file']", "debug": {}},
                {
                    "upload_confirmed": True,
                    "all_files_matched": True,
                    "found_in_chips": ["test.txt"],
                },
            ]

            # mock_tab.query_selector needs to return something
            mock_element = AsyncMock()
            mock_tab.query_selector.return_value = mock_element

            success = await uploader.upload([Path("/tmp/test.txt")])
            assert success is True
            mock_element.send_file.assert_called_once_with("/tmp/test.txt")


@pytest.mark.asyncio
async def test_upload_button_not_found(uploader, mock_tab):
    with patch("pathlib.Path.exists", return_value=True):
        mock_tab.evaluate.return_value = {
            "composer_found": True,
            "file_input_selector": None,
            "attach_button_selector": None,
            "debug": {},
        }

        with pytest.raises(NoDriverArtifactInputUploadButtonNotFoundError):
            await uploader.upload([Path("test.txt")])


@pytest.mark.asyncio
async def test_upload_timeout_waiting_for_chips(uploader, mock_tab):
    with patch("pathlib.Path.exists", return_value=True):
        # find_upload_element succeeds
        # wait_for_chips returns empty chips multiple times
        mock_tab.evaluate.side_effect = [
            {
                "composer_found": True,
                "file_input_selector": "input[type='file']",
                "attach_button_selector": "button.attach",  # Add this to avoid ButtonNotFoundError
                "debug": {},
            },
            {"upload_confirmed": False},
            {"upload_confirmed": False},
            {"upload_confirmed": False},
        ]
        mock_tab.query_selector.return_value = AsyncMock()

        # We need to speed up the loop or mock time
        with patch("asyncio.sleep", return_value=None):
            with patch("asyncio.get_running_loop") as mock_loop:
                # 0, 1, 2, ... 16 seconds
                mock_loop.return_value.time.side_effect = [0, 1, 2, 16]

                with pytest.raises(NoDriverArtifactInputUploadTimeoutError):
                    await uploader.upload([Path("test.txt")])


@pytest.mark.asyncio
async def test_filename_mismatch(uploader, mock_tab):
    with patch("pathlib.Path.exists", return_value=True):
        mock_tab.evaluate.side_effect = [
            {"composer_found": True, "file_input_selector": "input[type='file']", "debug": {}},
            {"upload_confirmed": True, "all_files_matched": False, "found_in_chips": ["wrong.txt"]},
        ]
        mock_tab.query_selector.return_value = AsyncMock()

        with pytest.raises(NoDriverArtifactInputUploadFilenameMismatchError):
            await uploader.upload([Path("right.txt")])


@pytest.mark.asyncio
async def test_script_escaping_logic(uploader, mock_tab):
    # Test that we don't crash with weird filenames and correctly escape them in JS
    weird_name = "file'with\"quotes and \\backslashes.txt"
    with patch("pathlib.Path.exists", return_value=True):
        mock_tab.evaluate.side_effect = [
            {"composer_found": True, "file_input_selector": "input[type='file']", "debug": {}},
            {"upload_confirmed": True, "all_files_matched": True, "found_in_chips": [weird_name]},
        ]
        mock_tab.query_selector.return_value = AsyncMock()

        await uploader.upload([Path(weird_name)])

        # Verify that the second evaluate call (status probe) received the escaped filename
        import json

        expected_json = json.dumps([weird_name])
        script_sent = mock_tab.evaluate.call_args_list[1][0][0]
        assert f"const filenames = {expected_json};" in script_sent
