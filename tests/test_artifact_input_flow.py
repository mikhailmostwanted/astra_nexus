from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astra_nexus.brain.nodriver.chatgpt_client import ChatGPTClient
from astra_nexus.brain.nodriver.exceptions import (
    NoDriverArtifactInputUploadTimeoutError,
    NoDriverProviderError,
)


@pytest.fixture
def mock_tab():
    tab = AsyncMock()
    return tab


@pytest.fixture
def client():
    settings = MagicMock()
    settings.nodriver_page_load_timeout_seconds = 10
    settings.nodriver_response_timeout_seconds = 60
    settings.nodriver_response_idle_confirm_seconds = 0
    settings.nodriver_response_progress_log_interval_seconds = 0
    settings.nodriver_response_max_empty_wait_seconds = 45
    settings.nodriver_debug_screenshots = False
    return ChatGPTClient(settings)


@pytest.mark.asyncio
async def test_ask_stops_if_upload_fails(client, mock_tab):
    debug_context = {
        "input_artifacts": ["test.txt"],
        "run_id": "test_run",
        "artifact_upload_enabled": True,
    }

    with patch.object(client, "_wait_for_prompt_box", new_callable=AsyncMock):
        with patch.object(client, "_fill_prompt", new_callable=AsyncMock) as mock_fill:
            with patch(
                "astra_nexus.brain.nodriver.chatgpt_client.ArtifactUploader"
            ) as mock_uploader_cls:
                mock_uploader = mock_uploader_cls.return_value
                mock_uploader.upload = AsyncMock(
                    side_effect=NoDriverArtifactInputUploadTimeoutError("Timeout Confirmation")
                )

                with pytest.raises(NoDriverProviderError) as exc:
                    await client._submit_prompt_and_wait(
                        mock_tab, prompt="hello", debug_context=debug_context
                    )

                assert "Timeout Confirmation" in str(exc.value)
                mock_fill.assert_not_called()


@pytest.mark.asyncio
async def test_ask_no_artifacts_does_not_call_uploader(client, mock_tab):
    debug_context = {"input_artifacts": [], "run_id": "test_run"}

    with patch.object(client, "_wait_for_prompt_box", new_callable=AsyncMock):
        with patch.object(client, "_fill_prompt", new_callable=AsyncMock):
            with patch.object(client, "_first_selector", new_callable=AsyncMock):
                with patch.object(client, "_wait_for_response_completion", new_callable=AsyncMock):
                    with patch(
                        "astra_nexus.brain.nodriver.chatgpt_client.ArtifactUploader"
                    ) as mock_uploader_cls:
                        await client._submit_prompt_and_wait(
                            mock_tab, prompt="hello", debug_context=debug_context
                        )
                        mock_uploader_cls.assert_not_called()


@pytest.mark.asyncio
async def test_upload_happens_after_wait_for_prompt_box(client, mock_tab):
    debug_context = {"input_artifacts": ["test.txt"], "artifact_upload_enabled": True}

    call_order = []

    async def mock_wait(*args, **kwargs):
        call_order.append("wait_for_prompt_box")

    async def mock_upload(*args, **kwargs):
        call_order.append("upload")
        return True

    with patch.object(client, "_wait_for_prompt_box", side_effect=mock_wait):
        with patch(
            "astra_nexus.brain.nodriver.chatgpt_client.ArtifactUploader"
        ) as mock_uploader_cls:
            mock_uploader = mock_uploader_cls.return_value
            mock_uploader.upload = AsyncMock(side_effect=mock_upload)

            with patch.object(client, "_fill_prompt", new_callable=AsyncMock):
                with patch.object(client, "_first_selector", new_callable=AsyncMock):
                    with patch.object(
                        client, "_wait_for_response_completion", new_callable=AsyncMock
                    ):
                        await client._submit_prompt_and_wait(
                            mock_tab, prompt="hi", debug_context=debug_context
                        )

    assert call_order == ["wait_for_prompt_box", "upload"]


@pytest.mark.asyncio
async def test_no_upload_without_explicit_flag(client, mock_tab):
    # Even with intent=file_task, upload should not happen without artifact_upload_enabled flag
    debug_context = {"input_artifacts": ["secret.txt"], "intent": "file_task"}

    with patch.object(client, "_wait_for_prompt_box", new_callable=AsyncMock):
        with patch.object(client, "_fill_prompt", new_callable=AsyncMock):
            with patch.object(client, "_first_selector", new_callable=AsyncMock):
                with patch.object(client, "_wait_for_response_completion", new_callable=AsyncMock):
                    with patch(
                        "astra_nexus.brain.nodriver.chatgpt_client.ArtifactUploader"
                    ) as mock_uploader_cls:
                        await client._submit_prompt_and_wait(
                            mock_tab, prompt="hello", debug_context=debug_context
                        )
                        mock_uploader_cls.assert_not_called()


@pytest.mark.asyncio
async def test_no_reupload_on_requested_file_retry(client, mock_tab):
    debug_context = {
        "input_artifacts": ["already_uploaded.txt"],
        "artifact_upload_enabled": True,
        "output_requested_as_file": True,
    }

    # Mock session to avoid real browser
    with patch.object(client.session, "start", new_callable=AsyncMock):
        with patch.object(client.session, "ensure_chatgpt_page", return_value=mock_tab):
            with patch.object(client.session, "current_url", return_value="http://chat.com"):
                with patch.object(client.session, "current_title", return_value="ChatGPT"):
                    with patch.object(client, "_login_state", return_value={"login_ok": True}):
                        with patch.object(
                            client, "_submit_prompt_and_wait", new_callable=AsyncMock
                        ) as mock_submit:
                            # Mocking wait_result for first call
                            res1 = MagicMock()
                            res1.final_answer = "Here is the file."
                            res1.structured_answer = {}
                            res1.detected_model = "gpt-4o"
                            res1.detected_reasoning_mode = None

                            res2 = MagicMock()
                            res2.final_answer = "Now really here is the file."
                            res2.structured_answer = {}
                            res2.detected_model = "gpt-4o"
                            res2.detected_reasoning_mode = None

                            mock_submit.side_effect = [res1, res2]

                            # Mock _detect_requested_file_artifacts to fail to trigger retry
                            with patch.object(
                                client, "_detect_requested_file_artifacts", new_callable=AsyncMock
                            ) as mock_detect:
                                mock_detect.return_value = MagicMock(selected=None)

                                with patch.object(
                                    client,
                                    "_requested_file_retry_prompt",
                                    return_value="Retry please",
                                ):
                                    try:
                                        await client.ask("make file", debug_context=debug_context)
                                    except Exception:
                                        # Expect NoDriverArtifactDownloadError after 2 attempts
                                        pass

                            # Check second call to _submit_prompt_and_wait
                            assert mock_submit.call_count == 2
                            retry_call_context = mock_submit.call_args_list[1].kwargs[
                                "debug_context"
                            ]
                            assert retry_call_context.get("requested_file_retry") is True
                            assert not retry_call_context.get(
                                "input_artifacts"
                            )  # Should be cleared
