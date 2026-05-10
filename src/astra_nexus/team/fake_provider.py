from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from astra_nexus.team.models import AgentProfile, AgentResult, AgentRole
from astra_nexus.team.prompting import AgentPrompt
from astra_nexus.team.provider import TeamProvider, TeamProviderError


@dataclass(frozen=True)
class FakeProviderCall:
    profile: AgentProfile
    user_task: str
    previous_results_count: int
    prompt: AgentPrompt | None


class FakeTeamProvider(TeamProvider):
    name = "fake"
    supports_parallel = True

    def __init__(
        self,
        *,
        fail_on: AgentRole | str | None = None,
        responses: Mapping[AgentRole | str, str] | None = None,
    ) -> None:
        self.fail_on = AgentRole(fail_on) if isinstance(fail_on, str) else fail_on
        self.responses = {
            AgentRole(role) if isinstance(role, str) else role: response
            for role, response in (responses or {}).items()
        }
        self.calls: list[FakeProviderCall] = []

    async def generate(
        self,
        *,
        profile: AgentProfile,
        user_task: str,
        previous_results: Sequence[AgentResult],
        prompt: AgentPrompt | None = None,
    ) -> str:
        self.calls.append(
            FakeProviderCall(
                profile=profile,
                user_task=user_task,
                previous_results_count=len(previous_results),
                prompt=prompt,
            )
        )
        if profile.role == self.fail_on:
            raise TeamProviderError(
                f"fake provider failed for agent {profile.role.value}",
                agent_id=profile.profile_id,
            )
        if profile.role in self.responses:
            return self.responses[profile.role]
        return f"fake:{profile.role.value}:{user_task}:context={len(previous_results)}"
