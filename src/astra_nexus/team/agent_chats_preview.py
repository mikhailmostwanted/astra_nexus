from __future__ import annotations

from astra_nexus.config.settings import load_settings
from astra_nexus.team.chat_sessions import AgentChatSessionRegistry


def main() -> None:
    settings = load_settings()
    registry = AgentChatSessionRegistry(root_dir=settings.data_dir)
    registry.load()
    print("Astra Nexus Team agent chats")
    print(f"path: {registry.path}")
    sessions = registry.list()
    print(f"sessions: {len(sessions)}")
    for session in sessions:
        print(
            " - "
            f"{session.agent_role.value}: {session.bootstrap_status}; "
            f"url={session.chat_url or 'missing'}; "
            f"model={session.preferred_model_name or 'default'}; "
            f"reasoning={session.preferred_reasoning_mode or 'default'}"
        )


if __name__ == "__main__":
    main()
