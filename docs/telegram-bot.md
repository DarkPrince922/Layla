# Telegram control panel

Create a dedicated bot with @BotFather. In Layla Settings → Integrations → Telegram,
save its token, enable the integration and press Connect Telegram. Open the generated
link in a private chat and press Start within ten minutes. The link is single use;
only the paired Telegram user can control this Layla account. Groups cannot issue commands.
A bot token can belong to only one Layla account. Tokens remain encrypted in storage.

Use `/menu` for buttons or `/help` for commands. Supported operations:

- Switch Code, Pentest, OSINT and Design; select projects, sites, cases and designs.
- Select any enabled provider/model, including connected Grok models, and plan/confirm/auto mode.
- Create/select chats and targets, send tasks, read history/results, rename and compact chats.
- View jobs/progress/errors, download full results and stop jobs with explicit confirmation.
- Browse project directories, download text files or ZIP archives.
- View findings and reports, generate reports and download a current Markdown findings summary.
- Inspect pentest workers, download archives, resume/stop workers and approve/deny pending actions.
- View OSINT artifacts and launch configured provider lookups; generate Design drafts.
- Configure completion, failure, cancellation and approval notifications.
- Open the complete Layla interface as a Telegram Web App using the configured public HTTPS URL.
  Editor, Git, uploads and remaining advanced settings are available there. Normal Layla login
  still applies; no session token is inserted into the URL.

Commands retain existing Layla ownership, provider, scope and approval checks through
its authenticated API. Bot state contains opaque, expiring single-use callback identifiers.
Changing the token resets pairing; disconnecting removes pending notifications.

Notifications include jobs launched in the web UI. Completion events are queued in the
same transaction as job completion. Delivery retries rate limits/network errors and
remembers acknowledged message chunks. Delivery is at least once: a crash between
Telegram accepting a message and the database recording it can duplicate that chunk.
Commands claim their update before acting to prevent replayed task launches; if the
process crashes during handling, the user may need to resend the command.

The bot uses long polling and requires outbound HTTPS access to api.telegram.org.
No inbound webhook is required. Connection refuses bots with an existing webhook;
use a dedicated bot rather than removing another application's webhook automatically.
Run a single core process, consistent with the existing in-process job worker model.
Queued notifications survive restarts; integration outages do not block agent completion.
Disabled integrations pause delivery; old queued messages for a changed destination are discarded.

Migration: `0022_telegram_bot`, following `0021_grok_login`. Deploy with the usual
migration/startup procedure. Telegram secrets are omitted from public errors and HTTPX
request logging. Real Telegram delivery is not exercised by mocked unit tests; after
deployment use Test notification, pair the account and run a small task to check delivery.

## Agent request restarts

A failed model request is retried up to five times after the initial attempt,
with delays of 1, 2, 4, 8 and 16 seconds (bounded Retry-After hints are respected).
This applies to tool agents, pentest workers, plain streaming chats and Design generation.
Token/context/output length limit errors and cancellation do not enter this retry loop.
Existing capability learning and context fitting remain separate recovery mechanisms.
Retries happen before executing model-selected tools, preserving completed operations.
Failed partial streaming text is retracted before the next attempt; retry progress is
shown in the task. Final failure/completion notifications are sent after recovery ends.
