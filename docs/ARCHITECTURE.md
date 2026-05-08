# Архитектура Astra Nexus

Astra Nexus - Telegram-first AI Office, где Telegram является интерфейсом и видимым
рабочим логом, backend управляет задачами, а brain-provider отвечает только за генерацию
ответов агентов.

## Слои

1. Telegram gateway принимает команды пользователя и показывает ход работы.
2. FastAPI backend даёт API для задач, агентов и health-check.
3. Task orchestrator управляет жизненным циклом задачи и порядком вызова агентов.
4. Agent layer содержит роли и правила подготовки prompt.
5. Brain provider является заменяемым мостом к источнику интеллекта.
6. DB layer хранит задачи, запуски, агентов, сообщения и артефакты.
7. Workspace layer хранит файлы задачи и `events.jsonl`.

## Процессы запуска

Backend API и Telegram bot запускаются отдельно:

- `astra-nexus-api` или `python -m astra_nexus.main`;
- `astra-nexus-bot` или `python -m astra_nexus.telegram.run_bot`.
- `astra-nexus-nodriver-login` для ручной подготовки локального ChatGPT Web profile.

Оба процесса используют один bootstrap контейнер сервисов, одну SQLite базу и один
workspace root.

## Почему Telegram - интерфейс

Telegram уже является привычным рабочим каналом: пользователь ставит задачу в чате,
видит реплики агентов и получает итог там же. Backend не должен знать деталей UI сверх
команд и формата ответа.

## Почему backend - мозг управления

Backend отвечает за состояние, порядок шагов, сохранение сообщений и контроль артефактов.
Brain-provider не должен решать, какие агенты существуют и когда задача завершена.

## Почему NoDriver - brain bridge

Будущий NoDriver + ChatGPT Web слой будет адаптером к ChatGPT Web. Он должен
реализовывать контракт `BrainProvider.ask(...)`, чтобы его можно было заменить без
переписывания оркестратора, сервисов и Telegram handlers.

## Почему нельзя делать монолит

Монолитный файл быстро смешает Telegram, SQLAlchemy, агента, workspace и браузерную
автоматизацию. Это усложнит замену brain-provider, тестирование task flow и аудит
безопасности. Поэтому каждый слой вынесен в отдельные модули.

## Как общаются агенты

В MVP агенты вызываются последовательно: Coordinator, Researcher, Writer, Critic,
Finalizer. Каждый следующий агент получает краткий контекст предыдущих сообщений.
Переписка сохраняется в таблицу `agent_messages`.

Orchestrator не импортирует aiogram. Вместо этого он создаёт доменные события:
`task.created`, `task.stage_changed`, `agent.message`, `task.done`, `task.failed`.
Telegram layer передаёт callback/event sink, форматирует события и отправляет их в чат.

## Dummy-flow

До подключения NoDriver используется `DummyBrainProvider`. Он возвращает
детерминированные структурированные ответы, чтобы можно было проверять Telegram flow,
историю сообщений, workspace и финальные артефакты без платных API.

`NoDriverProvider` реализует тот же контракт `BrainProvider.ask(...)`, но отправляет
prompt в ChatGPT Web через локальную браузерную сессию. Это bridge, а не ядро системы:
orchestrator, агенты, Telegram и DB не знают деталей браузерной автоматизации.

Ошибки browser bridge преобразуются в доменные статусы `login_required`, `timeout`,
`selector_not_found`, `unavailable`; Telegram показывает короткое действие вместо
traceback.
