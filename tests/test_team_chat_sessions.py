from pathlib import Path

from astra_nexus.team.chat_sessions import AgentChatSession, AgentChatSessionRegistry
from astra_nexus.team.models import AgentRole


def test_agent_chat_session_registry_save_load_upsert_list(tmp_path: Path) -> None:
    path = tmp_path / "agent_chats.json"
    registry = AgentChatSessionRegistry(path=path)
    session = AgentChatSession(
        agent_role=AgentRole.CRITIC,
        display_name="Критик",
        chat_url="https://chatgpt.com/c/critic",
        conversation_id="critic",
        bootstrap_status="created",
        preferred_model_name="GPT-5.5 Thinking",
        preferred_reasoning_mode="extended",
    )

    registry.upsert(session)
    registry.save()

    loaded = AgentChatSessionRegistry(path=path)
    loaded.load()

    assert path.exists()
    assert loaded.get_by_role(AgentRole.CRITIC).chat_url == "https://chatgpt.com/c/critic"
    assert loaded.get_by_role("critic").preferred_reasoning_mode == "extended"
    assert [item.agent_role for item in loaded.list()] == [AgentRole.CRITIC]


def test_agent_chat_session_registry_uses_default_storage_path(tmp_path: Path) -> None:
    registry = AgentChatSessionRegistry(root_dir=tmp_path)

    assert registry.path == tmp_path / "team_agent_chats" / "agent_chats.json"
