from __future__ import annotations

from astra_nexus.db.models import Agent


def render_agents(agents: list[Agent]) -> str:
    lines = ["Агенты Astra Nexus:"]
    lines.extend(f"- {agent.id}: {agent.name} - {agent.description}" for agent in agents)
    return "\n".join(lines)
