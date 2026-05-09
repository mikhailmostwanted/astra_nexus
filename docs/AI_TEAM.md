# AI Team Orchestration

`astra_nexus.team` - доменный слой AI-команды для будущего подключения к Telegram,
storage и реальным providers.

Слой не зависит от NoDriver, Telegram, FastAPI и SQLAlchemy. Сейчас он хранит состояние
run в памяти и нужен как чистый foundation для orchestration-логики.

## Состав слоя

- `TeamRun` - один запуск пользовательской задачи.
- `AgentProfile` - профиль агента: роль, имя, описание и системная инструкция.
- `AgentTask` - работа конкретного агента внутри run.
- `AgentResult` - текстовый результат агента.
- `RunEvent` - событие run или агента для будущего Telegram log-чата.

## Статусы и роли

Run использует `RunStatus`: `created`, `running`, `completed`, `failed`, `cancelled`.

Agent task использует `AgentTaskStatus`: `created`, `running`, `completed`, `failed`.

Pipeline по умолчанию:

1. `coordinator`
2. `analyst`
3. `critic`
4. `editor`
5. `qa_controller`
6. `final_composer`

## Provider contract

Агентский provider реализует `TeamProvider.generate(...)`:

- получает `AgentProfile`;
- получает исходную пользовательскую задачу;
- получает предыдущие `AgentResult`;
- возвращает текстовый результат.

`AsyncTeamOrchestrator` зависит только от `TeamProvider` и не импортирует NoDriver provider.
Реальный ChatGPT/NoDriver bridge можно будет подключить позже через отдельный adapter.

## События

Сейчас создаются события:

- `run_started`
- `run_finished`
- `run_failed`
- `agent_started`
- `agent_finished`
- `agent_failed`

Формат события уже содержит понятный `message` и `payload`, чтобы позже отдать эти данные
в Telegram log-chat без изменения domain-модели.

## Fake provider

`FakeTeamProvider` используется в unit-тестах. Он возвращает детерминированные ответы и
умеет симулировать ошибку выбранного агента через `fail_on`.

## Что пока не реализовано

- Telegram-группа и Telegram task flow.
- Параллельные агенты.
- Отдельные ChatGPT-чаты на агента.
- NoDriver adapter для AI-команды.
- Self-improving/Codex-режим.
- Storage/migrations для team-сущностей.

