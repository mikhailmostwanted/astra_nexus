# Telegram Live Test Checklist

Цель: проверить локальный MVP AI-команды в Telegram без production/deploy и без изменения
NoDriver lifecycle.

## Подготовка

1. Создать Telegram bot token через BotFather.
2. Прописать `.env`:
   - `TELEGRAM_BOT_TOKEN=...`
   - `TEAM_TELEGRAM_PROVIDER=fake`
   - `TEAM_TELEGRAM_ALLOWED_CHAT_IDS=<твой chat_id>`
   - `TEAM_TELEGRAM_LOG_CHAT_ID=<log chat id>` при наличии отдельного лог-чата.
   - `TEAM_RUNS_DIR=./data/team_runs`
   - `TEAM_TELEGRAM_DOWNLOADS_DIR=./data/team_telegram_downloads`

   Обычная Telegram-группа может автоматически мигрировать в supergroup. Если в логах
   видно `TelegramMigrateToChat: group migrated to supergroup with id -100... from ...`,
   используй новый id формата `-100...` в allowlist и log chat:

   ```env
   TEAM_TELEGRAM_ALLOWED_CHAT_IDS=-1003721761135
   TEAM_TELEGRAM_LOG_CHAT_ID=-1003902519410
   ```

3. Проверить локальную готовность:

```bash
astra-nexus-team-mvp-check
```

Ожидаемо: `status: ok` или `status: warn`, если намеренно не задан log chat/token для preview.

## Fake Provider Live

1. Запустить polling bot:

```bash
astra-nexus-team-telegram-bot
```

2. В Telegram проверить casual message:

```text
брат че думаешь
```

Ожидаемо: короткий человеческий ответ без team run.

3. Проверить task:

```text
сделай краткий план AI-команды
```

Ожидаемо: задача стартует, main chat получает живые реплики и финальный ответ, log chat
получает технические события.

4. Проверить file task `.md` или `.txt`:
   - отправить файл без caption;
   - затем отправить файл с caption `проверь файл и сделай краткий вывод`.

Ожидаемо: файл без caption не запускает run; файл с caption создаёт run, сохраняет
`input_files/`, `attachments.json`, `attachments.md` и итоговые `artifacts/`.

5. Проверить команды:

```text
/help
/health
/status
/runs
/stopall
```

Ожидаемо:

- `/help` показывает список команд;
- `/health` показывает provider, active job, последние terminal runs, runs dir и log chat;
- `/status` показывает active job или последний run из registry;
- `/runs` показывает последние 5 runs;
- `/stopall` останавливает active job или сообщает, что активных задач нет.

## NoDriver Provider Live

1. Остановить Telegram bot.
2. Отдельно проверить NoDriver:

```bash
astra-nexus-nodriver-clean
astra-nexus-nodriver-smoke
astra-nexus-nodriver-ask "Ответь ровно так: Astra Nexus online."
```

3. Если ручной вход нужен, выполнить:

```bash
astra-nexus-nodriver-login
```

4. Переключить `.env`:

```env
TEAM_TELEGRAM_PROVIDER=nodriver
```

5. Повторить диагностику:

```bash
astra-nexus-team-mvp-check
```

Ожидаемо: check не стартует NoDriver автоматически и напоминает проверить smoke/ask.

6. Запустить Telegram bot снова:

```bash
astra-nexus-team-telegram-bot
```

7. Проверить короткую task через Telegram:

```text
составь краткий план улучшения Astra Nexus
```

Ожидаемо: Chrome/NoDriver работает локально, main chat получает финал, workspace и
artifacts сохраняются в `TEAM_RUNS_DIR`.

## Что считать проблемой

- Main chat показывает traceback или внутренний stack trace.
- `/help`, `/health`, `/status`, `/runs` создают team run.
- Файл без caption запускает run.
- `TEAM_TELEGRAM_PROVIDER=fake` импортирует или запускает NoDriver.
- `astra-nexus-team-mvp-check` падает из-за отсутствия Telegram API/token.
