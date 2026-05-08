# Astra Nexus

Astra Nexus - личный командный центр AI-агентов в Telegram.

MVP создаёт задачу, запускает фиксированную команду агентов через `DummyBrainProvider`,
сохраняет сообщения в SQLite и пишет итоговый файл в workspace задачи.

## Что уже есть

- FastAPI backend: `/health`, `/api/tasks`, `/api/tasks/{task_id}`, `/api/agents`.
- Синхронный `TaskOrchestrator` без очереди.
- Роли агентов: Coordinator, Researcher, Writer, Critic, Finalizer.
- Абстракция `BrainProvider` и заглушки `DummyBrainProvider` / `NoDriverProvider`.
- SQLite-модели для задач, запусков, агентов, сообщений и артефактов.
- Каркас Telegram layer на aiogram 3.
- Workspace задачи в `data/workspaces/{task_id}/`.

## Локальный запуск

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn astra_nexus.main:app --reload
```

Проверка:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/agents
curl -X POST http://127.0.0.1:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Сделай короткий план MVP","user_id":"api:local"}'
```

## Telegram

Если `TELEGRAM_BOT_TOKEN` не задан, приложение не падает и логирует, что Telegram bot
отключён. Реальный polling будет подключаться отдельным entrypoint на следующем этапе.

Команды бота:

- `/start`
- `/task <текст>`
- `/status <task_id>`
- `/agents`

## Тесты и качество

```bash
pytest
ruff check .
ruff format .
```

## Безопасность

Не хранить в git `.env`, cookies, browser profiles, Telegram session files, SQLite data и
workspace-артефакты. Все эти пути уже закрыты в `.gitignore`.
