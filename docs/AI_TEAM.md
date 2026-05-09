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

## CLI smoke

Локальный smoke-запуск AI-команды выполняется командой:

```bash
astra-nexus-team-smoke "Составь краткий план улучшения Astra Nexus."
```

Если текст задачи не передан, команда использует дефолт:

```text
Составь краткий план улучшения Astra Nexus.
```

Команда использует только `FakeTeamProvider`, запускает `AsyncTeamOrchestrator`, сохраняет
workspace run и печатает:

- `status`
- `run_id`
- `final_result`
- `workspace_path`

## Team run workspace

Каждый smoke-run сохраняется в:

```text
data/team_runs/<run_id>/
```

Структура папки:

```text
data/team_runs/<run_id>/
  run.json
  events.jsonl
  final.md
  agent_results/
    coordinator.md
    analyst.md
    critic.md
    editor.md
    qa_controller.md
    final_composer.md
```

`run.json` содержит общий summary run: `run_id`, `status`, `user_task`, временные поля,
финальный результат и краткий список agent task/result summary.

`events.jsonl` содержит одну JSON-строку на событие: timestamp, event type, run id,
роль агента для agent-событий, message и details. Этот формат готовит слой к будущему
Telegram log/chat отображению без подключения Telegram на текущем этапе.

`agent_results/*.md` содержит человекочитаемый результат каждого агента: имя, роль,
исходную задачу, статус и текст результата.

`final.md` содержит финальный ответ `final_composer`.

## Что пока не реализовано

- Telegram-группа и Telegram task flow.
- Параллельные агенты.
- Отдельные ChatGPT-чаты на агента.
- NoDriver adapter для AI-команды.
- Self-improving/Codex-режим.
- Storage/migrations для team-сущностей.
