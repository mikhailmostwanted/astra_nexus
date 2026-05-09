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
- получает собранный `AgentPrompt` с `system_prompt`, `user_prompt` и metadata;
- возвращает текстовый результат.

`AsyncTeamOrchestrator` зависит только от `TeamProvider` и не импортирует NoDriver provider.
Реальный ChatGPT/NoDriver bridge можно будет подключить позже через отдельный adapter.

## Prompt Engine

Prompt Engine находится в `astra_nexus.team.prompting`.

Основные сущности:

- `AgentContext` - run id, задача пользователя, текущая роль/имя агента, предыдущие
  результаты команды, события run, workspace path и дополнительные инструкции.
- `AgentPrompt` - готовая пара `system_prompt` / `user_prompt` плюс debug metadata.
- `TeamPromptBuilder` - собирает prompt для конкретного агента на основе профиля и
  текущего контекста.

`AsyncTeamOrchestrator` строит `AgentContext` перед каждым шагом pipeline, вызывает
`TeamPromptBuilder`, затем передаёт готовый `AgentPrompt` в provider. Старые поля
`profile`, `user_task` и `previous_results` остаются в provider contract, чтобы adapter
можно было писать постепенно и без привязки к NoDriver.

## Живые профили агентов

Профили агентов содержат display-поля для будущего UI/Telegram отображения:

- `display_name`
- `short_description`
- `personality`
- `capabilities`
- `default_style`

Текущие роли:

- Артём / Координатор (`coordinator`) - понимает задачу, уточняет смысл, раскладывает
  работу на этапы и выдаёт план для команды.
- Ирина / Аналитик (`analyst`) - разбирает факты, структуру, вводные данные,
  ограничения и допущения.
- Вера / Критик (`critic`) - ищет слабые места, недостающие требования,
  противоречия и вопросы к решению.
- Лина / Редактор (`editor`) - берёт план и критику, усиливает текст или решение,
  сохраняя смысл задачи.
- Максим / Контроль качества (`qa_controller`) - проверяет готовый вариант,
  ошибки, пустые утверждения и недосказанность перед финалом.
- Саша / Финальный сборщик (`final_composer`) - собирает финальный ответ для
  пользователя и не показывает внутреннюю кухню, если пользователь этого не просил.

Эти поля не влияют на бизнес-логику как обязательные идентификаторы. Они нужны, чтобы
позже показывать команду в Telegram как понятных "живых участников", а не как сухие
служебные статусы.

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

Fake provider не импортирует NoDriver и не требует браузерной сессии. Он нужен для
быстрой проверки orchestration, prompts, событий и workspace.

## NoDriver team provider

`NoDriverTeamProvider` находится в `astra_nexus.team.nodriver_provider`.

Он реализует тот же `TeamProvider.generate(...)`, но внутри использует существующий
`NoDriverProvider` из `astra_nexus.brain.nodriver_provider`. Browser lifecycle,
ChatGPT client, retry/debug поведение и локальный browser profile остаются в NoDriver
слое; team adapter не дублирует browser-логику.

Так как текущий `NoDriverProvider.ask(...)` принимает один текстовый prompt, adapter
склеивает `AgentPrompt.system_prompt` и `AgentPrompt.user_prompt` в полный prompt:

1. системная инструкция агента;
2. задача пользователя;
3. предыдущие результаты команды;
4. инструкция текущего агента.

Этот prompt отправляется в существующий `NoDriverProvider.ask(...)` с `agent_id` текущей
роли и контекстом run/workspace для debug-reporting.

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

## CLI real team ask

Реальный запуск команды через ChatGPT Web/NoDriver выполняется командой:

```bash
astra-nexus-team-ask "Ответь кратко: что такое Astra Nexus?"
```

Если текст задачи не передан, используется дефолт:

```text
Ответь кратко: что такое Astra Nexus?
```

Команда запускает тот же последовательный `AsyncTeamOrchestrator`, но с
`NoDriverTeamProvider`, сохраняет workspace run и печатает:

- `status`
- `run_id`
- `workspace_path`
- `final_result`

Перед реальным запуском нужно подготовить локальную ChatGPT Web сессию обычными
NoDriver-командами:

```bash
astra-nexus-nodriver-login
astra-nexus-nodriver-smoke
```

`astra-nexus-team-ask` пока использует один общий ChatGPT Web provider последовательно.
Параллельные агенты, отдельные ChatGPT-чаты и отдельные agent sessions будут добавлены
позже.

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

Если у результата есть prompt debug metadata, workspace добавляет секцию
`Внутренний prompt` в соответствующий `agent_results/*.md`. Это dev/debug слой для
локального анализа качества prompts; Telegram пока его не использует.

`final.md` содержит финальный ответ `final_composer`.

## Что пока не реализовано

- Telegram-группа и Telegram task flow.
- Параллельные агенты.
- Отдельные ChatGPT-чаты на агента.
- NoDriver adapter для AI-команды.
- Self-improving/Codex-режим.
- Storage/migrations для team-сущностей.
