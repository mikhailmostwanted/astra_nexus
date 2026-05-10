import asyncio
import json
from pathlib import Path

import pytest

from astra_nexus.brain.nodriver.chatgpt_client import (
    ChatGPTClient,
    ResponseTurnBaseline,
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
        self.idle_without_final_artifact_calls = 0

    async def _response_wait_snapshot(self, tab: object) -> ResponseWaitSnapshot:
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]

    async def _response_wait_sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        await asyncio.sleep(0)

    async def _write_idle_without_final_text_artifacts(
        self,
        tab: object,
        *,
        debug_context: dict,
        turn_baseline: ResponseTurnBaseline,
    ) -> dict[str, str]:
        self.idle_without_final_artifact_calls += 1
        workspace_path = Path(debug_context["workspace_path"])
        html_path = workspace_path / "debug" / "nodriver_response_wait_manual.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text("<article>Думаю</article>", encoding="utf-8")
        return {"assistant_turn_html_path": str(html_path)}


def snapshot(
    messages: list[str],
    *,
    generating: bool,
    prompt_available: bool = False,
    send_idle: bool = False,
    indicators: list[str] | None = None,
    continue_required: bool = False,
    assistant_indexes: list[int] | None = None,
    user_count: int = 0,
    last_user_index: int | None = None,
    assistant_turns: list[dict] | None = None,
) -> ResponseWaitSnapshot:
    return ResponseWaitSnapshot(
        assistant_messages=messages,
        is_generating=generating,
        stop_button_visible=generating,
        prompt_available=prompt_available,
        send_button_idle=send_idle,
        visible_indicators=indicators or [],
        continue_required=continue_required,
        assistant_message_indexes=assistant_indexes or list(range(len(messages))),
        user_messages_count=user_count,
        last_user_message_index=last_user_index,
        assistant_turns=assistant_turns or [],
    )


def assistant_turn(
    *,
    index: int,
    final_text: str,
    raw_text: str = "",
    final_source: str = "assistant_root_text",
    thought_preview: str = "",
    rejected_reason: str = "",
) -> dict:
    return {
        "index": index,
        "role": "assistant",
        "id": f"assistant:{index}",
        "finalText": final_text,
        "text": final_text,
        "textLength": len(final_text),
        "textPreview": final_text,
        "rawTextPreview": raw_text or final_text,
        "finalCandidatePreviews": (
            [
                {
                    "source": final_source,
                    "selector": "div.markdown",
                    "textLength": len(final_text),
                    "textPreview": final_text,
                }
            ]
            if final_text
            else []
        ),
        "thoughtCandidatePreviews": (
            [
                {
                    "selector": ".result-thinking",
                    "textLength": len(thought_preview),
                    "textPreview": thought_preview,
                }
            ]
            if thought_preview
            else []
        ),
        "rejectedCandidateReasons": (
            [{"source": "visible_text", "selector": "span", "reason": rejected_reason}]
            if rejected_reason
            else []
        ),
    }


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
    debug_payload = json.loads(
        (tmp_path / "debug" / "nodriver_response_wait_manual.json").read_text(encoding="utf-8")
    )
    assert debug_payload["assistant_segments"] == ["intermediate", "final answer"]
    assert debug_payload["last_snapshot"]["send_button_state"] == "unknown"
    assert debug_payload["detected_phase"] == "idle_with_answer"


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


def test_response_wait_selects_segments_after_current_user_turn(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        nodriver_response_timeout_seconds=0,
        nodriver_response_idle_confirm_seconds=0,
    )
    baseline = ResponseTurnBaseline(
        assistant_count_before=1,
        user_count_before=1,
        last_user_message_index=0,
    )
    client = SequenceWaitClient(
        settings=settings,
        snapshots=[
            snapshot(
                ["old assistant", "old assistant completed after baseline", "final current turn"],
                generating=False,
                prompt_available=True,
                send_idle=True,
                assistant_indexes=[1, 2, 4],
                user_count=2,
                last_user_index=3,
            ),
        ],
    )

    result = asyncio.run(
        client._wait_for_response_completion(
            object(),
            response_count_before=baseline.assistant_count_before,
            turn_baseline=baseline,
            debug_context={"workspace_path": tmp_path},
        )
    )

    assert result.final_answer == "final current turn"
    assert result.assistant_segments == ["final current turn"]


def test_response_wait_uses_final_candidate_when_turn_has_thought_block(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        nodriver_response_timeout_seconds=0,
        nodriver_response_idle_confirm_seconds=0,
    )
    turn = assistant_turn(
        index=1,
        final_text="Astra Nexus online.",
        raw_text="Думаю\nAstra Nexus online.",
        thought_preview="Думаю",
    )
    client = SequenceWaitClient(
        settings=settings,
        snapshots=[
            snapshot(
                ["Astra Nexus online."],
                generating=False,
                prompt_available=True,
                send_idle=True,
                assistant_indexes=[1],
                user_count=1,
                last_user_index=0,
                assistant_turns=[turn],
            ),
        ],
    )

    result = asyncio.run(
        client._wait_for_response_completion(
            object(),
            response_count_before=0,
            turn_baseline=ResponseTurnBaseline(assistant_count_before=0),
            debug_context={"workspace_path": tmp_path},
        )
    )

    assert result.final_answer == "Astra Nexus online."
    debug_payload = json.loads(
        (tmp_path / "debug" / "nodriver_response_wait_manual.json").read_text(encoding="utf-8")
    )
    assert debug_payload["thought_candidate_previews"][0]["textPreview"] == "Думаю"
    assert debug_payload["final_candidate_previews"][0]["textPreview"] == "Astra Nexus online."


def test_response_wait_thought_only_turn_fails_with_debug_not_success(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        nodriver_response_timeout_seconds=0,
        nodriver_response_idle_confirm_seconds=0,
        nodriver_response_max_empty_wait_seconds=0.001,
    )
    turn = assistant_turn(
        index=1,
        final_text="",
        raw_text="Думаю",
        thought_preview="Думаю",
        rejected_reason="thought_or_reasoning_candidate",
    )
    client = SequenceWaitClient(
        settings=settings,
        snapshots=[
            snapshot(
                [""],
                generating=False,
                prompt_available=True,
                send_idle=True,
                assistant_indexes=[1],
                user_count=1,
                last_user_index=0,
                assistant_turns=[turn],
            ),
        ],
    )

    with pytest.raises(NoDriverTimeoutError) as exc:
        asyncio.run(
            client._wait_for_response_completion(
                object(),
                response_count_before=0,
                turn_baseline=ResponseTurnBaseline(assistant_count_before=0),
                debug_context={"workspace_path": tmp_path},
            )
        )

    debug_path = tmp_path / "debug" / "nodriver_response_wait_manual.json"
    html_path = tmp_path / "debug" / "nodriver_response_wait_manual.html"
    payload = json.loads(debug_path.read_text(encoding="utf-8"))
    assert exc.value.details["timeout_reason"] == "idle_without_final_text"
    assert payload["detected_phase"] == "stuck_unknown"
    assert payload["assistant_segments"] == []
    assert payload["assistant_turn_html_path"] == str(html_path)
    assert html_path.exists()
    assert client.idle_without_final_artifact_calls == 1


def test_response_wait_finds_final_answer_from_markdown_candidate(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        nodriver_response_timeout_seconds=0,
        nodriver_response_idle_confirm_seconds=0,
    )
    turn = assistant_turn(
        index=1,
        final_text="Final from markdown",
        final_source="markdown_prose",
    )
    client = SequenceWaitClient(
        settings=settings,
        snapshots=[
            snapshot(
                ["Final from markdown"],
                generating=False,
                prompt_available=True,
                send_idle=True,
                assistant_indexes=[1],
                user_count=1,
                last_user_index=0,
                assistant_turns=[turn],
            ),
        ],
    )

    result = asyncio.run(
        client._wait_for_response_completion(
            object(),
            response_count_before=0,
            turn_baseline=ResponseTurnBaseline(assistant_count_before=0),
            debug_context={"workspace_path": tmp_path},
        )
    )

    assert result.final_answer == "Final from markdown"


def test_response_wait_uses_last_final_assistant_segment_after_user(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        nodriver_response_timeout_seconds=0,
        nodriver_response_idle_confirm_seconds=0,
    )
    client = SequenceWaitClient(
        settings=settings,
        snapshots=[
            snapshot(
                ["", "actual final"],
                generating=False,
                prompt_available=True,
                send_idle=True,
                assistant_indexes=[1, 2],
                user_count=1,
                last_user_index=0,
                assistant_turns=[
                    assistant_turn(
                        index=1, final_text="", raw_text="Думаю", thought_preview="Думаю"
                    ),
                    assistant_turn(index=2, final_text="actual final"),
                ],
            ),
        ],
    )

    result = asyncio.run(
        client._wait_for_response_completion(
            object(),
            response_count_before=0,
            turn_baseline=ResponseTurnBaseline(assistant_count_before=0),
            debug_context={"workspace_path": tmp_path},
        )
    )

    assert result.final_answer == "actual final"
    assert result.assistant_segments == ["actual final"]


def test_response_wait_does_not_use_hidden_text_candidate_as_final(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        nodriver_response_timeout_seconds=0,
        nodriver_response_idle_confirm_seconds=0,
    )
    turn = assistant_turn(
        index=1,
        final_text="visible final",
        raw_text="hidden final visible final",
        rejected_reason="hidden_candidate",
    )
    client = SequenceWaitClient(
        settings=settings,
        snapshots=[
            snapshot(
                ["visible final"],
                generating=False,
                prompt_available=True,
                send_idle=True,
                assistant_indexes=[1],
                user_count=1,
                last_user_index=0,
                assistant_turns=[turn],
            ),
        ],
    )

    result = asyncio.run(
        client._wait_for_response_completion(
            object(),
            response_count_before=0,
            turn_baseline=ResponseTurnBaseline(assistant_count_before=0),
            debug_context={"workspace_path": tmp_path},
        )
    )

    assert result.final_answer == "visible final"
    debug_payload = json.loads(
        (tmp_path / "debug" / "nodriver_response_wait_manual.json").read_text(encoding="utf-8")
    )
    assert debug_payload["rejected_candidate_reasons"][0]["reason"] == "hidden_candidate"


def test_response_wait_does_not_return_user_prompt_as_answer(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        nodriver_response_timeout_seconds=0,
        nodriver_response_idle_confirm_seconds=0,
    )
    client = SequenceWaitClient(
        settings=settings,
        snapshots=[
            snapshot(
                ["Astra Nexus online."],
                generating=False,
                prompt_available=True,
                send_idle=True,
                assistant_indexes=[2],
                user_count=1,
                last_user_index=1,
                assistant_turns=[assistant_turn(index=2, final_text="Astra Nexus online.")],
            ),
        ],
    )

    result = asyncio.run(
        client._wait_for_response_completion(
            object(),
            response_count_before=0,
            turn_baseline=ResponseTurnBaseline(assistant_count_before=0),
            debug_context={"workspace_path": tmp_path},
        )
    )

    assert result.final_answer == "Astra Nexus online."
    assert result.final_answer != "Ответь ровно так: Astra Nexus online."


def test_response_wait_progress_marks_idle_without_answer_as_stuck_unknown() -> None:
    client = SequenceWaitClient(settings=Settings(_env_file=None), snapshots=[])
    phase = client._detected_response_phase(
        snapshot(
            [],
            generating=False,
            prompt_available=True,
            send_idle=True,
            user_count=1,
            last_user_index=0,
        ),
        segments=[],
        state=ResponseWaitState.PROMPT_SUBMITTED,
    )

    assert phase == "stuck_unknown"


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
