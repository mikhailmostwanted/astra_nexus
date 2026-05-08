from __future__ import annotations

from astra_nexus.core.events import TaskEvent
from astra_nexus.db.models import Agent

AGENT_TITLES = {
    "coordinator": "Coordinator",
    "researcher": "Researcher",
    "writer": "Writer",
    "critic": "Critic",
    "finalizer": "Finalizer",
}


def agent_title(agent_id: str) -> str:
    return AGENT_TITLES.get(agent_id, agent_id)


def render_agent_message(event: TaskEvent) -> str:
    agent_id = str(event.payload.get("agent_id", "agent"))
    content = str(event.payload.get("content", "")).strip()
    return f"{agent_title(agent_id)}\n{content}"


def render_agents(agents: list[Agent]) -> str:
    lines = ["Astra Nexus", "Агенты офиса:"]
    for agent in agents:
        status = "active" if agent.is_active else "disabled"
        lines.append(f"- {agent.id} / {agent.role} / {status}: {agent.description}")
    return "\n".join(lines)
