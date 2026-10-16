"""Rebind Code-only tools after a durable lead workspace transition."""
from __future__ import annotations

import asyncio
from contextlib import aclosing

from app.services import (
    agent_git,
    code_hooks,
    code_lsp,
    code_plans,
    code_runner,
    code_usage,
    code_workers,
    code_workspace,
)

ACTIVE_CHATS: set[tuple[str, str]] = set()


async def run(provider, key, model, messages, root, permissions=None, *, runtime, git=None,
              mode="auto", approve=None, caps=None, transcript=None, mcp=None, **_unused):
    from app.services import project_agent

    workspace = code_workspace.Runtime(runtime, root, permissions, mode=mode, git=git)
    workspace.job_id = _unused.get("job_id")
    caps = caps if caps is not None else {}
    scope = _unused.get("tool_allowlist")
    policies = _unused.get("tool_policies") or []
    budget, continuation = {}, False
    binding = (runtime.owner_id, runtime.chat_id)
    acquired = False
    usage_token = code_usage.activate(runtime, _unused.get("job_id")) if code_usage.ACTIVE.get() is None else None
    try:
        if binding in ACTIVE_CHATS:
            raise ValueError("В этом Code-чате уже работает ведущий агент")
        ACTIVE_CHATS.add(binding)
        acquired = True
        if mode in ("auto", "confirm") and await code_plans.pending(runtime):
            mode = workspace.mode = "plan"
            yield {"mode": mode}
        async with asyncio.timeout(project_agent.MAX_SECONDS):
            for _transition in range(9):
                current_root, current = await workspace.current()
                workspace_id = current.id if current else None
                cache_id = workspace_id or runtime.project_id
                runner = code_runner.Runner(runtime.owner_id, cache_id, current_root)
                workspace.runner = runner
                workers = code_workers.Manager(runtime, current_root, permissions, provider_id=provider.id, model=model,
                    mode=mode, caps=caps, source_root=root, workspace_id=workspace_id)
                current_git = agent_git.GitAgent(current_root, git.author, git.email, git.credential,
                    publish_branch=current.branch if current else None) if git else None
                if transcript:
                    transcript.team, transcript.transitioning = workers.team, False
                state = {"id": workspace_id, "root": current_root, "branch": current.branch if current else None}
                yield {"workspace": state}
                restarted = False
                async with aclosing(project_agent.run(provider, key, model, messages, current_root, permissions,
                    mode=mode, approve=approve, caps=caps, runtime=runtime, workers=workers, git=current_git, mcp=mcp,
                    transcript=transcript, workspace=workspace, continuation=continuation, budget=budget,
                    tool_allowlist=scope, tool_policies=policies,
                    extra="Current Code workspace: " + str(state),
                    runner=runner, lsp=code_lsp.Client(runtime.owner_id, cache_id, current_root),
                    workflow=code_hooks.Runtime(runner, permissions, mode=mode, session_id=runtime.chat_id,
                                                initial_path=_unused.get("skill_path") or "."))) as stream:
                    async for event in stream:
                        if "mode_restart" in event:
                            mode = event["mode_restart"]["mode"]
                            messages = event["mode_restart"]["messages"]
                            scope = event["mode_restart"].get("tool_allowlist")
                            policies = event["mode_restart"].get("tool_policies", policies)
                            workspace.mode = mode
                            continuation, restarted = True, True
                            yield {"mode": mode}
                            continue
                        if "workspace_restart" in event:
                            messages = event["workspace_restart"]
                            scope = event.get("tool_allowlist")
                            policies = event.get("tool_policies", policies)
                            if event.get("approve_all"):
                                approve = None
                            continuation, restarted = True, True
                            continue
                        if "checkpoint" in event:
                            event = {"checkpoint": {**event["checkpoint"], "workspace_id": workspace_id}}
                        elif "tool" in event:
                            event = {"tool": {**event["tool"], "workspace_id": workspace_id}}
                        yield event
                if not restarted:
                    return
            raise ValueError("Слишком много переключений рабочих копий за один запрос; состояние сохранено")
    except BaseException:
        if transcript:
            transcript.transitioning = False
            await transcript.finish(messages, failed=True)
        raise
    finally:
        try:
            if mcp is not None and hasattr(mcp, "aclose"):
                await mcp.aclose()
        finally:
            if acquired:
                ACTIVE_CHATS.discard(binding)
            if usage_token is not None:
                code_usage.reset(usage_token)
