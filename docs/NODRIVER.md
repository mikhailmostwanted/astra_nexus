# NoDriver ChatGPT Web Bridge

## Зачем нужен NoDriverProvider

`NoDriverProvider` подключает ChatGPT Web как заменяемый brain-provider для агентов
Astra Nexus без OpenAI API и без платных API-зависимостей.

Он реализует тот же контракт `BrainProvider.ask(...)`, поэтому orchestrator, Telegram
gateway, DB и workspace не зависят от деталей браузерной автоматизации.

## Как включить

В `.env`:

```env
BRAIN_PROVIDER=nodriver
NODRIVER_USER_DATA_DIR=./data/browser_profiles/default
NODRIVER_HEADLESS=false
NODRIVER_CHATGPT_URL=https://chatgpt.com/
NODRIVER_RESPONSE_TIMEOUT_SECONDS=180
NODRIVER_PAGE_LOAD_TIMEOUT_SECONDS=60
NODRIVER_AGENT_MODE=single_profile
NODRIVER_DEBUG_SCREENSHOTS=false
NODRIVER_SCREENSHOTS_DIR=./data/debug/screenshots
```

Для возврата к локальному fake-flow:

```env
BRAIN_PROVIDER=dummy
```

## Ручной вход в ChatGPT

Логины и пароли не хранятся и не автоматизируются.

```bash
astra-nexus-nodriver-login
```

Команда открывает браузер с `NODRIVER_USER_DATA_DIR`. В открывшемся окне нужно вручную
войти в ChatGPT. Сессия сохранится в локальном browser profile.

После входа останови helper через `Ctrl+C`.

## Где хранится профиль

По умолчанию:

```text
./data/browser_profiles/default
```

Нельзя коммитить:

- browser profile;
- cookies;
- local/session storage;
- screenshots из debug-режима;
- `.env`;
- любые session-файлы.

Эти пути закрыты в `.gitignore`.

## Ограничения

- Bridge зависит от текущего UI ChatGPT Web.
- Селекторы могут потребовать обновления после изменений интерфейса.
- Headless-режим может вести себя иначе, чем обычное окно.
- Система не обходит капчи, лимиты, блокировки и защитные механизмы.
- Если ChatGPT просит ручное действие, Astra Nexus возвращает понятную ошибку.

## Диагностика

```bash
curl http://127.0.0.1:8000/api/brain/health
```

Статусы:

- `ok` - provider доступен.
- `login_required` - нужен ручной вход через `astra-nexus-nodriver-login`.
- `timeout` - ChatGPT Web не ответил за заданное время.
- `selector_not_found` - UI изменился, нужно обновить селекторы.
- `unavailable` - браузер или страница недоступны.

## Почему это bridge

NoDriver не управляет задачами, агентами, состояниями, Telegram и файлами. Он только
отвечает на prompt агента и возвращает текст. Это сохраняет возможность заменить
ChatGPT Web на другой brain-provider без переписывания ядра Astra Nexus.
