# Продолжение интеграции Claude Code в Layla

## Обязательная область

Цель пользователя — закончить перенос функций предоставленного архива в Code.
Работа ограничена Code; другие домены не исследовать и не менять. Успешный
CI отдельного этапа не означает полного соответствия архиву.

## Текущая ветка

- База: PR #39, `codex/code-workspace-shell-teams`,
  commit `033af1552e0d0d98d6ded79120993641c390f6f1`.
- Продолжение: `codex/code-integration-continuation`.
- В PR #39 уже находятся worktree editor/terminal/Git/LSP, выбор ref,
  import/export/fork сессий, draft PR, протокол планов/остановки команд и PTY.
  Не дублировать этот перенос с основной ветки, где этих изменений ещё нет.
- Новые изменения: проектные/вложенные инструкции и project output styles;
  контракт и ограничения — [CODE_INSTRUCTIONS.md](CODE_INSTRUCTIONS.md).
- `code-parity.json` пересоздан по ZIP, приложенному в текущем обсуждении.
  Список 1907 файлов совпадал с прежним; digest содержимого отличался.
  Текущий digest: `f74f54805f280d8535bb0e3767fe7712392e77745d7e91ac9fbedd18891d55eb`.

## Проверки

Из каталога `core`:

```bash
pytest -q tests/test_code_*.py tests/test_project_agent.py tests/test_history.py
```

171 тест прошёл локально. Проверены Code tools/API, воркеры, сессии, hooks,
worktrees и история. Ruff для изменённых Python-файлов прошёл. Изменений схемы
БД, sandbox и frontend в этом продолжении нет. Для PR #39 frontend и sandbox
прошли CI; общий CI Core нужно проверять отдельно по актуальному коммиту.

Новые тесты проверяют порядок инструкций, условные области, циклы и пределы
импортов, снимки на запрос, ссылки, сохранение инструкций при сжатии контекста,
выдачу новых правил до записи, стили и приоритет настроек, Code-only доступ.

## Остаток

Полный список и статусы — [CODE_PARITY.md](CODE_PARITY.md) и
[code-parity.json](code-parity.json). `full_parity` остаётся `false`.
Следующие независимые контракты, требующие реализации и приёмки:

1. Переход ведущего Plan → execution с пользовательским решением и настройки
   агента; протокол планов участников команды уже реализован в PR #39.
2. Plugin manifest/install/update/enable lifecycle и регистрация skills/tools/hooks.
3. Остальные типы/события hooks, полноценные rules/imports и динамические skills.
4. MCP OAuth, постоянные сессии, reconnect/notifications и бинарные результаты.
5. Durable scheduler, remote triggers/sessions и проактивный цикл.
6. Консолидация памяти, полнота transcript/context recovery, usage/cost budgets.
7. Оставшиеся shell/IDE/browser/CLI/voice/companion/sync контракты из реестра.

Не исправлять сообщение о максимальной длине обсуждения как доказанный дефект
Layla: приложенный скриншот показывает интерфейс ChatGPT; причины лимита в
живом развёртывании Layla не подтверждены. Продолжать перенос по коду и тестам.
