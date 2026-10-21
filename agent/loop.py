"""The agent loop: model -> tool calls -> results -> model, until done.

Design points that matter:

* Only non-deferred tools are advertised initially. ``tool_search`` unlocks more
  by adding to ``ctx.loaded_tools``, and the next request advertises them.
* A tool failure is fed back to the model as an error result; the loop never
  raises out of a tool call.
* ``end_conversation`` and ``max_steps`` both terminate the loop.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from agent.llm import LLMError, LLMResponse, Provider
from agent.runtime import CallRecord, Dispatcher
from agent.tools.base import REGISTRY, Registry, ToolContext, ToolResult

DEFAULT_SYSTEM = """You are OpenAgent, a self-modifying coding agent running on the user's machine.

Environment
- Workspace: {workspace}
- Platform: {platform}. {shell_hint}
- State directory: {state_dir}

How to work
1. Inspect before you change: `view` a file before editing it.
2. Prefer `str_replace` for small edits and `create_file` for new files. Both are
   snapshotted and syntax-checked (.py/.json); invalid code is rejected before it
   touches disk.
3. Use `bash_tool` for builds and tests, and read the exit status. A green exit
   code alone does not prove behaviour is correct.
4. Deferred tools are hidden until you call `tool_search`. Search with the task in
   words ("list drive files", "create deployment") and then call the tool.
5. Verify changes by running the project's own checks. If a check fails, fix the
   cause; never weaken a test or suppress an error to get green output.
6. Be precise: report what you verified, what failed, and what you could not run.

Self-editing
- You may edit your own source under this workspace. Every edit is snapshotted to
  {snapshots}. Secrets (.env) and the state directory are not writable by tools.
- After editing your own code, reload affected modules before relying on them.

Ask with `ask_user_input_v0` when a decision is genuinely ambiguous; otherwise make
the reasonable choice, state it, and continue. Call `end_conversation` with a
summary when the task is done.
"""

SHELL_HINTS = {
    "cmd": "Commands run through cmd.exe; use cmd syntax and `%VAR%`.",
    "pwsh": "Commands run through PowerShell 7; use PowerShell syntax and `$env:VAR`.",
    "powershell": "Commands run through Windows PowerShell; use PowerShell syntax.",
    "bash": "Commands run through bash; use POSIX syntax.",
    "sh": "Commands run through sh; use POSIX syntax.",
}


@dataclass
class AgentRun:
    text: str
    steps: int
    stop_reason: str
    tool_calls: list[CallRecord] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "steps": self.steps,
            "stopReason": self.stop_reason,
            "error": self.error,
            "toolCalls": [
                {"tool": c.name, "isError": c.is_error, "durationMs": round(c.duration_ms, 2)}
                for c in self.tool_calls
            ],
        }


class Agent:
    def __init__(
        self,
        ctx: ToolContext,
        provider: Provider,
        *,
        dispatcher: Dispatcher | None = None,
        registry: Registry | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self.ctx = ctx
        self.provider = provider
        self.registry = (registry or REGISTRY).load_all()
        self.dispatcher = dispatcher or Dispatcher(ctx, registry=self.registry)
        self.system_prompt = system_prompt or DEFAULT_SYSTEM.format(
            workspace=ctx.settings.workspace,
            platform="Windows" if ctx.settings.windows_path_rules else "POSIX",
            shell_hint=SHELL_HINTS.get(ctx.settings.shell, f"Shell: {ctx.settings.shell}"),
            state_dir=ctx.settings.state_dir,
            snapshots=ctx.settings.snapshots_dir,
        )

    # --- tool advertisement --------------------------------------------------
    def advertised_tools(self) -> list[dict]:
        return [
            tool_obj.schema()
            for tool_obj in self.registry.all()
            if not tool_obj.requires_tool_search or tool_obj.name in self.ctx.loaded_tools
        ]

    # --- loop ----------------------------------------------------------------
    def run(self, prompt: str, *, max_steps: int | None = None) -> AgentRun:
        messages: list[dict] = [{"role": "user", "blocks": [{"type": "text", "text": prompt}]}]
        limit = max_steps or self.ctx.settings.max_steps
        steps = 0
        collected: list[CallRecord] = []

        while steps < limit:
            steps += 1
            try:
                response = self.provider.complete(
                    system=self.system_prompt,
                    messages=messages,
                    tools=self.advertised_tools(),
                )
            except LLMError as exc:
                detail = f"{exc}"
                if exc.body:
                    detail += f"\n{exc.body}"
                return AgentRun(
                    text=f"LLM call failed: {detail}",
                    steps=steps,
                    stop_reason="llm_error",
                    tool_calls=collected,
                    messages=messages,
                    error=detail,
                )

            messages.append({"role": "assistant", "blocks": response.blocks})

            if not response.tool_calls:
                return AgentRun(
                    text=response.text or "(no text returned)",
                    steps=steps,
                    stop_reason=response.stop_reason or "end_turn",
                    tool_calls=collected,
                    messages=messages,
                )

            result_blocks = []
            for call in response.tool_calls:
                result = self.dispatcher.call(call.name, call.arguments)
                if self.dispatcher.history:
                    collected.append(self.dispatcher.history[-1])
                result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "blocks": result.blocks,
                        "is_error": result.is_error,
                    }
                )
            messages.append({"role": "user", "blocks": result_blocks})

            if self.ctx.stop_requested:
                summary = next(
                    (
                        block["blocks"][0].get("text", "")
                        for block in result_blocks
                        if block["blocks"] and block["blocks"][0].get("type") == "text"
                    ),
                    "Conversation ended.",
                )
                return AgentRun(
                    text=summary,
                    steps=steps,
                    stop_reason=self.ctx.stop_reason or "end_conversation",
                    tool_calls=collected,
                    messages=messages,
                )

        return AgentRun(
            text=f"Stopped after {limit} steps without a final answer.",
            steps=steps,
            stop_reason="max_steps",
            tool_calls=collected,
            messages=messages,
        )


def transcript(run: AgentRun) -> str:
    """Render a run as JSON for logging or debugging."""
    return json.dumps(run.to_dict(), indent=2)


__all__ = ["Agent", "AgentRun", "DEFAULT_SYSTEM", "SHELL_HINTS", "transcript", "LLMResponse", "ToolResult"]
