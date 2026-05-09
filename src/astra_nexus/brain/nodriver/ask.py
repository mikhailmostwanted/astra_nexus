from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from astra_nexus.brain.nodriver.exceptions import (
    NoDriverPromptInsertFailedError,
    NoDriverProviderError,
)
from astra_nexus.brain.nodriver_provider import NoDriverProvider
from astra_nexus.config.settings import Settings, load_settings
from astra_nexus.utils.logging import configure_logging


async def run(argv: list[str] | None = None, *, provider: Any | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    prompt = " ".join(argv).strip()
    if not prompt:
        print('Использование: astra-nexus-nodriver-ask "текст"')
        return 2

    settings = load_settings()
    configure_logging(settings.log_level)
    provider = provider or NoDriverProvider(settings=settings)
    session = getattr(getattr(provider, "client", None), "session", None)
    error: NoDriverProviderError | None = None

    try:
        response = await provider.ask(
            agent_id="manual",
            prompt=prompt,
            context={"task_prompt": prompt},
        )
    except NoDriverProviderError as exc:
        error = exc
        debug_report_path = _write_prompt_insert_debug_report(settings, exc)
        print(f"status: {exc.error_code}")
        print(f"stage: {exc.stage or 'unknown'}")
        print(f"message: {exc}")
        if exc.url:
            print(f"url: {exc.url}")
        if exc.selector:
            print(f"selector: {exc.selector}")
        for key in (
            "ready_state",
            "textarea_count",
            "contenteditable_count",
            "textbox_count",
            "candidate_count",
        ):
            if key in exc.details:
                print(f"{key}: {exc.details[key]}")
        if debug_report_path is not None:
            print(f"debug_report: {debug_report_path}")
        print(f"action: {exc.action}")
        return_code = 1
    else:
        print("status: ok")
        print(f"response: {response.content}")
        return_code = 0
    finally:
        if error is not None and settings.nodriver_keep_browser_open_on_error and session:
            await asyncio.to_thread(
                input,
                "Браузер оставлен открытым. Проверь страницу и нажми Enter для закрытия.",
            )
        if session is not None:
            await session.stop()

    return return_code


def _write_prompt_insert_debug_report(
    settings: Settings,
    exc: NoDriverProviderError,
) -> Path | None:
    if not isinstance(exc, NoDriverPromptInsertFailedError):
        return None

    path = (
        Path(settings.data_dir).expanduser().resolve()
        / "debug"
        / "nodriver"
        / "prompt_insert_failed.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "error_code": exc.error_code,
        "stage": exc.stage,
        "message": str(exc),
        "url": exc.url,
        "page_title": exc.page_title,
        "selector": exc.selector,
        "details": exc.details,
    }
    for key in ("activeElement", "outerHTML", "dom_probe_summary", "attempts", "method"):
        if key in exc.details:
            payload[key] = exc.details[key]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
