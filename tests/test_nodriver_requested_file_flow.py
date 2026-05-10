from __future__ import annotations

import asyncio

import pytest

from astra_nexus.brain.nodriver.artifact_detector import (
    ArtifactDetectionDebug,
    ArtifactDetectionResult,
)
from astra_nexus.brain.nodriver.chatgpt_client import (
    ChatGPTClient,
    ResponseWaitResult,
    ResponseWaitSnapshot,
)
from astra_nexus.brain.nodriver.exceptions import NoDriverArtifactDownloadError
from astra_nexus.config.settings import Settings


class FakeSession:
    async def start(self) -> None:
        return None

    async def ensure_chatgpt_page(self) -> object:
        return object()

    async def current_url(self) -> str:
        return "https://chatgpt.com/c/test"

    async def current_title(self) -> str:
        return "ChatGPT"


class FakeSendButton:
    async def click(self) -> None:
        return None


class RequestedFileMissingClient(ChatGPTClient):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings=settings, session=FakeSession())
        self.prompts: list[str] = []
        self.detector_calls = 0

    async def _login_state(self, tab: object) -> dict:
        return {"login_required": False, "login_ok": True, "reason": "test"}

    async def _safe_response_wait_snapshot(self, tab: object):
        return ResponseWaitSnapshot(
            assistant_messages=[],
            is_generating=False,
            stop_button_visible=False,
            prompt_available=True,
            send_button_idle=True,
        )

    async def _ensure_preferred_model(self, tab: object, debug_context: dict) -> None:
        return None

    async def _wait_for_prompt_box(
        self,
        tab: object,
        debug_context: dict,
        login_state: dict,
    ) -> object:
        return object()

    async def _fill_prompt(self, tab: object, prompt: str) -> dict:
        self.prompts.append(prompt)
        return {"ok": True}

    async def _first_selector(self, *args, **kwargs) -> FakeSendButton:
        return FakeSendButton()

    async def _wait_for_response_completion(self, *args, **kwargs) -> ResponseWaitResult:
        return ResponseWaitResult(
            final_answer="Готово, файл можно скачать.",
            assistant_segments=["Готово, файл можно скачать."],
            response_count_before=0,
            response_count_after=1,
            final_segment_index=0,
            wait_state_timeline=[],
            final_idle_detected=True,
        )

    async def _detect_requested_file_artifacts(self, tab: object) -> ArtifactDetectionResult:
        self.detector_calls += 1
        return ArtifactDetectionResult(
            candidates=[],
            rejected=[],
            selected=None,
            debug=ArtifactDetectionDebug(
                candidates=[],
                rejected_candidates=[],
                html_snippet="<article>Готово</article>",
                visible_text="Готово, файл можно скачать.",
            ),
        )


def test_requested_file_flow_retries_once_and_fails_without_downloadable_file(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        nodriver_response_timeout_seconds=0,
        nodriver_response_idle_confirm_seconds=0,
    )
    client = RequestedFileMissingClient(settings=settings)

    with pytest.raises(NoDriverArtifactDownloadError):
        asyncio.run(
            client.ask(
                "Сделай документ.",
                debug_context={
                    "workspace_path": tmp_path / "team_run_1",
                    "run_id": "team_run_1",
                    "output_requested_as_file": True,
                    "requested_output_format": "docx",
                },
            )
        )

    assert client.detector_calls == 2
    assert len(client.prompts) == 2
    assert "downloadable file" in client.prompts[-1]
    assert (tmp_path / "team_run_1" / "artifact_detector_debug.json").exists()
    assert (tmp_path / "team_run_1" / "requested_file_download_result.json").exists()


def test_text_answer_flow_does_not_run_artifact_detector(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        nodriver_response_timeout_seconds=0,
        nodriver_response_idle_confirm_seconds=0,
    )
    client = RequestedFileMissingClient(settings=settings)

    response = asyncio.run(
        client.ask("Ответь текстом.", debug_context={"workspace_path": tmp_path})
    )

    assert response == "Готово, файл можно скачать."
    assert client.detector_calls == 0
    assert len(client.prompts) == 1
