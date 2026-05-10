# AI Team Orchestration

`astra_nexus.team` - доменный слой AI-команды и тонкие integration-adapters для
будущего подключения к Telegram, storage и реальным providers.

Core orchestration/runtime слой не зависит от NoDriver, Telegram, FastAPI и SQLAlchemy.
Telegram bridge v1 лежит отдельным adapter-модулем и вызывает runtime-controller, не
меняя orchestration-логику.

## Состав слоя

- `TeamRun` - один запуск пользовательской задачи.
- `AgentProfile` - профиль агента: роль, имя, описание и системная инструкция.
- `AgentTask` - работа конкретного агента внутри run.
- `AgentResult` - текстовый результат агента.
- `RunEvent` - событие run или агента для будущего Telegram log-чата.
- `TeamMessage` - человекочитаемая реплика или статус для будущего UI/Telegram stream.

## Статусы и роли

Run использует `RunStatus`: `created`, `running`, `completed`, `failed`, `cancelled`.

Agent task использует `AgentTaskStatus`: `created`, `running`, `completed`, `failed`.

Pipeline по умолчанию:

1. `coordinator`
2. `analyst`
3. `critic`
4. `editor`
5. `qa_controller`
6. `final_composer`

## Provider contract

Агентский provider реализует `TeamProvider.generate(...)`:

- получает `AgentProfile`;
- получает исходную пользовательскую задачу;
- получает предыдущие `AgentResult`;
- получает собранный `AgentPrompt` с `system_prompt`, `user_prompt` и metadata;
- возвращает текстовый результат.

`AsyncTeamOrchestrator` зависит только от `TeamProvider` и не импортирует NoDriver provider.
Реальный ChatGPT/NoDriver bridge можно будет подключить позже через отдельный adapter.

## Prompt Engine

Prompt Engine находится в `astra_nexus.team.prompting`.

Основные сущности:

- `AgentContext` - run id, задача пользователя, текущая роль/имя агента, предыдущие
  результаты команды, события run, workspace path и дополнительные инструкции.
- `AgentPrompt` - готовая пара `system_prompt` / `user_prompt` плюс debug metadata.
- `TeamPromptBuilder` - собирает prompt для конкретного агента на основе профиля и
  текущего контекста.

`AsyncTeamOrchestrator` строит `AgentContext` перед каждым шагом pipeline, вызывает
`TeamPromptBuilder`, затем передаёт готовый `AgentPrompt` в provider. Старые поля
`profile`, `user_task` и `previous_results` остаются в provider contract, чтобы adapter
можно было писать постепенно и без привязки к NoDriver.

## Живые профили агентов

Профили агентов содержат display-поля для будущего UI/Telegram отображения:

- `display_name`
- `short_name`
- `short_description`
- `style_hint`
- `main_chat_intro`
- `responsibility_summary`
- `personality`
- `capabilities`
- `default_style`

Текущие роли:

- Артём / Координатор (`coordinator`) - понимает задачу, уточняет смысл, раскладывает
  работу на этапы и выдаёт план для команды.
- Ирина / Аналитик (`analyst`) - разбирает факты, структуру, вводные данные,
  ограничения и допущения.
- Вера / Критик (`critic`) - ищет слабые места, недостающие требования,
  противоречия и вопросы к решению.
- Лина / Редактор (`editor`) - берёт план и критику, усиливает текст или решение,
  сохраняя смысл задачи.
- Максим / Контроль качества (`qa_controller`) - проверяет готовый вариант,
  ошибки, пустые утверждения и недосказанность перед финалом.
- Саша / Финальный сборщик (`final_composer`) - собирает финальный ответ для
  пользователя и не показывает внутреннюю кухню, если пользователь этого не просил.

Эти поля не влияют на бизнес-логику как обязательные идентификаторы. Они нужны, чтобы
позже показывать команду в Telegram как понятных "живых участников", а не как сухие
служебные статусы.

## События

Сейчас создаются события:

- `run_started`
- `run_finished`
- `run_failed`
- `agent_started`
- `agent_finished`
- `agent_failed`
- `agent_retry_scheduled`
- `agent_retry_started`

Формат события уже содержит понятный `message` и `payload`, чтобы позже отдать эти данные
в Telegram log-chat без изменения domain-модели.

## Team Event Stream

Поверх технических `RunEvent` добавлен слой `TeamMessage` из
`astra_nexus.team.messages`. Он нужен, чтобы будущий Telegram bridge или dashboard могли
получать не только служебные события, но и короткие человеческие сообщения команды.

Основные сущности:

- `TeamMessage` - одна реплика, статус или технический лог.
- `TeamMessageType` - тип сообщения: `agent_says`, `agent_thinks`, `agent_started`,
  `agent_finished`, `agent_retry`, `agent_failed`, `run_started`, `run_finished`,
  `system_log`, `user_visible_status`.
- `TeamMessageChannel` - канал назначения: `main_chat`, `log_chat`, `debug`.
- `TeamMessageRenderer` - превращает `RunEvent` в короткие main/log сообщения.
- `TeamMessageSink` - абстракция получателя сообщений.
- `InMemoryTeamMessageSink`, `NullTeamMessageSink`, `CompositeTeamMessageSink` -
  локальные sink-реализации без Telegram API.

Каналы:

- `main_chat` - будущая переписка "живых" агентов: короткие реплики вроде
  "Босс, принял задачу. Сейчас разложу её на рабочий маршрут."
- `log_chat` - технический статус: event type, retry count, error code, run id,
  resume hint и другие детали для отдельного лог-бота.
- `debug` - внутренний канал для будущего dev/debug вывода.

Технические детали ошибок и retry не выводятся в `main_chat`. Они остаются в
`log_chat`, чтобы пользователь видел нормальную командную работу, а отладка не терялась.

Это внутренний stream/renderer. Telegram bridge v1 уже умеет забирать этот stream и
передавать его в Telegram output, но сам stream не зависит от Telegram API.

Важно: слой event stream не решает intent/router. Сейчас CLI-команды явно запускают run.
Чтобы обычная болтовня в Telegram не считалась задачей, позже нужен отдельный
intent/router layer.

## Team Dialogue v1

`astra_nexus.team.dialogue` добавляет живой transcript поверх последовательного pipeline.
Это не отдельные ChatGPT-чаты и не параллельные агенты: orchestrator по-прежнему выполняет
роли последовательно, но перед стартом и после результата каждого агента фиксирует короткую
реплику в командном чате.

Основные сущности:

- `TeamDialogueTurn` - одна реплика агента или команды: run id, роль, display name, фаза,
  текст, optional `reply_to_role`, timestamps и флаги видимости.
- `TeamDialogueTranscript` - список turns одного run.
- `TeamDialoguePhase` - `intake`, `coordination`, `analysis`, `critique`, `revision`,
  `qa`, `finalization`, `completed`, `failed`, `cancelled`.
- `TeamDialogueStyle` - краткая метка тона/назначения turn: working, summary, error.

Main chat теперь должен получать именно dialogue turns: короткие рабочие реплики вроде
"Босс, взял задачу. Сначала разложу её на нормальные шаги." Технические `RunEvent`
(`run_started`, `agent_started`, `agent_finished`, `run_finished`) остаются в `log_chat`.
Это разделяет будущую Telegram-супергруппу:

- `main_chat` - видимая командная переписка и финальный результат;
- `log_chat` - event type, run id, retry/error metadata и workspace details;
- `debug` - внутренний dev/debug stream.

Workspace дополнен файлами:

- `team_chat.json` - structured transcript для будущего UI/Telegram bridge.
- `team_chat.md` - человекочитаемый transcript.
- `run.json` содержит `dialogue_turns_count`.

Если файл сохранён как metadata-only и текст из него пока не извлечён, координатор пишет
нормальную рабочую реплику: файл виден, но команда будет работать по метаданным и тексту
задачи. Это не валит pipeline и не превращает main-chat в сухой технический статус.

Preview без NoDriver:

```bash
astra-nexus-team-dialogue-preview "проверь идею AI-команды"
astra-nexus-team-dialogue-preview --file docs/AI_TEAM.md "проверь файл"
```

## Team Intake / Intent Router

Перед `AsyncTeamOrchestrator` добавлен слой `astra_nexus.team.intake`. Он принимает
входящее сообщение пользователя и решает, нужно ли запускать команду, продолжать run или
просто ответить без orchestration.

Основные сущности:

- `TeamInput` - текст, количество вложений и контекст run: `active_run_id`,
  `last_run_id`, `failed_run_id`, `has_active_run`.
- `TeamInputIntent` - классификация входа.
- `TeamIntakeDecision` - intent, confidence, reason, флаги действия и
  `user_visible_reply`.
- `TeamIntakeRouter` - rule-based router без NoDriver, Telegram и LLM.
- `TeamConversationController` - минимальный controller, который вызывает router и
  запускает orchestrator только когда decision разрешает старт/resume run.

Текущие intents:

- `casual_chat` - обычная короткая реплика, команду не запускаем.
- `new_task` - новая текстовая задача для AI-команды.
- `task_followup` - уточнение к активному run.
- `revise_previous_result` - правка предыдущего результата.
- `file_task` - вход с вложениями.
- `status_request` - запрос статуса.
- `resume_run` - продолжение failed run.
- `stop_all` - команда остановки активных runs.
- `empty_input` - пустой ввод без файлов.
- `unknown` - ввод не совпал с текущими правилами.

Сейчас router намеренно rule-based. Он смотрит на короткие команды, явные глаголы задачи
(`сделай`, `проверь`, `напиши`, `составь`, `проанализируй`, `улучши`, `перепиши`,
`подготовь`, `разбери`), наличие файлов и run-контекст. Это защищает систему от ошибки,
когда обычная болтовня автоматически воспринимается как новая сложная задача.

Позже можно добавить LLM-router, но только поверх стабильной rule-based базы, чтобы
Telegram-диалог оставался предсказуемым.

## Team Runtime / Conversation Controller

`astra_nexus.team.runtime` связывает intake-router, orchestrator, workspace, resume-flow
и `TeamMessageSink` в один управляемый runtime-flow. Именно к этому слою позже должен
подключаться Telegram bridge: Telegram будет отдавать входящее сообщение в runtime, а не
вызывать `AsyncTeamOrchestrator` напрямую.

Основные сущности:

- `TeamRuntimeState` - in-memory состояние runtime: active runs, stopped runs,
  `last_run_id`, `last_completed_run_id`, `last_failed_run_id`.
- `TeamActiveRun` - запись активного run с cancellation-полями: `stop_requested`,
  `stopped_at`, `stop_reason`.
- `TeamRuntimeStatus` - статус ответа runtime: `idle`, `running`, `completed`,
  `failed`, `cancelled`.
- `TeamRuntimeResponse` - ответ controller для основного чата: decision, run id,
  final text, workspace path и `user_visible_reply`.
- `TeamConversationController` - принимает `TeamInput` или текст, вызывает
  `TeamIntakeRouter`, решает запускать/не запускать run, сохраняет workspace и обновляет
  state.

Обычный диалог (`casual_chat`), пустой ввод (`empty_input`) и неизвестный ввод
(`unknown`) не создают `TeamRun`. Runtime просто возвращает `user_visible_reply`.
Новый run создаётся только для intent, где это явно разрешено: `new_task`, file task с
текстом, а также временная обработка `task_followup` и `revise_previous_result` как новой
контекстной задачи.

`status_request` читает runtime state и возвращает активные runs или последний
completed/failed run. `stop_all` пока не убивает реальные provider/browser процессы, но
помечает active runs как stopped/cancelled и очищает in-memory registry. Это foundation
для будущей безопасной остановки Telegram-задач.

Текущие ограничения:

- registry только in-memory, без SQLite/Redis;
- нет реального parallel execution;
- Telegram API не подключён;
- stop/cancel пока архитектурный флаг, а не принудительное завершение NoDriver.

Preview runtime-flow без NoDriver:

```bash
astra-nexus-team-runtime-preview "брат че думаешь"
astra-nexus-team-runtime-preview "сделай краткий план AI-команды"
astra-nexus-team-runtime-preview "статус"
astra-nexus-team-runtime-preview "стоп все"
```

## Telegram Team Bridge v1

`astra_nexus.team.telegram_bridge` подключает `TeamConversationController` к aiogram как
тонкую оболочку:

```text
Telegram message -> TeamConversationController -> TeamMessageSink -> Telegram output
```

Bridge не вызывает `AsyncTeamOrchestrator` напрямую. Он передаёт входящее сообщение в
runtime, получает `TeamRuntimeResponse`, отправляет `user_visible_reply` в основной чат
и публикует `TeamMessage` из sink в Telegram:

- `main_chat` - короткие человеческие сообщения агентов и финальный ответ в основной чат;
- `log_chat` - технические события, retry, ошибки и run metadata в отдельный chat id,
  если задан `TEAM_TELEGRAM_LOG_CHAT_ID`;
- `debug` - внутренний канал, сейчас выводится только через log sink.

Поддержанные команды:

- `/status` - возвращает состояние активных и последних runs.
- `/stopall` - вызывает runtime `stop_all` и помечает активные runs как stopped/cancelled.

Provider mode:

- `TEAM_TELEGRAM_PROVIDER=fake` - безопасный режим по умолчанию, без NoDriver и без
  browser.
- `TEAM_TELEGRAM_PROVIDER=nodriver` - лениво подключает `NoDriverTeamProvider` и
  выполняет тот же последовательный pipeline через ChatGPT Web.

Дополнительные настройки:

- `TEAM_TELEGRAM_LOG_CHAT_ID` - отдельный chat id для технического лога.
- `TEAM_TELEGRAM_ALLOWED_CHAT_IDS` - comma-separated allowlist chat ids. Если пусто,
  bridge принимает все чаты.

Preview без реального Telegram и без NoDriver:

```bash
astra-nexus-team-telegram-preview "брат че думаешь"
astra-nexus-team-telegram-preview "сделай краткий план AI-команды"
astra-nexus-team-telegram-preview "/status"
astra-nexus-team-telegram-preview "/stopall"
```

Polling bot:

```bash
TELEGRAM_BOT_TOKEN=... astra-nexus-team-telegram-bot
```

Ограничения v1:

- pipeline остаётся последовательным;
- отдельных ChatGPT-чатов для агентов нет;
- полноценной файловой обработки сложных форматов нет;
- Telegram session registry пока in-memory;
- NoDriver lifecycle/start/clean не меняется.

## Telegram Team Jobs v1

Долгий team pipeline не должен выполняться внутри Telegram handler синхронно: иначе bot
не сможет отвечать на `/status`, `/stopall` и другие сообщения, пока агенты ждут provider.
Для этого добавлен `astra_nexus.team.jobs`.

Основные сущности:

- `TeamJob` - один background запуск команды для Telegram/session.
- `TeamJobStatus` - `pending`, `running`, `completed`, `failed`, `cancelled`.
- `TeamJobManager` - создаёт `asyncio.Task`, хранит active/last jobs и запрещает второй
  active job в том же чате.
- `TeamJobHandle` - handle для ожидания/cancel конкретной job.
- `TeamJobSnapshot` - короткий status view: job id, run id, workspace path, final text,
  error message и timestamps.

Поведение Telegram bridge:

- обычный `casual_chat`, `empty_input` и `unknown` не создают background job;
- новая задача создаёт `TeamJob` и сразу отвечает в основной чат:
  `Принял задачу. Команда начала работу.`;
- агентские `TeamMessage` продолжают уходить через Telegram sink по мере выполнения;
- `/status` во время active job показывает job id, статус и run id, если run уже создан;
- `/stopall` отменяет active job и сообщает, что команда остановлена;
- если job completed, bridge отправляет финальный ответ;
- если job failed, bridge отправляет понятное сообщение с run id, workspace path и resume
  hint, когда эти данные доступны;
- если в чате уже есть active job, новая задача не стартует: нужно дождаться результата
  или вызвать `/stopall`.

Preview нескольких сообщений без реального Telegram:

```bash
astra-nexus-team-telegram-job-preview \
  "сделай краткий план AI-команды" \
  "/status" \
  "/stopall"
```

Ограничения jobs v1:

- это фоновые Telegram jobs, а не параллельные агенты внутри pipeline;
- NoDriver lifecycle/start/clean не меняется;
- cancel best-effort: job получает `Task.cancel()`, но отдельный browser lifecycle не
  переписывается;
- registry остаётся in-memory;
- полноценной файловой обработки сложных форматов нет.

## Files for Team Tasks v1

Файловый foundation добавлен в `astra_nexus.team.attachments`. Он нужен, чтобы входящее
сообщение пользователя или Telegram-вложение могло стать частью team task без отдельной
БД и без тяжёлого parser pipeline.

Основные сущности:

- `TeamInputAttachment` - один файл: original/stored filename, content type, размер,
  source, local path, extracted text, extraction status и extraction error.
- `TeamAttachmentType` - `text`, `markdown`, `pdf`, `docx`, `binary`, `unknown`.
- `TeamAttachmentExtractionStatus` - `pending`, `extracted`, `metadata_only`,
  `unsupported`, `error`.
- `TeamAttachmentManifest` - список файлов run.
- `TeamAttachmentProcessor` - проверяет лимиты, читает `.txt`/`.md` и оставляет
  остальные форматы как metadata-only.

Настройки:

- `TEAM_ATTACHMENTS_MAX_FILES` / `ASTRA_TEAM_ATTACHMENTS_MAX_FILES`;
- `TEAM_ATTACHMENT_MAX_BYTES` / `ASTRA_TEAM_ATTACHMENT_MAX_BYTES`;
- `TEAM_ATTACHMENT_TEXT_MAX_CHARS` / `ASTRA_TEAM_ATTACHMENT_TEXT_MAX_CHARS`;
- `TEAM_UPLOADS_DIR` / `ASTRA_TEAM_UPLOADS_DIR`.

Extraction v1:

- `.txt` и `.md` читаются как UTF-8 text;
- извлечённый текст ограничивается `TEAM_ATTACHMENT_TEXT_MAX_CHARS`;
- `.pdf`, `.docx`, binary/unknown пока сохраняются как файл и metadata;
- ошибка чтения файла записывается в `extraction_error` и не валит pipeline.

Prompt context:

`TeamPromptBuilder` добавляет в prompt блок `Файлы пользователя`: список файлов,
размеры, content type, local path, extraction status и extracted text, если он есть.
Если текст не извлечён, агент явно видит, что файл доступен только как metadata/path.

Preview:

```bash
astra-nexus-team-file-preview --file docs/AI_TEAM.md "проверь файл"
astra-nexus-team-file-preview --file docs/ROADMAP.md
```

## Retry policy

AI Team pipeline выполняет агентов последовательно, но каждый агентский шаг может быть
повторён на уровне team orchestration при временной ошибке provider-а.

Настройки:

- `TEAM_AGENT_MAX_RETRIES` / `ASTRA_TEAM_AGENT_MAX_RETRIES` - число повторных попыток
  после первой неудачной попытки.
- `TEAM_AGENT_RETRY_DELAY_SECONDS` / `ASTRA_TEAM_AGENT_RETRY_DELAY_SECONDS` - пауза
  перед retry.
- `TEAM_AGENT_RESPONSE_TIMEOUT_SECONDS` / `ASTRA_TEAM_AGENT_RESPONSE_TIMEOUT_SECONDS` -
  внешний timeout на один агентский вызов.

Transient provider errors:

- `response_timeout`
- `browser_connect_failed`
- `prompt_insert_failed`
- `chatgpt_ui_not_ready`
- generic provider exceptions без явной permanent-классификации

Permanent provider errors не ретраятся:

- `login_required`
- `profile_locked`
- `prompt_box_not_found`
- `selector_not_found`

Retry-события пишутся в `events.jsonl` и `events.json`, поэтому будущий Telegram log-chat
сможет показать, какой агент был повторён и почему.

## Context limit

Чтобы поздние агенты не получали бесконечно растущий prompt, `TeamPromptBuilder`
ограничивает только prompt-контекст предыдущих результатов.

Настройка:

- `TEAM_PREVIOUS_RESULTS_MAX_CHARS` / `ASTRA_TEAM_PREVIOUS_RESULTS_MAX_CHARS`

Default: `16000`.

Если контекст сокращается, prompt получает пометку:

```text
Контекст предыдущих результатов сокращён...
```

Полные agent results не теряются и сохраняются в workspace.

## Fake provider

`FakeTeamProvider` используется в unit-тестах. Он возвращает детерминированные ответы и
умеет симулировать ошибку выбранного агента через `fail_on`.

Fake provider не импортирует NoDriver и не требует браузерной сессии. Он нужен для
быстрой проверки orchestration, prompts, событий и workspace.

## NoDriver team provider

`NoDriverTeamProvider` находится в `astra_nexus.team.nodriver_provider`.

Он реализует тот же `TeamProvider.generate(...)`, но внутри использует существующий
`NoDriverProvider` из `astra_nexus.brain.nodriver_provider`. Browser lifecycle,
ChatGPT client, retry/debug поведение и локальный browser profile остаются в NoDriver
слое; team adapter не дублирует browser-логику.

Так как текущий `NoDriverProvider.ask(...)` принимает один текстовый prompt, adapter
склеивает `AgentPrompt.system_prompt` и `AgentPrompt.user_prompt` в полный prompt:

1. системная инструкция агента;
2. задача пользователя;
3. предыдущие результаты команды;
4. инструкция текущего агента.

Этот prompt отправляется в существующий `NoDriverProvider.ask(...)` с `agent_id` текущей
роли и контекстом run/workspace для debug-reporting.

## CLI smoke

Локальный smoke-запуск AI-команды выполняется командой:

```bash
astra-nexus-team-smoke "Составь краткий план улучшения Astra Nexus."
```

Если текст задачи не передан, команда использует дефолт:

```text
Составь краткий план улучшения Astra Nexus.
```

Команда использует только `FakeTeamProvider`, запускает `AsyncTeamOrchestrator`, сохраняет
workspace run и печатает:

- `status`
- `run_id`
- `final_result`
- `workspace_path`

## CLI chat preview

Псевдо-чат AI-команды без NoDriver и без Telegram можно посмотреть командой:

```bash
astra-nexus-team-chat-preview "Проверь идею AI-команды для Astra Nexus."
```

Команда использует только `FakeTeamProvider` и печатает stream сообщений:

```text
[Команда] ...
[Артём] ...
[Лог] ...
```

По умолчанию показываются `main_chat` и `log_chat`. Только main-chat:

```bash
astra-nexus-team-chat-preview --main-only "Проверь идею AI-команды для Astra Nexus."
```

## CLI intake preview

Посмотреть routing decision без запуска команды можно так:

```bash
astra-nexus-team-intake-preview "брат че думаешь"
astra-nexus-team-intake-preview "сделай подробный план AI-команды"
astra-nexus-team-intake-preview "стоп все"
```

Команда печатает `intent`, `confidence`, `reason`, action-флаги и
`user_visible_reply`. Она не импортирует NoDriver и не запускает browser.

## CLI real team ask

Реальный запуск команды через ChatGPT Web/NoDriver выполняется командой:

```bash
astra-nexus-team-ask "Ответь кратко: что такое Astra Nexus?"
```

Если текст задачи не передан, используется дефолт:

```text
Ответь кратко: что такое Astra Nexus?
```

Команда запускает тот же последовательный `AsyncTeamOrchestrator`, но с
`NoDriverTeamProvider`, сохраняет workspace run и печатает:

- `status`
- `run_id`
- `workspace_path`
- `final_result`

Перед реальным запуском нужно подготовить локальную ChatGPT Web сессию обычными
NoDriver-командами:

```bash
astra-nexus-nodriver-login
astra-nexus-nodriver-smoke
```

`astra-nexus-team-ask` пока использует один общий ChatGPT Web provider последовательно.
Параллельные агенты, отдельные ChatGPT-чаты и отдельные agent sessions будут добавлены
позже.

Если run завершился с ошибкой, команда печатает подсказку:

```text
Можно продолжить: astra-nexus-team-resume <run_id>
```

## CLI resume

Failed run можно продолжить из сохранённого workspace:

```bash
astra-nexus-team-resume <run_id>
```

Команда:

- читает `data/team_runs/<run_id>/`;
- восстанавливает completed agent results;
- пропускает уже completed агентов;
- продолжает pipeline с первого failed/not-started шага;
- обновляет workspace и печатает `status`, `run_id`, `workspace_path`, `final_result`.

## Team run workspace

Каждый smoke-run сохраняется в:

```text
data/team_runs/<run_id>/
```

Структура папки:

```text
data/team_runs/<run_id>/
  run.json
  attachments.json
  attachments.md
  events.jsonl
  final.md
  tasks.json
  results.json
  events.json
  messages.json
  messages.md
  input_files/
  agent_results/
    coordinator.md
    analyst.md
    critic.md
    editor.md
    qa_controller.md
    final_composer.md
```

`run.json` содержит общий summary run: `run_id`, `status`, `user_task`, временные поля,
финальный результат, количество attachments и краткий список agent task/result summary.

`input_files/` содержит копии исходных файлов, привязанных к run.

`attachments.json` содержит machine-readable manifest: original/stored filename,
content type, size, source, local path, extraction status, extraction error и extracted
text, если он был получен.

`attachments.md` содержит человекочитаемое описание файлов и извлечённый текст для
локального анализа.

`events.jsonl` содержит одну JSON-строку на событие: timestamp, event type, run id,
роль агента для agent-событий, message и details. Этот формат готовит слой к будущему
Telegram log/chat отображению без подключения Telegram на текущем этапе.

`tasks.json`, `results.json` и `events.json` нужны для машинного восстановления failed
run через `astra-nexus-team-resume`. Markdown-файлы остаются человекочитаемым отчётом.

`messages.json` содержит machine-readable stream `TeamMessage`, а `messages.md` -
человекочитаемый transcript `main_chat` и `log_chat`. При resume новые сообщения
добавляются к существующей истории run.

`agent_results/*.md` содержит человекочитаемый результат каждого агента: имя, роль,
исходную задачу, статус и текст результата.

Если у результата есть prompt debug metadata, workspace добавляет секцию
`Внутренний prompt` в соответствующий `agent_results/*.md`. Это dev/debug слой для
локального анализа качества prompts; Telegram пока его не использует.

`final.md` содержит финальный ответ `final_composer`.

## Что пока не реализовано

- Полноценная Telegram-супергруппа с отдельными agent identities.
- Параллельные агенты.
- Отдельные ChatGPT-чаты на агента.
- Self-improving/Codex-режим.
- Storage/migrations для team-сущностей.
- Полноценный PDF/DOCX parser.
