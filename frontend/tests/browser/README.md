# Pentest chat browser regression

This test starts Core with a temporary SQLite database, a local fake model and Next.js on ports 8219/3310. It creates test-only accounts; the provider finishes without running commands or contacting targets. The database and projects are removed after the test.

Install Core development dependencies into `core/.venv`, run `npm ci` in `frontend`, and make Playwright with Chromium available. From the repository root:

```sh
python frontend/tests/browser/run.py
```

If Playwright is installed elsewhere, set `PLAYWRIGHT_MODULE` to its absolute module path. `LAYLA_BROWSER_EXECUTABLE` and `LAYLA_BROWSER_ARGS` (a JSON array) optionally select another Chromium executable.

Covers delayed engagement list refresh, exactly one mounted chat, isolated history, repeated Enter in one event turn, continuation before history polling, late POST responses after switching, per-engagement restoration after reload, and 320px layout.

The runner uses the current Python interpreter, so invoke it from an environment
with Core dependencies installed. Set `LAYLA_BROWSER_TEST=workbench.cjs` to test
engagement memory, real stored tool evidence, coverage reasons, four queued local
workers, folded panels, compose actions and 320/390px layouts. This fixture uses
only local file listing; it never runs attackbox commands. `LAYLA_QA_SCREENSHOTS`
optionally preserves screenshots outside the temporary test directory.
