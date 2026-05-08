from astra_nexus.agents.registry import AgentRegistry, create_default_registry


def test_default_registry_contains_core_agents() -> None:
    registry = create_default_registry()

    assert isinstance(registry, AgentRegistry)
    assert registry.ids() == [
        "coordinator",
        "researcher",
        "writer",
        "critic",
        "finalizer",
    ]


def test_registry_returns_agent_by_id() -> None:
    registry = create_default_registry()

    agent = registry.get("writer")

    assert agent.agent_id == "writer"
    assert agent.role == "writer"
