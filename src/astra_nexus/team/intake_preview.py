from __future__ import annotations

import argparse

from astra_nexus.team.intake import TeamInput, TeamIntakeRouter

DEFAULT_MESSAGE = "брат че думаешь"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    text = " ".join(args.message).strip() or DEFAULT_MESSAGE
    decision = TeamIntakeRouter().route(
        TeamInput(
            text=text,
            attachments_count=args.attachments_count,
            active_run_id=args.active_run_id,
            last_run_id=args.last_run_id,
            failed_run_id=args.failed_run_id,
            has_active_run=args.has_active_run,
        )
    )

    print(f"intent: {decision.intent.value}")
    print(f"confidence: {decision.confidence:.2f}")
    print(f"reason: {decision.reason}")
    print(f"should_start_run: {_bool_text(decision.should_start_run)}")
    print(f"should_resume_run: {_bool_text(decision.should_resume_run)}")
    print(f"should_stop_runs: {_bool_text(decision.should_stop_runs)}")
    if decision.target_run_id:
        print(f"target_run_id: {decision.target_run_id}")
    print(f"user_visible_reply: {decision.user_visible_reply}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview AI Team intake routing decision.")
    parser.add_argument(
        "message",
        nargs="*",
        help="Входящее сообщение пользователя.",
    )
    parser.add_argument("--attachments-count", type=int, default=0)
    parser.add_argument("--active-run-id", default=None)
    parser.add_argument("--last-run-id", default=None)
    parser.add_argument("--failed-run-id", default=None)
    parser.add_argument("--has-active-run", action="store_true")
    return parser.parse_args(argv)


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


if __name__ == "__main__":
    raise SystemExit(main())
