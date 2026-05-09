from __future__ import annotations

from astra_nexus.team.models import AgentProfile, AgentRole

DEFAULT_AGENT_PIPELINE = [
    AgentRole.COORDINATOR,
    AgentRole.ANALYST,
    AgentRole.CRITIC,
    AgentRole.EDITOR,
    AgentRole.QA_CONTROLLER,
    AgentRole.FINAL_COMPOSER,
]


DEFAULT_AGENT_PROFILES = [
    AgentProfile(
        role=AgentRole.COORDINATOR,
        display_name="Coordinator",
        description="Принимает задачу и задаёт маршрут работы команды.",
        system_instruction=(
            "Пойми цель пользователя, выдели ожидаемый результат и передай команде "
            "краткий рабочий план."
        ),
    ),
    AgentProfile(
        role=AgentRole.ANALYST,
        display_name="Analyst",
        description="Разбирает факты, структуру, вводные данные и ограничения.",
        system_instruction=(
            "Разбери исходную задачу, найди факты, ограничения, зависимости и явно "
            "отметь допущения."
        ),
    ),
    AgentProfile(
        role=AgentRole.CRITIC,
        display_name="Critic",
        description="Ищет ошибки, слабые места, противоречия и недосказанность.",
        system_instruction=(
            "Проверь предыдущие выводы на ошибки, противоречия, упущенные требования "
            "и рискованные допущения."
        ),
    ),
    AgentProfile(
        role=AgentRole.EDITOR,
        display_name="Editor",
        description="Улучшает текст, структуру, стиль и понятность.",
        system_instruction=(
            "Улучши структуру и формулировки результата, сохрани смысл и не добавляй "
            "неподтверждённые решения."
        ),
    ),
    AgentProfile(
        role=AgentRole.QA_CONTROLLER,
        display_name="QA Controller",
        description="Проверяет, выполнена ли задача и не потеряны ли требования.",
        system_instruction=(
            "Сверь результат с задачей пользователя, отметь пропуски и проверь, что "
            "финальный ответ можно отдавать."
        ),
    ),
    AgentProfile(
        role=AgentRole.FINAL_COMPOSER,
        display_name="Final Composer",
        description="Собирает финальный ответ в нормальный вид.",
        system_instruction=(
            "Собери итоговый ответ для пользователя на основе предыдущих результатов, "
            "без служебного шума и внутренних рассуждений."
        ),
    ),
]


def default_profiles_by_role() -> dict[AgentRole, AgentProfile]:
    return {profile.role: profile for profile in DEFAULT_AGENT_PROFILES}
