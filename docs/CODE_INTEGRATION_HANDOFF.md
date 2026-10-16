# Продолжение интеграции Claude Code в Layla

## Область и ветки

Работа ограничена Code. Другие домены не исследовать и не менять.
Текущий запрос пользователя: добавить рекомендованные встроенные
`/simplify`, `/skillify`, `/remember`; плагины отложены.
Успешный CI отдельного этапа не означает полного соответствия архиву.

- PR #39: `codex/code-workspace-shell-teams` — worktree editor/terminal/Git/LSP,
  выбор ref, import/export/fork сессий, draft PR, протокол команды и PTY.
- PR #40: `codex/code-integration-continuation`, commit
  `64fd0def02ccede872b0b4ce8b86bc958ef71290` — проектные/вложенные инструкции,
  rules/imports и output styles; контракт: [CODE_INSTRUCTIONS.md](CODE_INSTRUCTIONS.md).
- PR #41: `codex/code-mcp-scheduler`, commit `1cf870a7ca1645498daffec1ad2af76f5719853d`, основан на PR #40.
- PR #42: `codex/code-plans-context-budgets`, commit
  `0ebe728ef23121ac76d0947a3af8cda6b8ce6f78`, основан на PR #41.
- PR #43: `codex/code-rules-skills`, commit
  `175484730fa9dea43c5964f38741887c7bb8e94c`, основан на PR #42.
- Текущая ветка: `codex/code-bundled-skills`, основана на PR #43.
  Новый PR должен использовать `codex/code-rules-skills` как base.
  Не переносить эти изменения прямо с main без зависимых PR.

## Реализовано сейчас

Три встроенных Code skills поставляются с Core и доступны без файлов проекта:
`/simplify` (до трёх read-only ревьюеров или самостоятельная проверка),
`/skillify` (создание проектного skill только внутри `.layla/skills/**`, шаблон
в ресурсах), `/remember` (обзор памяти и правил, без изменения/удаления).
Последние два требуют явного slash-вызова. Проектная версия имеет приоритет
в своей области, ошибочная версия не заменяется молча встроенной. Ресурсы
читаются только из manifest пакета; CI проверяет их из собранного wheel.
UI помечает встроенные skills. Новых зависимостей, миграции БД или версии
sandbox нет; для установки нужна пересборка Core/frontend.

Rules: bounded brace/extglob/negative glob sets, settings exclusions,
Markdown-aware includes/comments, независимые условные bindings общего include,
обнаружение правил для путей/cwd до pre-hooks и действий.
Skills: вложенные/условные каталоги, namespaces, ресурсы, общий снимок read/invoke,
worktree/path picker и передача выбранного пути в slash-запуск. Ограничения
аргументов `allowed-tools`, enabled model/effort текущего провайдера,
динамический контекст через обычный sandbox runner с hooks/Confirm/checkpoints.
Fork и resume сохраняют ограничения и настройки, в том числе после inline skill
внутри воркера; final-save не может затереть более новую политику старым payload.
Контракты: [CODE_INSTRUCTIONS.md](CODE_INSTRUCTIONS.md),
[CODE_WORKFLOWS.md](CODE_WORKFLOWS.md). Новая миграция не требуется.
Нужно пересобрать Core (markdown-it-py, wcmatch, regex) и frontend.

Пункты 1/2 по запросу пользователя: enter_plan_mode/submit_plan, неизменяемый
план и решение пользователя, запрет обхода pending-плана через Auto, продолжение
по сохранённому checkpoint, компактные replay views и context_search,
консолидация явных заметок без удаления источников и общий token/USD бюджет
для ведущего, воркеров, повторов и сводок. Контракт/границы:
[CODE_PLANS_CONTEXT.md](CODE_PLANS_CONTEXT.md). Миграция `0034_code_control`.


Code MCP: явный браузерный OAuth PKCE, metadata discovery, CIMD/DCR/готовый
client ID, зашифрованные persistent credentials и регистрация клиента,
refresh с lease/revision и отзыв. Отдельный пул соединений между сообщениями,
reconnect, notifications и обновление каталога, двоичные файлы с авторизованной
загрузкой и лимитами. Другие домены не переведены на эти Code-механизмы.

Code-планировщик: cron/разовая дата, timezone/DST, durable state, lease и
идемпотентная постановка обычного Code Job, busy retry, объединение пропущенных
повторений, pause/resume/delete, панель чата и cron tools. Привязка к владельцу,
чату, проекту и активной рабочей копии; Confirm/Plan/Review сохраняют ограничения.
Точный контракт и оставшиеся пробелы: [CODE_MCP_SCHEDULER.md](CODE_MCP_SCHEDULER.md).

Для развёртывания нужны пересборка Core/frontend, `alembic upgrade head`
(миграции `0032`, `0033` и `0034`) и корректный внешний `LAYLA_PUBLIC_URL` для OAuth.
Живое production-развёртывание в этом этапе не выполнялось.

## Проверки

Из `core`:

```bash
pytest -q tests/test_code_*.py tests/test_project_agent.py tests/test_history.py tests/test_mcp.py
```

В PR #42 прошли 260 Code/shared-provider тестов, в PR #43 — 294.
Текущий этап добавляет 14 сценариев встроенных skills. Все 308 Code/shared-provider
тестов проходят локально, включая реальные API/Jobs, read-only memory policy,
создание skill с ограничением записи и три независимых review-воркера.
Ruff, TypeScript, ESLint и чтение трёх skills/шаблона из собранного wheel проходят;
полный прогон, упаковку, PostgreSQL и frontend дополнительно проверяет CI.
Есть настоящий stdio MCP fixture и запуск
расписания через реальные API/БД/Jobs с тестовым provider stream. Проверены
upgrade с `0031`, отсутствие ORM drift и downgrade с сохранением пользователя.
Ruff изменённых Python-файлов и TypeScript проходят; ESLint без предупреждений.
Локальная production-сборка Next блокируется отсутствием `/proc` в среде
(`uv_resident_set_memory`); frontend сборка PR #42 прошла CI, текущую проверяет CI нового PR.
Workflow `Code contracts` отдельно проверяет PostgreSQL миграции и Code/provider
контракты, не завися от тестов других доменов.
Внешний OAuth-провайдер в production локальные тесты не заменяют.

## Реестр и остаток

[CODE_PARITY.md](CODE_PARITY.md) и [code-parity.json](code-parity.json)
содержат полный инвентарь 1907 файлов, 40 инструментов и 33 подсистем.
`full_parity` остаётся `false`; MCP и scheduler отмечены `partial` с конкретными
пробелами. Digest приложенного ZIP:
`f74f54805f280d8535bb0e3767fe7712392e77745d7e91ac9fbedd18891d55eb`.

Остаются полная архивная схема plan-file/permission proposals, настройки агента,
общая/managed/командная память и dream loop, оставшиеся hooks/rules/skills,
MCP sampling/elicitation/prompts и полный lifecycle, архивные scheduler jitter/
auto-expiry/session-only/teammate routes, remote triggers/sessions, проактивный
цикл, полная совместимость context recovery и provider billing, прочие shell/IDE/CLI/browser/voice/sync
контракты. Плагины отложены; не начинать их без смены текущего приоритета.

Сообщение о максимальной длине обсуждения на приложенном скриншоте относится
к интерфейсу ChatGPT. Дефект лимита в живом Layla этим не подтверждён.
