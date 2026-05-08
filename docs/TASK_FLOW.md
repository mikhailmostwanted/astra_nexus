# Task Flow

## Пример

Пользователь пишет в Telegram:

```text
/task Подготовь план MVP для личного AI Office
```

## Шаги

1. Telegram gateway принимает команду и передаёт задачу backend.
2. Backend создаёт `Task`, `TaskRun` и workspace `data/workspaces/{task_id}/`.
3. Coordinator планирует порядок работы.
4. Researcher собирает факты и ограничения.
5. Writer пишет черновик.
6. Critic проверяет результат.
7. Finalizer собирает итог.
8. Orchestrator сохраняет сообщения агентов.
9. Итог пишется в `artifacts/final.md`.
10. Задача переходит в `done`.

## Состояния

- `new`
- `planned`
- `running`
- `waiting_review`
- `finalizing`
- `done`
- `failed`
- `cancelled`
