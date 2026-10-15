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
    "AgentTool": ("partial", "agent_start, worker_list/output/wait/resume/apply/discard", "Up to 3 concurrent implement/explore/review agents in private snapshot worktrees, durable output, progress, resume and explicit detach. Lead collects attached results before finishing. Missing teams, messaging and full archive transcript/session replay."),
    "AskUserQuestionTool": ("equivalent", "ask_user", "1-4 structured questions, single/multiple choice and free text; durable answers and same-worker continuation. Code UI can resume after interruption."),
    "BashTool": ("partial", "run_command, command_start", "Foreground/background sandbox commands with durable output, wait and cancellation. Shell writes do not sync to the project; no PTY or full archive shell lifecycle."),
    "BriefTool": ("missing", "", "Need agent-controlled concise output mode."),
    "ConfigTool": ("missing", "", "No compatible agent-facing settings tool."),
    "EnterPlanModeTool": ("partial", "plan chat mode", "The user can select Plan; the agent cannot change the mode itself."),
    "ExitPlanModeTool": ("partial", "plan chat mode", "No structured submitted plan and acceptance transition."),
    "EnterWorktreeTool": ("partial", "agent_start", "Workers receive managed detached Git worktrees from actual project snapshots, including dirty/untracked files. Main repository/index remain untouched. No lead workspace switching or user-selected branches."),
    "ExitWorktreeTool": ("partial", "worker_apply, worker_discard", "Retain private edits, import selected text diffs with optimistic hashes/checkpoints, or explicitly discard. No archive-compatible lead cwd transition or branch publication."),
    "FileEditTool": ("equivalent", "edit_file", "Exact fragment replacement with optimistic hashes, diff and rollback."),
    "FileReadTool": ("partial", "read_file", "Text ranges and hashes; missing multimodal images/PDF extraction."),
    "FileWriteTool": ("equivalent", "write_file", "Atomic UTF-8 project-scoped writes and version checks."),
    "GlobTool": ("partial", "glob_files", "Project-scoped pageable search; fnmatch behavior differs from ripgrep glob syntax."),
    "GrepTool": ("partial", "grep_files", "Bounded regex/content/count/context; no full ripgrep flag compatibility."),
    "LSPTool": ("partial", "lsp", "Managed sandbox stdio servers for Python and JS/TS; all 9 archive operations plus diagnostics, capability checks, UTF-16 positions, source-only locations, change invalidation, cancel/restart and editor navigation. Missing plugin-provided/custom server registration and automatic cross-turn passive diagnostic attachments."),
    "ListMcpResourcesTool": ("equivalent", "mcp_list_resources, mcp_resource_templates", "Owner/persona-scoped paginated resource and URI-template inventory."),
    "MCPTool": ("partial", "mcp__*", "Dynamic discovery, owner/persona binding and text/JSON results; no full persistent session and notifications lifecycle."),
    "McpAuthTool": ("missing", "", "Need interactive MCP OAuth and credential lifecycle."),
    "NotebookEditTool": ("equivalent", "notebook_read, notebook_edit", "Cell-level insert/replace/delete, metadata preservation, invalidation of outputs, hashes and rollback."),
    "PowerShellTool": ("missing", "", "Current execution backend is Linux/bash; no Windows PowerShell backend."),
    "REPLTool": ("missing", "", "No persistent interactive REPL state."),
    "ReadMcpResourceTool": ("partial", "mcp_read_resource", "Text resource reads with bounded output; binary resources are described without content."),
    "RemoteTriggerTool": ("missing", "", "Need remote trigger subscriptions, dispatch and lifecycle."),
    "ScheduleCronTool": ("missing", "", "Need durable scheduled jobs and scheduler UI."),
    "SendMessageTool": ("missing", "", "Need agent inboxes and direct/broadcast team messages."),
    "SkillTool": ("partial", "list_skills, read_skill", "Project Markdown and SKILL.md discovery; missing frontmatter execution policy, argument expansion and hooks."),
    "SleepTool": ("missing", "", "No deferred wake/sleep agent tool."),
    "SyntheticOutputTool": ("missing", "", "No compatible synthetic output transport."),
    "TaskCreateTool": ("equivalent", "task_create", "Durable Code-chat tasks with prerequisites and owner."),
    "TaskGetTool": ("equivalent", "task_get", "Durable task details, result and blocked/ready state."),
    "TaskListTool": ("equivalent", "task_list", "Durable per-conversation task inventory."),
    "TaskOutputTool": ("equivalent", "worker_output, worker_wait", "Independently addressed agent/command output, character pagination, bounded diffs, persistent reports and wait/poll operations; scoped to owner/chat/project."),
    "TaskStopTool": ("equivalent", "worker_stop", "Independently stop one agent or background sandbox command while retaining output and completed edits. Parent cancellation stops attached workers; explicit detached workers continue."),
    "TaskUpdateTool": ("equivalent", "task_update", "Durable status, owner, result, metadata merge/delete and dependencies with cycle checks; accepts taskId, activeForm, addBlocks and addBlockedBy."),
    "TeamCreateTool": ("missing", "", "Need durable teams, members and role assignment."),
    "TeamDeleteTool": ("missing", "", "Need coordinated shutdown and team cleanup."),
    "TodoWriteTool": ("equivalent", "update_todos", "Checklist with progress and persisted message metadata."),
    "ToolSearchTool": ("partial", "mcp_search_tools", "Deferred MCP discovery; no unified deferred registry for every built-in/plugin tool."),
    "WebFetchTool": ("partial", "web_fetch", "Public HTTPS text/HTML/JSON/XML with SSRF protections; no authenticated browser or PDF extraction."),
    "WebSearchTool": ("equivalent", "web_search", "Public web research with references; search provider differs."),
}

SUBSYSTEMS = [
    ("agent_loop", "partial", ["QueryEngine.ts", "query.ts", "query/"], "Provider tool loop/retries exist; not every archive stop condition and continuation path is matched."),
    ("session_history", "partial", ["history.ts", "utils/conversationRecovery.ts", "utils/crossProjectResume.ts"], "Durable chats and interrupted-turn notes; no full archive transcript replay/resume/fork compatibility."),
    ("context_compaction", "partial", ["services/compact/", "context/", "context.ts"], "History summaries and tool-result shrinking; no archive compaction/overflow policy equivalence."),
    ("file_checkpoints", "partial", ["utils/fileHistory.ts"], "File checkpoints/rollback exist; no full history snapshot and rewind policy equivalence."),
    ("memory", "partial", ["memdir/", "services/SessionMemory/", "services/extractMemories/", "services/autoDream/"], "Project LAYLA.md and durable Code-chat notes; missing extraction, consolidation, team memory and dream loop."),
    ("agent_orchestration", "partial", ["coordinator/", "tasks/", "Task.ts", "utils/teammateMailbox.ts", "utils/tasks.ts"], "Durable task graph and 3 independent implement/explore/review workers with progress, stop/resume, detach and reviewed imports. Missing teams, messaging and full session replay."),
    ("hooks", "missing", ["utils/hooks.ts", "utils/hooks/", "query/stopHooks.ts"], "Need event registry, sandbox execution and result handling for session/tool/task lifecycle hooks."),
    ("plugins", "missing", ["plugins/", "services/plugins/"], "Need plugin manifest/install/update/enable lifecycle and skill/tool/hook registration."),
    ("lsp", "partial", ["services/lsp/"], "Per-owner/project/worker sandbox servers with bounded JSON-RPC, push/pull diagnostics, changes/deletions, limits, idle reaping and crash recovery. Editor supports draft analysis and source navigation. Plugin configuration, arbitrary language packs and automatic cross-turn diagnostic delivery remain."),
    ("skills", "partial", ["skills/"], "Basic project skills supported; bundled workflows, invocation policies, hooks and package integration remain."),
    ("project_instructions", "partial", ["utils/claudemd.ts", "projectOnboardingState.ts"], "Root LAYLA.md only; nested instruction precedence and onboarding are not equivalent."),
    ("background_jobs", "partial", ["utils/background/", "tasks/LocalShellTask/", "tasks/RemoteAgentTask/"], "Independent agent/sandbox command jobs, durable output, explicit detach, cancellation and retained-workspace resume. Commands restart from the beginning; shell writes do not sync to source and no persistent OS process survives restart."),
    ("git_worktrees", "partial", ["utils/worktree.ts", "utils/worktreeModeEnabled.ts"], "Private snapshot repositories and detached worktrees, selected CAS imports/checkpoints, retain/discard and trash cleanup. No lead cwd switching, custom branches or publication lifecycle."),
    ("permissions", "partial", ["utils/permissions/", "hooks/toolPermission/", "utils/autoModeDenials.ts"], "Persona allowlists, project boundaries and Auto/Confirm/Plan/Review; no full rule language/policy/classifier equivalence."),
    ("mcp", "partial", ["services/mcp/"], "Dynamic Code tools and resources; auth, templates, notifications, reconnect and long-lived sessions remain."),
    ("scheduled_tasks", "missing", ["utils/cron.ts", "utils/cronScheduler.ts", "utils/cronTasks.ts", "utils/cronTasksLock.ts"], "Need scheduler, leases, missed-run behavior, cancellation and persistence."),
    ("browser_integration", "missing", ["utils/claudeInChrome/", "skills/bundled/claudeInChrome.ts"], "Need browser runtime/plugin and authenticated interaction contract."),
    ("ide_integration", "partial", ["hooks/useIDEIntegration.tsx", "hooks/useIdeConnectionStatus.tsx"], "Web editor cursor, source navigation, symbols/diagnostics and unsaved-buffer LSP analysis. Missing external IDE connection/bridge and full archive editor event model."),
    ("remote_sessions", "missing", ["remote/", "bridge/", "server/", "upstreamproxy/"], "Need independent remote transport/session contracts; external service counterparts are not in the archive."),
    ("voice", "missing", ["voice/", "services/voice.ts", "services/voiceStreamSTT.ts"], "Need audio capture, transcription transport and voice UI."),
    ("terminal_ui", "missing", ["ink/", "ink.ts", "vim/", "keybindings/", "cli/", "screens/", "main.tsx"], "Layla has a web UI; no matching terminal renderer, keyboard/Vim model or CLI application."),
    ("web_ui", "partial", ["components/", "dialogLaunchers.tsx", "interactiveHelpers.tsx"], "Web chat, file tree/editor, diffs and collapsed specialist/task cards; not all archive dialogs/controls are reproduced."),
    ("configuration", "partial", ["utils/config.ts", "utils/settings/", "constants/", "schemas/"], "Layla settings exist; archive schema/precedence/import/migrations require explicit mapping."),
    ("commands", "missing", ["commands/", "commands.ts", "entrypoints/", "replLauncher.tsx"], "Need compatible slash-command registry and entrypoint behavior."),
    ("output_styles", "missing", ["outputStyles/"], "Need configurable output styles and agent-controlled brief mode."),
    ("cost_tracking", "partial", ["cost-tracker.ts", "costHook.ts"], "Provider capability adaptation exists; archive usage/cost/budget tracking is not matched."),
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
