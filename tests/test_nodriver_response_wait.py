import asyncio
from pathlib import Path

import pytest

from astra_nexus.brain.nodriver.chatgpt_client import (
    ChatGPTClient,
    ResponseWaitSnapshot,
    ResponseWaitState,
)
from astra_nexus.brain.nodriver.exceptions import (
    NoDriverPreferredModelError,
    NoDriverTimeoutError,
)
from astra_nexus.config.settings import Settings


class FakeSession:
    async def current_url(self) -> str:
        return "https://chatgpt.com/"

    async def current_title(self) -> str:
        return "ChatGPT"


class SequenceWaitClient(ChatGPTClient):
    def __init__(
        self,
        *,
        settings: Settings,
        snapshots: list[ResponseWaitSnapshot],
    ) -> None:
        super().__init__(settings=settings, session=FakeSession())
        self.snapshots = list(snapshots)
        self.sleep_calls: list[float] = []

    async def _response_wait_snapshot(self, tab: object) -> ResponseWaitSnapshot:
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]

    async def _response_wait_sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        await asyncio.sleep(0)


def snapshot(
    messages: list[str],
    *,
    generating: bool,
    prompt_available: bool = False,
    send_idle: bool = False,
    indicators: list[str] | None = None,
    continue_required: bool = False,
) -> ResponseWaitSnapshot:
    return ResponseWaitSnapshot(
        assistant_messages=messages,
        is_generating=generating,
        stop_button_visible=generating,
        prompt_available=prompt_available,
        send_button_idle=send_idle,
        visible_indicators=indicators or [],
        continue_required=continue_required,
    )


def test_response_wait_returns_last_segment_only_after_final_idle(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        nodriver_response_timeout_seconds=0,
        nodriver_response_idle_confirm_seconds=0,
    )
    client = SequenceWaitClient(
        settings=settings,
        snapshots=[
            snapshot(["old"], generating=True, indicators=["thinking"]),
            snapshot(["old", "intermediate"], generating=True, indicators=["thinking"]),
            snapshot(
                ["old", "intermediate", "final answer"],
                generating=True,
                indicators=["streaming"],
            ),
            snapshot(
                ["old", "intermediate", "final answer"],
                generating=False,
                prompt_available=True,
                send_idle=True,
            ),
        ],
    )

    result = asyncio.run(
        client._wait_for_response_completion(
            object(),
            response_count_before=1,
            debug_context={"workspace_path": tmp_path},
        )
    )

    assert result.final_answer == "final answer"
    assert result.assistant_segments == ["intermediate", "final answer"]
    assert result.final_segment_index == 1
    assert result.final_idle_detected is True
    states = [entry["state"] for entry in result.wait_state_timeline]
    assert ResponseWaitState.INTERMEDIATE_RESPONSE_SEEN.value in states
    assert ResponseWaitState.WAITING_FOR_FINAL_IDLE.value in states
    assert states[-1] == ResponseWaitState.FINAL_RESPONSE_READY.value


def test_response_wait_does_not_complete_from_stable_text_without_idle(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        nodriver_response_timeout_seconds=0,
        nodriver_response_idle_confirm_seconds=0,
    )
    client = SequenceWaitClient(
        settings=settings,
        snapshots=[
            snapshot(["old", "partial"], generating=True, indicators=["thinking"]),
            snapshot(["old", "partial"], generating=True, indicators=["thinking"]),
            snapshot(
                ["old", "partial", "final"],
                generating=False,
                prompt_available=True,
                send_idle=True,
            ),
        ],
    )

    result = asyncio.run(
        client._wait_for_response_completion(
            object(),
            response_count_before=1,
            debug_context={"workspace_path": tmp_path},
        )
    )

    assert result.final_answer == "final"
    assert result.assistant_segments == ["partial", "final"]


def test_response_wait_hard_timeout_disabled_allows_long_generating_state(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        nodriver_response_timeout_seconds=0,
        nodriver_response_idle_confirm_seconds=0,
    )
    client = SequenceWaitClient(
        settings=settings,
        snapshots=[
            snapshot(["old"], generating=True, indicators=["thinking"]),
            snapshot(["old"], generating=True, indicators=["thinking"]),
            snapshot(["old", "done"], generating=False, prompt_available=True, send_idle=True),
        ],
    )

    result = asyncio.run(
        client._wait_for_response_completion(
            object(),
            response_count_before=1,
            debug_context={"workspace_path": tmp_path},
        )
    )

    assert result.final_answer == "done"
    assert len(client.sleep_calls) >= 1


def test_response_wait_hard_timeout_enabled_fails_with_diagnostics(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        nodriver_response_timeout_seconds=0.001,
        nodriver_response_idle_confirm_seconds=2,
    )
    client = SequenceWaitClient(
        settings=settings,
        snapshots=[
            snapshot(["old"], generating=True, indicators=["thinking"]),
            snapshot(["old"], generating=True, indicators=["thinking"]),
        ],
    )

    with pytest.raises(NoDriverTimeoutError) as exc:
        asyncio.run(
            client._wait_for_response_completion(
                object(),
                response_count_before=1,
                debug_context={"workspace_path": tmp_path},
            )
        )

    assert exc.value.details["timeout_reason"] == "hard_timeout"
    assert exc.value.details["final_idle_detected"] is False


def test_response_wait_cancel_clicks_stop_generation(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, nodriver_response_timeout_seconds=0)
    client = SequenceWaitClient(
        settings=settings,
        snapshots=[snapshot(["old"], generating=True, indicators=["thinking"])],
    )
    stopped = False

    async def stop_generation(_tab: object) -> bool:
        nonlocal stopped
        stopped = True
        return True

    client._try_stop_generation = stop_generation

    async def scenario() -> None:
        task = asyncio.create_task(
            client._wait_for_response_completion(
                object(),
                response_count_before=1,
                debug_context={"workspace_path": tmp_path},
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert stopped is True


def test_require_preferred_model_blocks_when_detected_model_mismatches(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        nodriver_preferred_model_name="GPT-5.5 Thinking",
        nodriver_preferred_reasoning_mode="extended",
        nodriver_require_preferred_model=True,
    )
    client = SequenceWaitClient(settings=settings, snapshots=[])

    with pytest.raises(NoDriverPreferredModelError) as exc:
        asyncio.run(client._ensure_preferred_model(object(), {"current_model": "GPT-4o"}))

    assert exc.value.status == "preferred_model_not_active"
    assert exc.value.details["preferred_model"] == "GPT-5.5 Thinking"
    assert exc.value.details["detected_model"] == "GPT-4o"


def test_require_preferred_model_blocks_when_model_cannot_be_detected(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        nodriver_preferred_model_name="GPT-5.5 Thinking",
        nodriver_require_preferred_model=True,
    )
    client = SequenceWaitClient(settings=settings, snapshots=[])

    with pytest.raises(NoDriverPreferredModelError) as exc:
        asyncio.run(client._ensure_preferred_model(object(), {"current_model": None}))

    assert exc.value.details["detected_model"] is None


def test_require_preferred_reasoning_blocks_when_reasoning_mismatches(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        nodriver_preferred_reasoning_mode="extended",
        nodriver_require_preferred_model=True,
    )
    client = SequenceWaitClient(settings=settings, snapshots=[])

    with pytest.raises(NoDriverPreferredModelError) as exc:
        asyncio.run(
            client._ensure_preferred_model(
                object(),
                {"current_model": "GPT-5.5 Thinking", "reasoning_mode": "standard"},
            )
        )

    assert exc.value.details["preferred_reasoning_mode"] == "extended"
    assert exc.value.details["detected_reasoning_mode"] == "standard"
