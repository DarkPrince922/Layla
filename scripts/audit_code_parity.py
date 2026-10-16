"""Inventory a supplied source archive and track Code feature parity explicitly.

Usage: python scripts/audit_code_parity.py /path/to/extracted/archive

Only filenames, counts and a content digest are recorded. No source is copied.
The manifest inventories the entire archive, including features with no Layla
equivalent. A green CI run is never treated as proof of complete feature parity.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

TOOL_PARITY = {
    "AgentTool": ("partial", "agent_start, worker_list/output/wait/resume/apply/discard", "Up to 3 concurrent implement/explore/review agents in private snapshot worktrees, durable output, progress, resume and explicit detach. Named durable teams, direct/broadcast mail and append-only worker protocol history with resume are implemented. Lead collects attached results and receives peer mail while waiting. Lead and worker protocol replay, rollback/compact-aware context and lead worktree switching are implemented. Import/fork of a selected JSON/JSONL branch and structured approval/shutdown are implemented. Full attribution, all attachments and archive protocol parity remain."),
    "AskUserQuestionTool": ("equivalent", "ask_user", "1-4 structured questions, single/multiple choice and free text; durable answers and same-worker continuation. Code UI can resume after interruption."),
    "BashTool": ("partial", "run_command, command_start, command_input, command_changes, worker_command_changes", "Foreground/background commands, project-relative cwd, persistent stdin for interactive processes, live output, timeout/stop, binary/text/mode file imports with diffs, CAS conflicts, durable pending results and rollback. PTY and resizing are implemented; missing automatic in-place foreground promotion, full shell snapshot/profile and archive-specific permission semantics."),
    "BriefTool": ("missing", "", "Need agent-controlled concise output mode."),
    "ConfigTool": ("missing", "", "No compatible agent-facing settings tool."),
    "EnterPlanModeTool": ("equivalent", "enter_plan_mode, Plan mode", "The Code lead can switch to read-only Plan mid-turn. Remaining batch calls are not executed, the prompt/tools are rebuilt and call/round budgets remain. A pending plan prevents ordinary Auto messages from bypassing approval."),
    "ExitPlanModeTool": ("partial", "submit_plan, Code plan decision panel", "Immutable durable plan, explicit user approve Auto/Confirm or reject with feedback, owner/chat/project/worktree and fresh-instruction checks, idempotent Job admission. Confirmation and persona permissions remain. Missing full archive permission-proposal schema and plan-file semantics."),
    "EnterWorktreeTool": ("partial", "enter_worktree, resume_worktree, worktree_list", "Lead switches tools mid-turn into durable private Git worktrees with source history/current dirty files, separate cache IDs and worker scopes; original source/index/branch preserved. Ref selection and explicit editor/terminal binding are implemented; hook-created worktrees remain."),
    "ExitWorktreeTool": ("partial", "exit_worktree, worktree_diff/read/publish, worker_apply/discard", "Lead returns to source, keeps/resumes or explicitly discards its copy. Complete since-entry diff, ZIP and fixed generated branch publication using owner credentials; scoped rollback cannot overwrite source. Draft GitHub PR creation is implemented; archive hooks/tmux and merge lifecycle remain."),
    "FileEditTool": ("equivalent", "edit_file", "Exact fragment replacement with optimistic hashes, diff and rollback."),
    "FileReadTool": ("partial", "read_file", "Text ranges and hashes; missing multimodal images/PDF extraction."),
    "FileWriteTool": ("equivalent", "write_file", "Atomic UTF-8 project-scoped writes and version checks."),
    "GlobTool": ("partial", "glob_files", "Project-scoped pageable search; fnmatch behavior differs from ripgrep glob syntax."),
    "GrepTool": ("partial", "grep_files", "Bounded regex/content/count/context; no full ripgrep flag compatibility."),
    "LSPTool": ("partial", "lsp", "Managed sandbox stdio servers for Python and JS/TS; all 9 archive operations plus diagnostics, capability checks, UTF-16 positions, source-only locations, change invalidation, cancel/restart and editor navigation. Missing plugin-provided/custom server registration and automatic cross-turn passive diagnostic attachments."),
    "ListMcpResourcesTool": ("equivalent", "mcp_list_resources, mcp_resource_templates", "Owner/persona-scoped paginated resource and URI-template inventory."),
    "MCPTool": ("partial", "mcp__*", "Dynamic discovery, owner/persona binding, pooled cross-turn sessions, notifications/catalog refresh, explicit reconnect, text/JSON and owned binary files. No automatic mutating replay, sampling or elicitation."),
    "McpAuthTool": ("partial", "mcp_auth, Code OAuth settings", "Explicit browser OAuth PKCE, discovery, CIMD/DCR/pre-registered clients, encrypted credentials, persisted registration, serialized refresh and revocation. No automatic scope step-up or complete archive auth compatibility."),
    "NotebookEditTool": ("equivalent", "notebook_read, notebook_edit", "Cell-level insert/replace/delete, metadata preservation, invalidation of outputs, hashes and rollback."),
    "PowerShellTool": ("partial", "run_command/command_start shell=powershell", "Conditional pwsh backend probing, no-profile argv execution, cwd/stdin/background/output/stop and file imports. PTY on the Linux sandbox is available; no Windows host or archive-specific CLM/parser/permission behavior. Default image has no pwsh; unavailable use fails explicitly."),
    "REPLTool": ("partial", "command_start interactive=true, command_input, worker_output/stop", "Persistent per-worker OS process stdin and state for Python/Node/bash REPLs, EOF, live output and stop; owner/chat/project isolation and file collection. PTY is available; no archive primitive-tools JS kernel API or restoration of in-memory variables after restart."),
    "ReadMcpResourceTool": ("partial", "mcp_read_resource", "Bounded text resource reads and owned binary artifact downloads; no multimodal inline model input or full archive attachment rendering."),
    "RemoteTriggerTool": ("missing", "", "Need remote trigger subscriptions, dispatch and lifecycle."),
    "ScheduleCronTool": ("partial", "cron_create/list/update/delete, Code schedule panel", "Durable cron/one-shot prompts, IANA timezone/DST, leases, idempotent normal Job admission, busy retry, missed-run coalescing, pause/delete and owner/chat/project/worktree binding. Missing archive jitter, auto-expiry, session-only tasks and teammate routing."),
    "SendMessageTool": ("partial", "send_message, team_info", "Durable named direct/broadcast mail, automatic between-turn delivery, transactional worker transcript acknowledgement, bounded mailboxes and archived UI. Structured shutdown/plan decisions and same-project cross-chat lead mail are available; cross-session UDS/bridge peers remain."),
    "SkillTool": ("partial", "list_skills, read_skill, invoke_skill, /name", "Bounded project YAML metadata, literal positional/named arguments, user/model invocation flags, narrowing tool scopes, run-scoped hooks and context=fork through isolated workers. Missing model overrides, dynamic shell substitution, plugin/bundled/remote skills and full archive permission-grant semantics."),
    "SleepTool": ("missing", "", "No deferred wake/sleep agent tool."),
    "SyntheticOutputTool": ("missing", "", "No compatible synthetic output transport."),
    "TaskCreateTool": ("equivalent", "task_create", "Durable Code-chat tasks with prerequisites and owner."),
    "TaskGetTool": ("equivalent", "task_get", "Durable task details, result and blocked/ready state."),
    "TaskListTool": ("equivalent", "task_list", "Durable per-conversation task inventory."),
    "TaskOutputTool": ("equivalent", "worker_output, worker_wait", "Independently addressed agent/command output, character pagination, bounded diffs, persistent reports and wait/poll operations; scoped to owner/chat/project."),
    "TaskStopTool": ("equivalent", "worker_stop", "Independently stop one agent or background sandbox command while retaining output and completed edits. Parent cancellation stops attached workers; explicit detached workers continue."),
    "TaskUpdateTool": ("equivalent", "task_update", "Durable status, owner, result, metadata merge/delete and dependencies with cycle checks; accepts taskId, activeForm, addBlocks and addBlockedBy."),
    "TeamCreateTool": ("partial", "team_create, agent_start name", "One durable active team per Code chat, named implement/explore/review members, database uniqueness and three shared active slots. Missing archive backend/team configuration compatibility."),
    "TeamDeleteTool": ("partial", "team_delete", "Refuse cleanup while members run, serialize close/member reservation, retain mail/reports/transcripts/worktrees in archive and cascade on chat deletion. Structured graceful shutdown responses are available; archive team backend compatibility remains."),
    "TodoWriteTool": ("equivalent", "update_todos", "Checklist with progress and persisted message metadata."),
    "ToolSearchTool": ("partial", "mcp_search_tools", "Deferred MCP discovery; no unified deferred registry for every built-in/plugin tool."),
    "WebFetchTool": ("partial", "web_fetch", "Public HTTPS text/HTML/JSON/XML with SSRF protections; no authenticated browser or PDF extraction."),
    "WebSearchTool": ("equivalent", "web_search", "Public web research with references; search provider differs."),
}

SUBSYSTEMS = [
    ("agent_loop", "partial", ["QueryEngine.ts", "query.ts", "query/"], "Provider tool loop/retries exist; not every archive stop condition and continuation path is matched."),
    ("session_history", "partial", ["history.ts", "utils/conversationRecovery.ts", "utils/crossProjectResume.ts"], "Durable chats and append-only worker messages/tool results, independent of compaction; resume repairs unknown tool outcomes and preserves worktrees. Lead journal replay, fresh persona/provider adaptation, atomic mail acknowledgement, rollback invalidation, full-tool compaction and atomic protocol trimming are implemented. JSON/JSONL selected-branch import, isolated fork and export are implemented; validated recovery checkpoints, explicit continuation, compact replay views and journal recall are implemented; full attribution and archive hook/attachment state remain."),
    ("context_compaction", "partial", ["services/compact/", "context/", "context.ts"], "History summaries, tool-result shrinking, Code in-run protocol-group compaction, durable compact replay views and raw journal context_search. Current request/instructions and recent rounds remain; fail with saved progress if still too large. Missing full archive overflow/continuation and attachment semantics."),
    ("file_checkpoints", "partial", ["utils/fileHistory.ts"], "File checkpoints/rollback exist; no full history snapshot and rewind policy equivalence."),
    ("memory", "partial", ["memdir/", "services/SessionMemory/", "services/extractMemories/", "services/autoDream/"], "Project instructions and durable Code-chat notes with non-destructive model consolidation, fingerprint invalidation, preserved source notes and automatic consolidation above a threshold. Missing cross-chat extraction, team/global/managed memory and dream loop."),
    ("agent_orchestration", "partial", ["coordinator/", "tasks/", "Task.ts", "utils/teammateMailbox.ts", "utils/tasks.ts"], "Durable task graph and 3 independent implement/explore/review workers with progress, stop/resume, detach and reviewed imports. Named teams, direct/broadcast durable mail and worker transcript resume are implemented. Structured plan/shutdown, same-project cross-chat mail and JSON/JSONL import/fork are implemented; UDS peers and full attribution remain."),
    ("hooks", "partial", ["utils/hooks.ts", "utils/hooks/", "query/stopHooks.ts"], "Code-only sandbox command hooks: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse/Failure, Stop and SessionEnd. Snapshot config, once, matchers, disableAllHooks, deny/update/context, confirmation, stop retry limits and audit. Missing prompt/agent/http/async hooks, remaining events, full regex matching and session persistence."),
    ("plugins", "missing", ["plugins/", "services/plugins/"], "Deferred by user for this stage. Need plugin manifest/install/update/enable lifecycle and skill/tool/hook registration."),
    ("lsp", "partial", ["services/lsp/"], "Per-owner/project/worker sandbox servers with bounded JSON-RPC, push/pull diagnostics, changes/deletions, limits, idle reaping and crash recovery. Editor supports draft analysis and source navigation. Plugin configuration, arbitrary language packs and automatic cross-turn diagnostic delivery remain."),
    ("skills", "partial", ["skills/"], "Project SKILL.md/Markdown metadata, flags, literal args, run-scoped hooks, narrowed tools and fork workers; snapshot catalogue and Code picker. Bundled workflows, nested conditional discovery, dynamic shell substitution, model overrides and plugin/package lifecycle remain."),
    ("project_instructions", "partial", ["utils/claudemd.ts", "projectOnboardingState.ts"], "Code-only per-run project instructions: root/nested CLAUDE.md, .claude/CLAUDE.md, CLAUDE.local.md and LAYLA.md; recursive .claude/.layla rules with path globs, bounded local @includes, snapshots and pre-action discovery in lead/worker worktrees. No host/home/managed memory, full Markdown/minimatch/import semantics, InstructionsLoaded hooks or onboarding equivalence."),
    ("background_jobs", "partial", ["utils/background/", "tasks/LocalShellTask/", "tasks/RemoteAgentTask/"], "Independent agent/command jobs, project-scoped sandbox locks, live/durable output, detach, interactive stdin, EOF/stop, partial file recovery and text/binary/mode imports via worker_apply. Resume restarts a command; no OS process survives restart and no automatic in-place foreground promotion."),
    ("git_worktrees", "partial", ["utils/worktree.ts", "utils/worktreeModeEnabled.ts"], "Private snapshot repositories and detached worktrees, selected CAS imports/checkpoints, retain/discard and trash cleanup. Lead cwd switching, private source history, retain/resume/remove, scoped rollback and own-branch publication are implemented. Ref selection, draft GitHub PR and unified editor/terminal switching are implemented; hook-created worktrees remain."),
    ("permissions", "partial", ["utils/permissions/", "hooks/toolPermission/", "utils/autoModeDenials.ts"], "Persona allowlists, project boundaries and Auto/Confirm/Plan/Review; no full rule language/policy/classifier equivalence."),
    ("mcp", "partial", ["services/mcp/"], "Code-only OAuth PKCE/discovery/CIMD/DCR/manual clients, encrypted durable registration/tokens, leased refresh/revocation; pooled cross-turn SDK sessions, templates, notifications/catalog refresh, reconnect and owned binary files. Missing sampling, elicitation, prompts UI, automatic scope step-up and archive/protocol lifecycle equivalence."),
    ("scheduled_tasks", "partial", ["utils/cron.ts", "utils/cronScheduler.ts", "utils/cronTasks.ts", "utils/cronTasksLock.ts"], "Durable Code cron/one-shot prompts, timezone/DST, leased idempotent normal Job admission, busy retries, coalesced missed runs, pause/delete UI and owner/chat/project/worktree binding. Missing jitter, recurring-task auto-expiry, session-only tasks, teammate routing and remote wake service."),
    ("browser_integration", "missing", ["utils/claudeInChrome/", "skills/bundled/claudeInChrome.ts"], "Need browser runtime/plugin and authenticated interaction contract."),
    ("ide_integration", "partial", ["hooks/useIDEIntegration.tsx", "hooks/useIdeConnectionStatus.tsx"], "Web editor cursor, source navigation, symbols/diagnostics and unsaved-buffer LSP analysis. Missing external IDE connection/bridge and full archive editor event model."),
    ("remote_sessions", "missing", ["remote/", "bridge/", "server/", "upstreamproxy/"], "Need independent remote transport/session contracts; external service counterparts are not in the archive."),
    ("voice", "missing", ["voice/", "services/voice.ts", "services/voiceStreamSTT.ts"], "Need audio capture, transcription transport and voice UI."),
    ("terminal_ui", "missing", ["ink/", "ink.ts", "vim/", "keybindings/", "cli/", "screens/", "main.tsx"], "Layla has a web UI; no matching terminal renderer, keyboard/Vim model or CLI application."),
    ("web_ui", "partial", ["components/", "dialogLaunchers.tsx", "interactiveHelpers.tsx"], "Web chat, file tree/editor, diffs and collapsed specialist/task cards; not all archive dialogs/controls are reproduced."),
    ("configuration", "partial", ["utils/config.ts", "utils/settings/", "constants/", "schemas/"], "Layla settings exist; archive schema/precedence/import/migrations require explicit mapping."),
    ("commands", "partial", ["commands/", "commands.ts", "entrypoints/", "replLauncher.tsx"], "Project /skill commands and legacy .layla/.claude commands directories, user-invocable checks and Code picker. Built-in command suite, namespaces and CLI/remote entrypoints remain."),
    ("output_styles", "partial", ["outputStyles/"], "Code-only project outputStyle settings, default/Explanatory/Learning presentation modes, .claude/.layla output-styles Markdown with metadata and read-only model catalog. Persona and coding constraints are retained. Missing user/global/plugin styles, style picker, full keep-coding-instructions replacement semantics and BriefTool transport."),
    ("cost_tracking", "partial", ["cost-tracker.ts", "costHook.ts"], "Code per-chat shared token/USD admission budget, CAS reservations for lead/workers/retries/summaries, OpenAI/Anthropic usage reconciliation and conservative unknown charges. User-configured per-model prices, persistent ledger and UI; missing invoice parity, provider-specific cache tariffs and full archive account/billing policies."),
    ("proactive_assistant", "missing", ["assistant/"], "Need tick loop, observed events, journaling and proactive task policy."),
    ("companion", "missing", ["buddy/"], "Need character lifecycle, animation and companion interaction."),
    ("authentication_backends", "missing", ["services/oauth/", "services/api/"], "Archive provider-specific account/subscription services have no supplied server implementations."),
    ("settings_sync", "missing", ["services/settingsSync/", "services/remoteManagedSettings/", "services/teamMemorySync/"], "Need remote settings/team-memory contracts and server-side implementation."),
    ("telemetry_diagnostics", "missing", ["services/analytics/", "services/internalLogging.ts", "services/diagnosticTracking.ts"], "Need product-level diagnostics equivalent; proprietary telemetry endpoints should not be copied blindly."),
    ("platform_runtime", "missing", ["bootstrap/", "native-ts/", "migrations/", "setup.ts"], "Archive has no supplied package manifest, lockfile or build config. CLI/platform support requires an independent build/runtime design."),
    ("support_modules", "unclassified", ["utils/", "state/", "types/", "public/", "moreright/"], "All paths inventoried; every remaining cross-cutting helper still requires contract classification before claiming full parity."),
]


def build(root: Path) -> dict:
    source_files = sorted(path for path in root.rglob("*") if path.is_file())
    digest = hashlib.sha256()
    files = []
    for path in source_files:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
        files.append(relative)
    tool_names = sorted(path.name for path in (root / "tools").iterdir()
                        if path.is_dir() and path.name not in ("shared", "testing"))
    unmapped = set(tool_names) - set(TOOL_PARITY)
    obsolete = set(TOOL_PARITY) - set(tool_names)
    if unmapped or obsolete:
        raise ValueError(f"Tool inventory changed; unmapped={sorted(unmapped)}, obsolete={sorted(obsolete)}")
    tools = [{"archive_tool": name, "status": TOOL_PARITY[name][0],
              "layla": TOOL_PARITY[name][1], "notes": TOOL_PARITY[name][2]}
             for name in tool_names]
    missing_imports = []
    import_count = 0
    root_absolute = root.resolve()
    for path in source_files:
        if path.suffix not in (".ts", ".tsx"):
            continue
        content = path.read_text(errors="replace")
        for specifier in re.findall(r'''(?:from\s*|import\s*\(\s*)['"](\.[^'"]+)['"]''', content):
            import_count += 1
            target = path.parent / specifier
            candidates = (target, target.with_suffix(".ts"), target.with_suffix(".tsx"),
                          target.with_suffix(".d.ts"), target.with_suffix(".json"),
                          target / "index.ts", target / "index.tsx")
            if not any(candidate.resolve().is_relative_to(root_absolute) and candidate.is_file()
                       for candidate in candidates):
                missing_imports.append({"file": path.relative_to(root).as_posix(), "import": specifier})
    return {
        "format": 1,
        "goal": "Full functional correspondence in Code. No excluded archive subsystems.",
        "full_parity": False,
        "status_meaning": {
            "equivalent": "Equivalent for the described contract; independent implementation, not identical code or wire format.",
            "partial": "Some behavior exists, remaining gaps described explicitly.",
            "missing": "Not implemented or not verified.",
            "unclassified": "Inventoried but requires further behavioral decomposition.",
        },
        "archive": {"file_count": len(files), "content_digest": digest.hexdigest(), "files": files},
        "dependency_audit": {
            "method": "Static relative-import scan; does not infer build-time generated modules or private package contents.",
            "relative_import_occurrences": import_count,
            "unresolved_occurrences": len(missing_imports),
            "unresolved": missing_imports,
            "build_manifests": [name for name in ("package.json", "package-lock.json", "bun.lock", "bun.lockb", "tsconfig.json")
                                if (root / name).is_file()],
        },
        "tool_count": len(tools), "tools": tools,
        "subsystems": [{"id": ident, "status": status, "archive_paths": paths, "notes": notes}
                       for ident, status, paths, notes in SUBSYSTEMS],
        "completion_rule": "No full parity claim while partial/missing/unclassified contracts remain. Each equivalent requires behavior tests; CI alone is not a parity certificate.",
    }


if __name__ == "__main__":
    root = Path(sys.argv[1])
    output = Path(__file__).resolve().parents[1] / "docs" / "code-parity.json"
    manifest = build(root)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Inventoried {manifest['archive']['file_count']} files, {manifest['tool_count']} tools; full_parity=false")
