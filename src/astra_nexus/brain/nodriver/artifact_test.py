from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from astra_nexus.brain.nodriver.exceptions import NoDriverProviderError
from astra_nexus.brain.nodriver_provider import NoDriverProvider
from astra_nexus.config.settings import load_settings
from astra_nexus.utils.logging import configure_logging


async def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        print('Использование: astra-nexus-nodriver-artifact-test "сделай docx файл ..."')
        return 2

    settings = load_settings()
    configure_logging(settings.log_level)
    workspace_path = Path(args.workspace).expanduser().resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)
    provider = NoDriverProvider(settings=settings)
    session = getattr(getattr(provider, "client", None), "session", None)
    full_prompt = _downloadable_file_prompt(prompt, output_format=args.format)
    try:
        response = await provider.ask(
            agent_id="artifact_test",
            prompt=full_prompt,
            context={
                "task_prompt": prompt,
                "direct_prompt": True,
                "run_id": workspace_path.name,
                "task_id": workspace_path.name,
                "workspace_path": workspace_path,
                "output_requested_as_file": True,
                "requested_output_format": args.format,
            },
        )
    except NoDriverProviderError as exc:
        print(f"status: {exc.error_code}")
        print(f"stage: {exc.stage or 'unknown'}")
        print(f"message: {exc}")
        print(f"workspace: {workspace_path}")
        print(f"artifact_debug: {workspace_path / 'artifact_detector_debug.json'}")
        print(f"download_result: {workspace_path / 'requested_file_download_result.json'}")
        return 1
    finally:
        if session is not None:
            await session.stop()

    print("status: ok")
    print(f"workspace: {workspace_path}")
    print(f"response: {response.content}")
    result_path = workspace_path / "requested_file_download_result.json"
    if result_path.exists():
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        print(f"downloaded_file: {payload.get('path') or ''}")
        print(f"size_bytes: {payload.get('size_bytes') or 0}")
    return 0


def _downloadable_file_prompt(prompt: str, *, output_format: str) -> str:
    extension = str(output_format or "file").lstrip(".")
    return "\n".join(
        [
            prompt,
            "",
            "Create an actual downloadable file in ChatGPT Web.",
            f"Required extension: .{extension}.",
            "Do not only paste text. The final turn must include a file card, attachment, "
            "filename chip, download link, or download button.",
        ]
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ask ChatGPT Web for a downloadable file.")
    parser.add_argument("--format", default="docx", help="Expected extension, e.g. docx/pdf/md.")
    parser.add_argument(
        "--workspace",
        default="data/debug/nodriver/artifact_test",
        help="Workspace directory for requested file debug output.",
    )
    parser.add_argument("prompt", nargs=argparse.REMAINDER)
    return parser.parse_args(sys.argv[1:] if argv is None else argv)


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
