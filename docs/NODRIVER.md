# NoDriver ChatGPT Web Bridge

`NoDriverProvider` подключает ChatGPT Web как заменяемый brain-provider без OpenAI API
и без платных API. Он сохраняет контракт `BrainProvider.ask(...)`, поэтому orchestrator,
Telegram, DB и workspace не зависят от деталей браузера.

## Настройки

В `.env`:

```env
BRAIN_PROVIDER=nodriver
NODRIVER_USER_DATA_DIR=./data/browser_profiles/default
NODRIVER_HEADLESS=false
NODRIVER_CHATGPT_URL=https://chatgpt.com/
NODRIVER_RESPONSE_TIMEOUT_SECONDS=180
NODRIVER_PAGE_LOAD_TIMEOUT_SECONDS=60
NODRIVER_START_TIMEOUT_SECONDS=90
NODRIVER_NO_SANDBOX=false
NODRIVER_BROWSER_EXECUTABLE_PATH=
NODRIVER_AGENT_MODE=single_profile
NODRIVER_DEBUG_SCREENSHOTS=false
NODRIVER_SCREENSHOTS_DIR=./data/debug/screenshots
```

Если `NODRIVER_BROWSER_EXECUTABLE_PATH` пустой, NoDriver сам ищет Chrome/Chromium.
Если путь задан, он передаётся в NoDriver как абсолютный путь.

## Правильный порядок

1. Настрой `.env`.
2. Выполни безопасную очистку:

```bash
astra-nexus-nodriver-clean
```

3. Открой login helper:

```bash
astra-nexus-nodriver-login
```

4. Войди в ChatGPT в открывшемся окне.
5. Нажми Enter в терминале с login helper.
6. Проверь реальный browser bridge:

```bash
astra-nexus-nodriver-smoke
```

7. Только после успешного smoke запускай API или Telegram bot.

## Lifecycle и lock

Astra Nexus использует один browser profile:

```text
./data/browser_profiles/default
```

Для защиты от параллельного запуска создаётся lock:

```text
./data/runtime/nodriver/default.lock
```

В lock хранится PID процесса Astra Nexus, время старта, абсолютный `user_data_dir` и
контекст команды: `login`, `smoke`, `provider`, `deep_health`.

Если lock есть и PID живой, новая команда не открывает ещё одно окно Chrome. Она
выводит понятную ошибку с PID. Если lock устарел, `clean` или следующий управляемый
запуск удалит stale lock.

Не запускай одновременно:

- `astra-nexus-nodriver-login`;
- `astra-nexus-nodriver-smoke`;
- API deep health;
- Telegram/API flow с `BRAIN_PROVIDER=nodriver`.

## Безопасная очистка

```bash
astra-nexus-nodriver-clean
```

Команда показывает текущий lock, PID и живые процессы профиля. Она может удалить:

- stale lock Astra Nexus;
- `SingletonLock`;
- `SingletonSocket`;
- `SingletonCookie`;
- `DevToolsActivePort`.

Команда не удаляет cookies, session storage и сам browser profile. Если ты уже вошёл
в ChatGPT, не удаляй `data/browser_profiles/default`.

Если открылось несколько окон Chrome:

1. Закрой все окна, открытые NoDriver.
2. Выполни `astra-nexus-nodriver-clean`.
3. Повтори `astra-nexus-nodriver-smoke`.

## Diagnose и health

`diagnose` не открывает Chrome:

```bash
astra-nexus-nodriver-diagnose
```

Он показывает конфиг, абсолютные пути, lock, PID, наличие профиля и состояние
`profile_locked`.

Лёгкий health тоже не открывает Chrome:

```bash
curl http://127.0.0.1:8000/api/brain/health
```

Он возвращает `configured`, `unavailable` или `profile_locked`, а также `user_data_dir`,
`headless`, `chatgpt_url` и подсказку про smoke.

Глубокий health может открыть браузер:

```bash
curl http://127.0.0.1:8000/api/brain/health/deep
```

Используй deep health только когда не запущены login/smoke/API-provider с тем же
профилем.

## Статусы ошибок

- `profile_locked` - профиль занят живым процессом.
- `browser_connect_failed` - NoDriver не смог подключиться к Chrome.
- `chrome_start_timeout` - Chrome не поднялся за `NODRIVER_START_TIMEOUT_SECONDS`.
- `login_required` - нужно вручную войти через `astra-nexus-nodriver-login`.
- `stale_lock_cleaned` - безопасная очистка удалила устаревший lock.
- `timeout` - ChatGPT Web не ответил за заданное время.
- `selector_not_found` - UI ChatGPT изменился, нужно обновить selectors.

## Что нельзя коммитить

Не коммить:

- `.env`;
- `data/browser_profiles/`;
- `data/runtime/`;
- `data/debug/`;
- cookies и session files;
- screenshots из debug-режима.

Проект не обходит капчи, лимиты, блокировки и защитные механизмы. Если ChatGPT требует
ручное действие, Astra Nexus возвращает доменную ошибку.
