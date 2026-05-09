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

## Если профиль занят

Ошибка `profile_locked` означает, что тот же profile уже использует живой процесс.

Что делать:

1. Закрой предыдущий `astra-nexus-nodriver-login`, `astra-nexus-nodriver-smoke`,
   API deep health или Telegram/API provider.
2. Если процесс завис, заверши PID из сообщения.
3. Выполни:

```bash
astra-nexus-nodriver-clean
```

Не удаляй `data/browser_profiles/default`, если уже выполнен вход в ChatGPT.

## Когда запускать API или bot

Запускай `astra-nexus-api` или `astra-nexus-bot` только после успешного smoke. Не
запускай smoke и API/bot одновременно с одним `NODRIVER_USER_DATA_DIR`.
