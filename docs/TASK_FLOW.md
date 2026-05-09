# Task Flow

## Пример

Пользователь пишет в Telegram:

```text
/task Подготовь план MVP для личного AI Office
```

## Шаги

1. Telegram gateway принимает команду `/task`.
2. Bot сразу создаёт `Task`, `TaskRun` и workspace `data/workspaces/{task_id}/`.
3. Пользователь получает `task_id`, `run_id` и список агентов.
4. Background runner запускает синхронный orchestrator через `asyncio.to_thread`.
5. Coordinator планирует порядок работы и его сообщение отправляется в Telegram.
6. Researcher собирает факты и отправляет сообщение.
7. Writer пишет черновик и отправляет сообщение.
8. Critic проверяет результат и отправляет сообщение.
9. Finalizer собирает итог и отправляет сообщение.
10. Orchestrator сохраняет все сообщения в SQLite.
11. Итог пишется в `artifacts/final.md`.
12. Задача переходит в `done`, Telegram получает финальную карточку.

## Status

Команда `/status <task_id>` показывает текущее состояние задачи, последние сообщения
агентов, путь к workspace и итог, если задача уже завершена.

Для `failed` задач `/status` дополнительно показывает:

- failed stage;
- failed agent;
- error_code;
- error_message;
- путь к debug report, если он есть.

## Cancel

Команда `/cancel <task_id>` переводит задачу в `cancelled`, если она ещё не `done`.
Полная остановка уже выполняющегося шага пока не реализована: MVP проверяет отмену
между шагами агентов.

## Состояния

- `new`
- `planned`
- `running`
- `waiting_review`
- `finalizing`
- `done`
- `failed`
- `cancelled`

## Как дебажить ошибку Telegram /task + NoDriver

Если `/task ...` в Telegram падает на `BRAIN_PROVIDER=nodriver`, сначала отдели
NoDriver от Telegram:

1. Выполни `astra-nexus-nodriver-clean`.
2. Выполни `astra-nexus-nodriver-login`, войди в ChatGPT и нажми Enter.
3. Выполни `astra-nexus-nodriver-smoke`.
4. Выполни:

```bash
astra-nexus-nodriver-ask "Ответь одним предложением: Astra Nexus online."
```

5. Запусти `astra-nexus-bot`.
6. Отправь `/task ...`.
7. Если задача упала, открой:

```text
data/workspaces/{task_id}/debug/nodriver_error.json
```

Ошибка в Telegram имеет вид:

```text
Astra Nexus
Задача завершилась с ошибкой

task_id: ...
stage: ...
agent: ...
provider: nodriver
error_code: prompt_box_not_found
message: ...
debug: data/workspaces/{task_id}/debug/nodriver_error.json
```

Traceback не отправляется в Telegram и остаётся только в server logs.
