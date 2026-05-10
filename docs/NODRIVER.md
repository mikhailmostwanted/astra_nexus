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
NODRIVER_WINDOW_MODE=small
NODRIVER_WINDOW_WIDTH=1100
NODRIVER_WINDOW_HEIGHT=800
NODRIVER_WINDOW_X=20
NODRIVER_WINDOW_Y=20
NODRIVER_BACKGROUND_START=true
NODRIVER_DISABLE_FOCUS_STEALING=true
NODRIVER_CHATGPT_URL=https://chatgpt.com/
NODRIVER_RESPONSE_TIMEOUT_SECONDS=180
NODRIVER_PAGE_LOAD_TIMEOUT_SECONDS=60
NODRIVER_KEEP_BROWSER_OPEN_ON_ERROR=false
NODRIVER_START_TIMEOUT_SECONDS=90
NODRIVER_START_RETRIES=3
NODRIVER_START_RETRY_DELAY_SECONDS=2
NODRIVER_AFTER_TERMINATE_GRACE_SECONDS=2
NODRIVER_NO_SANDBOX=false
NODRIVER_BROWSER_EXECUTABLE_PATH=
NODRIVER_AGENT_MODE=single_profile
NODRIVER_DEBUG_SCREENSHOTS=false
NODRIVER_SCREENSHOTS_DIR=./data/debug/screenshots
```

Если `NODRIVER_BROWSER_EXECUTABLE_PATH` пустой, NoDriver сам ищет Chrome/Chromium.
Если путь задан, он передаётся в NoDriver как абсолютный путь.
`NODRIVER_WINDOW_MODE=small` по умолчанию запускает Chrome небольшим окном через
`--window-size` и `--window-position`, чтобы smoke/ask/team/Telegram режимы не
разворачивали браузер на весь экран.
`NODRIVER_WINDOW_MODE=normal` не добавляет window args и оставляет старое поведение
NoDriver/Chrome.
`NODRIVER_WINDOW_MODE=offscreen` добавляет позицию `--window-position=-32000,-32000`.
Это удобно для фоновых проверок, но на macOS/Chrome нельзя гарантировать полный запрет
focus stealing: система всё равно может кратко активировать новое окно. Astra Nexus
делает best-effort через размер/позицию окна и не включает headless без явной настройки.
`NODRIVER_WINDOW_MODE=headless` включает headless только явно. Старый
`NODRIVER_HEADLESS=true` тоже продолжает включать headless.
Для `astra-nexus-nodriver-login`, `astra-nexus-nodriver-dom-probe` и
`astra-nexus-nodriver-insert-probe` offscreen/headless window mode автоматически
превращается в видимый small mode, если `NODRIVER_HEADLESS=true` не задан напрямую:
ручной вход и debug-проверки должны оставаться удобными.
`NODRIVER_BACKGROUND_START` и `NODRIVER_DISABLE_FOCUS_STEALING` документируют желаемое
поведение. В текущей реализации это безопасный best-effort, а не жёсткая гарантия
macOS, потому что Chrome/NoDriver не дают надёжного cross-platform запрета на
перехват фокуса при создании окна.
`NODRIVER_START_TIMEOUT_SECONDS` передаётся в NoDriver, но сам NoDriver иногда
возвращает `Failed to connect to browser` раньше этого timeout. Поэтому вокруг старта
есть внешний retry-цикл: `NODRIVER_START_RETRIES` и
`NODRIVER_START_RETRY_DELAY_SECONDS`. Если failed start успел поднять Chrome, Astra
Nexus завершает только процесс, появившийся в этой попытке, ждёт
`NODRIVER_AFTER_TERMINATE_GRACE_SECONDS`, отпускает свой runtime lock и безопасно
удаляет только `SingletonLock`, `SingletonSocket`, `SingletonCookie` и
`DevToolsActivePort`, если профиль уже свободен.

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
5. Когда увидишь поле ввода ChatGPT, нажми Enter в терминале с login helper.
   Команда выполнит DOM probe и напишет `status: ok` только если поле ввода найдено.
6. Проверь реальный browser bridge:

```bash
astra-nexus-nodriver-smoke
```

7. Проверь один произвольный prompt без Telegram:

```bash
astra-nexus-nodriver-ask "Ответь одним предложением: Astra Nexus online."
```

8. Если ask падает на `prompt_insert_failed`, проверь только вставку без отправки:

```bash
astra-nexus-nodriver-insert-probe "Ответь одним предложением: Astra Nexus online."
```

9. Только после успешных smoke и ask запускай API или Telegram bot.

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
контекст команды: `login`, `smoke`, `insert_probe`, `provider`, `deep_health`.

Если lock есть и PID живой, новая команда не открывает ещё одно окно Chrome. Она
выводит понятную ошибку с PID. Если lock устарел, `clean` или следующий управляемый
запуск удалит stale lock.

Не запускай одновременно:

- `astra-nexus-nodriver-login`;
- `astra-nexus-nodriver-smoke`;
- `astra-nexus-nodriver-ask`;
- `astra-nexus-nodriver-dom-probe`;
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

## Ручная проверка brain без Telegram

```bash
astra-nexus-nodriver-ask "Объясни в 5 предложениях, что такое Astra Nexus"
```

Команда использует тот же `NoDriverProvider`, но не запускает orchestrator и Telegram.
Если ошибка в NoDriver, вывод будет коротким:

```text
status: prompt_box_not_found
stage: chatgpt.prompt_box.search.started
message: Поле ввода ChatGPT не найдено.
action: ...
```

Это основной способ отделить проблему ChatGPT Web bridge от Telegram task flow.
При `prompt_insert_failed` команда сохраняет подробный отчёт в
`data/debug/nodriver/prompt_insert_failed.json`: URL, title, activeElement,
найденный selector, укороченный `outerHTML`, DOM probe summary и попытки вставки.

Для проверки одного этапа вставки без отправки сообщения используй:

```bash
astra-nexus-nodriver-insert-probe "тестовый текст"
```

## DOM probe и проверка входа

Собери безопасную DOM-диагностику:

```bash
astra-nexus-nodriver-dom-probe
```

Команда не отправляет prompt и не сохраняет HTML, cookies или сообщения. Она выводит
`current_url`, `page_title`, `ready_state`, количество `textarea`,
`contenteditable`, `role=textbox`, `login_buttons_count`, `candidate_count`,
`login_state` и список candidate-элементов с безопасными метаданными. JSON пишется в:

```text
data/debug/nodriver/dom_probe.json
```

В `dom_probe.json` также сохраняются `raw_evaluate_result_type`,
`raw_evaluate_result_repr`, `normalized_result`, `timestamp` и `exception`. Если
слой JavaScript evaluate сломан, команда возвращает `status: evaluate_failed`; в этом
случае не нужно обновлять selectors, сначала смотри raw/normalized результат в отчёте.

Если `login_state: login_required`, выполни `astra-nexus-nodriver-login` и войди
заново. Если `candidate_count: 0` при `login_state: chatgpt_ui_not_ready`, открой
`dom_probe.json` и проверь, какие безопасные признаки страницы увидел probe.

Чтобы оставить Chrome открытым при ошибке login/dom-probe/smoke/ask:

```env
NODRIVER_KEEP_BROWSER_OPEN_ON_ERROR=true
```

В этом режиме команда при ошибке
печатают сообщение:

```text
Браузер оставлен открытым. Проверь страницу и нажми Enter для закрытия.
```

После Enter браузер закрывается и lifecycle lock освобождается.

## Как дебажить Telegram /task + NoDriver

1. Закрой лишние Chrome/Chromium окна.
2. Выполни `astra-nexus-nodriver-clean`.
3. Выполни `astra-nexus-nodriver-login`, войди в ChatGPT и нажми Enter.
4. Выполни `astra-nexus-nodriver-smoke`.
5. Выполни `astra-nexus-nodriver-ask "Ответь одним предложением: Astra Nexus online."`.
6. Запусти `astra-nexus-bot`.
7. Отправь `/task ...` в Telegram.
8. Если задача упала, посмотри `data/workspaces/{task_id}/debug/nodriver_error.json`.

Telegram показывает `task_id`, `stage`, `agent`, `provider`, `error_code` и
человеческое сообщение. Traceback остаётся только в server logs.

NoDriver не перезагружает ChatGPT перед каждым агентом, если текущая вкладка уже на
`chatgpt.com`. Новая загрузка выполняется только для пустой вкладки, другого домена или
явного reload.

## Статусы ошибок

- `profile_locked` - профиль занят живым процессом.
- `browser_connect_failed` - NoDriver не смог подключиться к Chrome.
- `chrome_start_timeout` - Chrome не поднялся за `NODRIVER_START_TIMEOUT_SECONDS`.
- `login_required` - нужно вручную войти через `astra-nexus-nodriver-login`.
- `chatgpt_ui_not_ready` - ChatGPT открылся, но поле ввода не найдено.
- `prompt_box_not_found` - поле ввода ChatGPT не найдено на текущей странице.
- `stale_lock_cleaned` - безопасная очистка удалила устаревший lock.
- `response_timeout` - ChatGPT Web не ответил за заданное время.
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
