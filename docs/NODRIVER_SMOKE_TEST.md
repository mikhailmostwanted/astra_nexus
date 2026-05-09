# NoDriver Smoke Test

Smoke test проверяет реальный ChatGPT Web bridge. Обычный `pytest` браузер не
запускает.

## Перед проверкой

1. Проверь `.env`:

```env
BRAIN_PROVIDER=nodriver
NODRIVER_USER_DATA_DIR=./data/browser_profiles/default
NODRIVER_HEADLESS=false
NODRIVER_CHATGPT_URL=https://chatgpt.com/
NODRIVER_START_TIMEOUT_SECONDS=90
NODRIVER_KEEP_BROWSER_OPEN_ON_ERROR=false
```

2. Закрой лишние Chrome/Chromium окна, открытые предыдущими NoDriver-командами.
3. Выполни:

```bash
astra-nexus-nodriver-clean
```

## Login

```bash
astra-nexus-nodriver-login
```

Команда откроет ровно один Chrome с `NODRIVER_USER_DATA_DIR`. Войди в ChatGPT вручную.
После входа нажми Enter в терминале. Сессия останется в browser profile.

Не используй `Ctrl+C` как обычный способ завершения login helper.

## Smoke

```bash
astra-nexus-nodriver-smoke
```

Команда:

- берёт lifecycle lock;
- открывает ChatGPT;
- проверяет, что не требуется login;
- отправляет prompt `Ответь одним предложением: Astra Nexus online.`;
- печатает ответ;
- закрывает браузер и освобождает lock.

Успешный вывод содержит:

```text
status: ok
result: ...
```

## Manual ask

После smoke проверь реальный prompt без Telegram:

```bash
astra-nexus-nodriver-ask "Ответь одним предложением: Astra Nexus online."
```

Успешный вывод содержит:

```text
status: ok
response: ...
```

При ошибке команда печатает `status`, `stage`, `message`, `url`, `selector` и `action`.
Это помогает понять, проблема в NoDriver/ChatGPT или уже в Telegram task flow.

## Если smoke падает с prompt_box_not_found

Запусти безопасный DOM probe:

```bash
astra-nexus-nodriver-dom-probe
```

Команда открывает ChatGPT через текущий профиль, не отправляет prompt и сохраняет
только метаданные candidate-элементов:

```text
data/debug/nodriver/dom_probe.json
```

В выводе будут `current_url`, `page_title`, `ready_state`, `textarea_count`,
`contenteditable_count`, `textbox_count`, `candidate_count` и видимые candidates.

Для ручной диагностики можно временно включить:

```env
NODRIVER_KEEP_BROWSER_OPEN_ON_ERROR=true
```

Тогда smoke/ask при ошибке оставит Chrome открытым до Enter в терминале, после чего
браузер будет закрыт и lock освобождён.

## Если профиль занят

Ошибка `profile_locked` означает, что тот же profile уже использует живой процесс.

Что делать:

1. Закрой предыдущий `astra-nexus-nodriver-login`, `astra-nexus-nodriver-smoke`,
   `astra-nexus-nodriver-ask`, `astra-nexus-nodriver-dom-probe`, API deep health
   или Telegram/API provider.
2. Если процесс завис, заверши PID из сообщения.
3. Выполни:

```bash
astra-nexus-nodriver-clean
```

Не удаляй `data/browser_profiles/default`, если уже выполнен вход в ChatGPT.

## Когда запускать API или bot

Запускай `astra-nexus-api` или `astra-nexus-bot` только после успешных smoke и manual
ask. Не запускай smoke/ask и API/bot одновременно с одним `NODRIVER_USER_DATA_DIR`.

Если Telegram `/task` упал, ошибка будет в сообщении Telegram и в:

```text
data/workspaces/{task_id}/debug/nodriver_error.json
```
