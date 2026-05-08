# MVP Plan

## Этап 1: backend + dummy agents

Создать FastAPI backend, SQLite-хранилище, модели, workspace, agent registry,
`DummyBrainProvider` и синхронный task flow.

## Этап 2: Telegram task flow

Подключить aiogram polling, отдельный bot entrypoint, создание задач из `/task`,
background runner, вывод сообщений агентов по шагам, `/status`, `/agents` и `/cancel`.

## Этап 3: NoDriver ChatGPT bridge

Реализовать `NoDriverProvider`, управление browser profile, ручной login helper,
health endpoint и безопасное хранение локальных сессий вне git.

## Этап 4: файлы и DOCX

Добавить загрузку входных файлов, генерацию Markdown/DOCX/PDF и выдачу артефактов в
Telegram.

## Этап 5: mini app/dashboard

Сделать лёгкую панель задач, просмотр runs, сообщений агентов, файлов и настроек
workspace.
