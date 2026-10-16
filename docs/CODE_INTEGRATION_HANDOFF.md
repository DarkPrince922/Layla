# Продолжение интеграции Claude Code в Layla

## Область и ветки

Работа ограничена Code. Другие домены не исследовать и не менять.
Последнее решение пользователя: сначала MCP и планировщик; плагины отложены.
Успешный CI отдельного этапа не означает полного соответствия архиву.

- PR #39: `codex/code-workspace-shell-teams` — worktree editor/terminal/Git/LSP,
  выбор ref, import/export/fork сессий, draft PR, протокол команды и PTY.
- PR #40: `codex/code-integration-continuation`, commit
  `64fd0def02ccede872b0b4ce8b86bc958ef71290` — проектные/вложенные инструкции,
  rules/imports и output styles; контракт: [CODE_INSTRUCTIONS.md](CODE_INSTRUCTIONS.md).
- Текущая ветка: `codex/code-mcp-scheduler`, основана на commit PR #40 выше.
  Новый PR должен использовать `codex/code-integration-continuation` как base.
  Не переносить эти изменения прямо с main без зависимых PR.

## Реализовано сейчас

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
(миграции `0032` и `0033`) и корректный внешний `LAYLA_PUBLIC_URL` для OAuth.
Живое production-развёртывание в этом этапе не выполнялось.

## Проверки

Из `core`:

```bash
pytest -q tests/test_code_*.py tests/test_project_agent.py tests/test_history.py tests/test_mcp.py
```

205 тестов прошли локально. Есть настоящий stdio MCP fixture и запуск
расписания через реальные API/БД/Jobs с тестовым provider stream. Проверены
upgrade с `0031`, отсутствие ORM drift и downgrade с сохранением пользователя.
Ruff изменённых Python-файлов и TypeScript проходят; ESLint изменённых TSX
без ошибок (существующее предупреждение ChatPanel dependency остаётся).
Локальная production-сборка Next блокируется отсутствием `/proc` в среде
(`uv_resident_set_memory`); итоговую сборку следует проверить по CI PR.
Внешний OAuth-провайдер в production локальные тесты не заменяют.

## Реестр и остаток

[CODE_PARITY.md](CODE_PARITY.md) и [code-parity.json](code-parity.json)
содержат полный инвентарь 1907 файлов, 40 инструментов и 33 подсистем.
`full_parity` остаётся `false`; MCP и scheduler отмечены `partial` с конкретными
пробелами. Digest приложенного ZIP:
`f74f54805f280d8535bb0e3767fe7712392e77745d7e91ac9fbedd18891d55eb`.

Остаются Plan → execution с решением пользователя, оставшиеся hooks/rules/skills,
MCP sampling/elicitation/prompts и полный lifecycle, архивные scheduler jitter/
auto-expiry/session-only/teammate routes, remote triggers/sessions, проактивный
цикл, память/context recovery и budgets, прочие shell/IDE/CLI/browser/voice/sync
контракты. Плагины отложены; не начинать их без смены текущего приоритета.

Сообщение о максимальной длине обсуждения на приложенном скриншоте относится
к интерфейсу ChatGPT. Дефект лимита в живом Layla этим не подтверждён.
