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
NODRIVER_PROVIDER_WINDOW_MODE=offscreen
NODRIVER_LOGIN_WINDOW_MODE=small
NODRIVER_WINDOW_WIDTH=1100
NODRIVER_WINDOW_HEIGHT=800
NODRIVER_WINDOW_X=20
NODRIVER_WINDOW_Y=20
NODRIVER_MINIMIZE_AFTER_START=true
NODRIVER_HIDE_AFTER_START=false
NODRIVER_OFFSCREEN_X=-3000
NODRIVER_OFFSCREEN_Y=20
NODRIVER_BACKGROUND_START=false
NODRIVER_DISABLE_FOCUS_STEALING=false
NODRIVER_CHATGPT_URL=https://chatgpt.com/
NODRIVER_RESPONSE_TIMEOUT_SECONDS=180
NODRIVER_RESPONSE_IDLE_CONFIRM_SECONDS=2.0
NODRIVER_RESPONSE_PROGRESS_LOG_INTERVAL_SECONDS=30.0
NODRIVER_RESPONSE_MAX_EMPTY_WAIT_SECONDS=
NODRIVER_PREFERRED_MODEL_NAME=
NODRIVER_PREFERRED_REASONING_MODE=
NODRIVER_REQUIRE_PREFERRED_MODEL=false
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

Window mode разделён по контекстам:

- `NODRIVER_PROVIDER_WINDOW_MODE=offscreen` используется для smoke/ask/provider/Telegram.
- `NODRIVER_LOGIN_WINDOW_MODE=small` используется для login/debug, где окно должно быть
  видимым.
- `NODRIVER_WINDOW_MODE` остаётся общим fallback.

Поддерживаются режимы `visible`, `small`, `minimized`, `offscreen` и
`headless_experimental`. Старые значения `normal` и `headless` принимаются как alias.
`offscreen` ставит окно в `NODRIVER_OFFSCREEN_X/Y`; `minimized` и
`NODRIVER_MINIMIZE_AFTER_START=true` дополнительно пытаются свернуть Chrome после
успешного подключения. На macOS это best-effort через AppleScript/System Events: если
свернуть или скрыть окно не удалось, provider не падает, а пишет warning в диагностику.

Если первая попытка упала на `Failed to connect to browser` с configured window args,
следующая попытка автоматически включает minimal fallback: Chrome стартует без
`--window-size`, `--window-position` и фоновых window tweaks. Для login/debug контекстов
сохраняется видимое small-окно.

`NODRIVER_WINDOW_MODE=headless_experimental` включает headless только явно для
автоматических smoke/ask/provider запусков. Старый `NODRIVER_HEADLESS=true` тоже
продолжает включать headless для автоматических запусков.
`NODRIVER_BACKGROUND_START` и `NODRIVER_DISABLE_FOCUS_STEALING` документируют желаемое
поведение. На macOS эти режимы небезопасны как default: Chrome/NoDriver не дают
надёжного cross-platform запрета на перехват фокуса при создании окна, а агрессивное
фоновое окно может ломать подключение. Поэтому оба флага по умолчанию `false`; если
включаешь их вручную, считай это best-effort, а не гарантией.
`NODRIVER_START_TIMEOUT_SECONDS` передаётся в NoDriver, но сам NoDriver иногда
возвращает `Failed to connect to browser` раньше этого timeout. Поэтому Astra Nexus
явно выбирает `remote-debugging-port`, проверяет Chrome process, ждёт `/json/version`
на этом порту до `NODRIVER_START_TIMEOUT_SECONDS` и, если endpoint дозрел, повторно
подключается к уже поднятому Chrome. Если endpoint так и не открылся, срабатывает
внешний retry-цикл: `NODRIVER_START_RETRIES` и
`NODRIVER_START_RETRY_DELAY_SECONDS`. Если failed start успел поднять Chrome, Astra
Nexus завершает только процесс, появившийся в этой попытке, ждёт
`NODRIVER_AFTER_TERMINATE_GRACE_SECONDS`, отпускает свой runtime lock и безопасно
удаляет только `SingletonLock`, `SingletonSocket`, `SingletonCookie` и
`DevToolsActivePort`, если профиль уже свободен.

Ожидание ответа ChatGPT Web использует state machine, а не stable-text эвристику. Ответ
считается завершённым только после появления нового assistant segment и финального
idle-состояния UI: нет stop/cancel generation, prompt снова доступен, send button idle,
нет visible thinking/generating/tool/progress indicators и нет continue/resume/try again
состояния. Для extended thinking можно поставить:

```env
NODRIVER_RESPONSE_TIMEOUT_SECONDS=0
NODRIVER_RESPONSE_MAX_EMPTY_WAIT_SECONDS=
```

`NODRIVER_RESPONSE_TIMEOUT_SECONDS=0` или отрицательное значение отключает hard timeout.
`NODRIVER_RESPONSE_IDLE_CONFIRM_SECONDS` - короткое подтверждение финального idle UI, не
stable-text timeout. `NODRIVER_RESPONSE_PROGRESS_LOG_INTERVAL_SECONDS` задаёт период
progress log. `NODRIVER_RESPONSE_MAX_EMPTY_WAIT_SECONDS` ограничивает ожидание первого
assistant segment; для extended thinking оставляй пустым или ставь большое значение.

Если нужно контролировать выбранный режим ChatGPT Web, задай
`NODRIVER_PREFERRED_MODEL_NAME` и `NODRIVER_PREFERRED_REASONING_MODE`. Client пытается
прочитать текущую выбранную модель из UI. При `NODRIVER_REQUIRE_PREFERRED_MODEL=true`
задача не стартует, если модель не похожа на preferred или её нельзя определить.
Автоматический click-flow выбора модели намеренно не реализован: UI ChatGPT слишком
хрупкий для безопасного hardcoded выбора.

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

Для проверки file artifact flow есть отдельные команды:

```bash
astra-nexus-nodriver-dump-artifacts --json
astra-nexus-nodriver-artifact-test --format docx --workspace data/debug/nodriver/artifact_test \
  "Сделай короткий Word-файл с планом проверки Astra Nexus"
```

`dump-artifacts` не отправляет prompt: он смотрит текущий assistant turn и печатает
artifact candidates, rejected candidates, filename/extension, download url и button id.
`artifact-test` отправляет prompt с прямым требованием создать downloadable file,
сохраняет debug в workspace и включает тот же retry/download manager, что Telegram flow.

8. Если ask падает на `prompt_insert_failed`, проверь только вставку без отправки:

```bash
astra-nexus-nodriver-insert-probe "Ответь одним предложением: Astra Nexus online."
```

9. Только после успешных smoke и ask запускай API или Telegram bot.

## ChatGPT Web Artifact Downloader v1

Когда AI Team final composer получает `output_requested_as_file=true`,
NoDriver prompt требует не текстовую имитацию файла, а настоящий downloadable file в
ChatGPT Web. После финального idle-состояния `ChatGPTClient` запускает artifact detector
по текущему assistant turn и ищет:

- file card;
- download button;
- attachment;
- filename chip;
- ссылку или кнопку скачивания.

Detector пишет `artifact_detector_debug.json` с accepted/rejected candidates, HTML
snippet, visible text, detected filename/extension и download url/button info.

Download manager создаёт run-local directories:

```text
<workspace>/
  requested_files/
    downloads/
  requested_file_request.json
  requested_file_download_result.json
  artifact_detector_debug.json
```

Перед кликом NoDriver выставляет Chrome download path на
`requested_files/downloads/`. После клика он ждёт настоящий завершённый файл, игнорирует
`.crdownload`, `.tmp`, `.partial`, `.part`, проверяет размер и ожидаемое расширение,
затем переносит результат в `requested_files/`.

Если ChatGPT написал, что файл готов, но UI не содержит file card/download button,
это не success. Client делает один retry с прямой инструкцией создать downloadable file.
Если после retry файла нет, provider возвращает `requested_file_missing`; Telegram
показывает короткую ошибку пользователю, а детали остаются в log chat и workspace JSON.

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

`doctor` открывает Chrome без полного ask и проверяет DevTools endpoint:

```bash
astra-nexus-nodriver-doctor
```

Он выводит выбранный `remote_debugging_port`, effective window mode, Chrome args,
появился ли Chrome process, открылся ли endpoint, сколько секунд его ждали, а также
desired model/reasoning настройки.

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
- `response_timeout` - включённый hard timeout истёк до финального idle-состояния UI.
- `preferred_model_not_active` - включён `NODRIVER_REQUIRE_PREFERRED_MODEL`, но текущая
  модель ChatGPT Web не похожа на desired model или не определена.
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
