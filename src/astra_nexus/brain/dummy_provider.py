from __future__ import annotations

from typing import Any

from astra_nexus.brain.base import BrainProvider, BrainResponse


class DummyBrainProvider(BrainProvider):
    name = "dummy"

    def ask(
        self,
        agent_id: str,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> BrainResponse:
        task_prompt = (context or {}).get("task_prompt", prompt)
        content = self._build_content(agent_id=agent_id, task_prompt=task_prompt)
        return BrainResponse(
            content=content,
            provider=self.name,
            metadata={"stub": True, "agent_id": agent_id},
        )

    def _build_content(self, agent_id: str, task_prompt: str) -> str:
        match agent_id:
            case "coordinator":
                return (
                    "План:\n"
                    "1. Уточнить цель и ожидаемый результат.\n"
                    "2. Собрать исходные факты.\n"
                    "3. Подготовить черновик.\n"
                    "4. Проверить риски и собрать итог.\n"
                    f"Фокус задачи: {task_prompt}"
                )
            case "researcher":
                return (
                    "Исследование:\n"
                    "- Для MVP достаточно локального хранилища и детерминированного flow.\n"
                    "- Внешний brain-provider должен оставаться заменяемым.\n"
                    "- Рабочий лог нужно сохранять как сообщения агентов."
                )
            case "writer":
                return (
                    "Черновик:\n"
                    "Сформирован базовый ответ по задаче. Структура включает цель, шаги, "
                    "ограничения и проверяемый итог."
                )
            case "critic":
                return (
                    "Проверка:\n"
                    "- Итог должен быть конкретным.\n"
                    "- Нужны сохранённые сообщения всех агентов.\n"
                    "- Нельзя зависеть от платного API."
                )
            case "finalizer":
                return (
                    "Итог:\n"
                    "Задача обработана демо-командой агентов Astra Nexus. "
                    "Получен структурированный результат без внешних paid API."
                )
            case _:
                return f"Ответ агента {agent_id}: обработан запрос «{task_prompt}»."
