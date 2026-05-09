from __future__ import annotations

from astra_nexus.brain.nodriver.lifecycle import NoDriverLifecycleManager
from astra_nexus.config.settings import load_settings
from astra_nexus.utils.logging import configure_logging


def main() -> None:
    raise SystemExit(run())


def run() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    lifecycle = NoDriverLifecycleManager(settings, context="clean")
    report = lifecycle.clean()
    lock_info = report.lock_info

    print("Astra Nexus NoDriver clean")
    print(f"user_data_dir: {report.user_data_dir}")
    print(f"profile_exists: {report.user_data_dir.exists()}")
    print(f"lock_file: {report.lock_path}")
    print(f"lock_exists: {lock_info is not None}")
    print(f"lock_pid: {lock_info.pid if lock_info else 'none'}")
    print(f"lock_context: {lock_info.context if lock_info else 'none'}")
    print(f"lock_pid_alive: {report.lock_pid_alive}")
    print(
        "live_profile_processes: "
        + (
            ", ".join(str(process.pid) for process in report.live_profile_processes)
            if report.live_profile_processes
            else "none"
        )
    )
    print(f"stale_lock_removed: {report.stale_lock_removed}")
    print(f"invalid_lock_removed: {report.invalid_lock_removed}")
    print(
        "removed_chrome_lock_files: "
        + (
            ", ".join(report.removed_chrome_lock_files)
            if report.removed_chrome_lock_files
            else "none"
        )
    )

    if report.lock_pid_alive or report.live_profile_processes:
        print("status: profile_locked")
        print("message: живой процесс использует профиль; cookies/session/profile не удалялись")
        return 1
    if report.stale_lock_removed:
        print("status: stale_lock_cleaned")
    else:
        print("status: ok")
    print("message: безопасная очистка завершена; browser profile не удалялся")
    return 0


if __name__ == "__main__":
    main()
