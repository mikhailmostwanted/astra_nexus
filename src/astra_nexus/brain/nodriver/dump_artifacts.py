from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from astra_nexus.brain.nodriver.artifact_detector import (
    artifact_detection_from_probe_payload,
    build_artifact_detector_probe_script,
)
from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.dom_probe import evaluate_script
from astra_nexus.config.settings import load_settings
from astra_nexus.utils.logging import configure_logging


async def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = load_settings()
    configure_logging(settings.log_level)
    session = BrowserSession(settings, lifecycle_context="doctor")
    try:
        await session.start()
        tab = await session.ensure_chatgpt_page()
        payload = await evaluate_script(tab, build_artifact_detector_probe_script())
        result = artifact_detection_from_probe_payload(payload)
    finally:
        await session.stop()

    result_payload = result.as_dict()
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result_payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"output: {output_path}")

    print(f"status: {'found' if result.selected is not None else 'not_found'}")
    print(f"candidates: {len(result.candidates)}")
    print(f"rejected: {len(result.rejected)}")
    if result.selected is not None:
        print(f"filename: {result.selected.filename or ''}")
        print(f"extension: {result.selected.extension or ''}")
        print(f"download_url: {result.selected.download_url or ''}")
        print(f"button_id: {result.selected.button_id or ''}")
    else:
        print(f"visible_text: {_line(result.debug.visible_text)}")
    if args.json:
        print(json.dumps(result_payload, ensure_ascii=False, indent=2, default=str))
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dump ChatGPT Web artifact/download candidates from the current assistant turn."
    )
    parser.add_argument("--output", help="Optional JSON output path.")
    parser.add_argument("--json", action="store_true", help="Print full JSON payload.")
    return parser.parse_args(argv)


def _line(value: Any) -> str:
    return " ".join(str(value or "").split())[:240]


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
