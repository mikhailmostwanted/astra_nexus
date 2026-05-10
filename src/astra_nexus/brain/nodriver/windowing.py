from __future__ import annotations

import os
import subprocess

from astra_nexus.config.settings import Settings

MANUAL_VISIBLE_CONTEXTS = {"login", "dom_probe", "insert_probe", "debug", "doctor"}
PROVIDER_CONTEXTS = {"provider", "smoke", "ask"}


def _context_window_mode(settings: Settings, *, context: str) -> str:
    if context in MANUAL_VISIBLE_CONTEXTS:
        return settings.nodriver_login_window_mode or settings.nodriver_window_mode
    if context in PROVIDER_CONTEXTS:
        return settings.nodriver_provider_window_mode or settings.nodriver_window_mode
    return settings.nodriver_window_mode


def effective_nodriver_window_mode(
    settings: Settings,
    *,
    context: str = "provider",
    fallback_window_mode: bool = False,
) -> str:
    if fallback_window_mode:
        return "small" if context in MANUAL_VISIBLE_CONTEXTS else "minimal"

    mode = _context_window_mode(settings, context=context).strip().lower()
    if mode == "normal":
        mode = "visible"
    if context in MANUAL_VISIBLE_CONTEXTS and mode in {
        "offscreen",
        "minimized",
        "headless_experimental",
    }:
        return "small"
    return mode


def effective_nodriver_headless(
    settings: Settings,
    *,
    context: str = "provider",
    fallback_window_mode: bool = False,
) -> bool:
    if fallback_window_mode:
        return False
    if context in MANUAL_VISIBLE_CONTEXTS:
        return False
    if settings.nodriver_headless:
        return True
    return (
        effective_nodriver_window_mode(
            settings,
            context=context,
            fallback_window_mode=fallback_window_mode,
        )
        == "headless_experimental"
    )


def build_nodriver_browser_args(
    settings: Settings,
    *,
    context: str = "provider",
    fallback_window_mode: bool = False,
) -> list[str]:
    if fallback_window_mode and context not in MANUAL_VISIBLE_CONTEXTS:
        return []
    mode = effective_nodriver_window_mode(
        settings,
        context=context,
        fallback_window_mode=fallback_window_mode,
    )
    if mode in {"visible", "headless_experimental"} or effective_nodriver_headless(
        settings,
        context=context,
        fallback_window_mode=fallback_window_mode,
    ):
        return []

    width = max(320, int(settings.nodriver_window_width))
    height = max(240, int(settings.nodriver_window_height))
    if mode == "offscreen":
        position = f"{int(settings.nodriver_offscreen_x)},{int(settings.nodriver_offscreen_y)}"
    elif mode == "minimized":
        position = f"{int(settings.nodriver_offscreen_x)},{int(settings.nodriver_offscreen_y)}"
    else:
        position = f"{int(settings.nodriver_window_x)},{int(settings.nodriver_window_y)}"

    return [
        f"--window-size={width},{height}",
        f"--window-position={position}",
    ]


def should_adjust_window_after_start(
    settings: Settings,
    *,
    context: str = "provider",
    fallback_window_mode: bool = False,
) -> bool:
    if fallback_window_mode or context in MANUAL_VISIBLE_CONTEXTS:
        return False
    mode = effective_nodriver_window_mode(
        settings,
        context=context,
        fallback_window_mode=fallback_window_mode,
    )
    return bool(
        settings.nodriver_minimize_after_start
        or settings.nodriver_hide_after_start
        or mode in {"minimized", "offscreen"}
    )


def apply_macos_window_adjustment(
    settings: Settings,
    *,
    context: str = "provider",
    fallback_window_mode: bool = False,
) -> dict[str, object]:
    mode = effective_nodriver_window_mode(
        settings,
        context=context,
        fallback_window_mode=fallback_window_mode,
    )
    report: dict[str, object] = {
        "attempted": False,
        "ok": False,
        "mode": mode,
        "minimize": settings.nodriver_minimize_after_start or mode == "minimized",
        "hide": settings.nodriver_hide_after_start,
    }
    if os.name != "posix" or not should_adjust_window_after_start(
        settings,
        context=context,
        fallback_window_mode=fallback_window_mode,
    ):
        return report

    script_lines = [
        'tell application "System Events"',
        "  set chromeProcesses to (application processes whose "
        'name is "Google Chrome" or name is "Chromium")',
        "  repeat with chromeProcess in chromeProcesses",
    ]
    if settings.nodriver_minimize_after_start or mode == "minimized":
        script_lines.extend(
            [
                "    try",
                '      set value of attribute "AXMinimized" of every window of '
                "chromeProcess to true",
                "    end try",
            ]
        )
    if settings.nodriver_hide_after_start:
        script_lines.extend(
            [
                "    try",
                "      set visible of chromeProcess to false",
                "    end try",
            ]
        )
    script_lines.extend(["  end repeat", "end tell"])
    script = "\n".join(script_lines)
    report["attempted"] = True
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            check=False,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        report["error"] = str(exc)
        return report

    report["returncode"] = result.returncode
    report["stdout"] = result.stdout.strip()
    report["stderr"] = result.stderr.strip()
    report["ok"] = result.returncode == 0
    return report
