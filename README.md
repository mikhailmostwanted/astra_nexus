# Astra Nexus

Astra Nexus - личный командный центр AI-агентов в Telegram.

MVP создаёт задачу, запускает фиксированную команду агентов через `DummyBrainProvider`,
сохраняет сообщения в SQLite и пишет итоговый файл в workspace задачи.

## Что уже есть

- FastAPI backend: `/health`, `/api/tasks`, `/api/tasks/{task_id}`, `/api/agents`.
- `TaskOrchestrator` с доменными событиями для Telegram-лога.
- Роли агентов: Coordinator, Researcher, Writer, Critic, Finalizer.
- Абстракция `BrainProvider`, `DummyBrainProvider` и `NoDriverProvider`.
- SQLite-модели для задач, запусков, агентов, сообщений и артефактов.
- Telegram bot на aiogram 3 с polling и background task runner.
- NoDriverProvider для ChatGPT Web как заменяемый brain-provider.
- Workspace задачи в `data/workspaces/{task_id}/`.

## Локальный запуск

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
astra-nexus-api
```

Альтернатива без console script:

```bash
python -m astra_nexus.main
```

Проверка:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/brain/health
curl http://127.0.0.1:8000/api/agents
curl -X POST http://127.0.0.1:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Сделай короткий план MVP","user_id":"api:local"}'
```

Docker:

```bash
docker compose up api
docker compose --profile bot up bot
```

## Telegram

Если `TELEGRAM_BOT_TOKEN` не задан, bot не падает и логирует, что Telegram отключён.
Если токен задан, polling запускается отдельным процессом:

```bash
astra-nexus-bot
```

Альтернатива:

```bash
python -m astra_nexus.telegram.run_bot
```

Команды бота:

- `/start` - краткое описание и команды.
- `/agents` - список агентов, роли и статус.
- `/task <текст>` - создать задачу и запустить агентов в фоне.
- `/status <task_id>` - статус, последние сообщения, workspace и итог.
- `/cancel <task_id>` - отменить задачу между шагами агентов.

Текущий flow использует `DummyBrainProvider`: агентские сообщения приходят по шагам,
но без реального NoDriver и без платных API.

## NoDriver ChatGPT Web

Для включения ChatGPT Web bridge:

```bash
BRAIN_PROVIDER=nodriver
NODRIVER_USER_DATA_DIR=./data/browser_profiles/default
NODRIVER_HEADLESS=false
```

Перед первым запуском нужно вручную подготовить browser profile:

```bash
astra-nexus-nodriver-clean
astra-nexus-nodriver-login
astra-nexus-nodriver-smoke
```

В открывшемся браузере вручную войди в ChatGPT, затем нажми Enter в терминале login
helper. После успешного smoke можно запускать API или Telegram bot с
`BRAIN_PROVIDER=nodriver`.

Логины, пароли, cookies, runtime locks и browser profile остаются только локально в
`data/` и не коммитятся. Подробности: [docs/NODRIVER.md](docs/NODRIVER.md) и
[docs/NODRIVER_SMOKE_TEST.md](docs/NODRIVER_SMOKE_TEST.md).

Если Chrome стартует, но NoDriver не подключается или profile занят:

```bash
astra-nexus-nodriver-clean
astra-nexus-nodriver-diagnose
```

## Тесты и качество

```bash
pytest
ruff check .
ruff format .
```

Проверить без Telegram можно через API:

```bash
curl -X POST http://127.0.0.1:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Проверить dummy-flow","user_id":"api:local"}'
```

## Безопасность

Не хранить в git `.env`, cookies, browser profiles, Telegram session files, SQLite data и
workspace-артефакты. Все эти пути уже закрыты в `.gitignore`.
