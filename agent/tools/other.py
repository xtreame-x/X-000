"""Miscellaneous control tools (1): end_conversation."""
from __future__ import annotations

from agent.tools.base import ToolContext, ToolResult, tool


@tool(group="Other", tags=("stop", "end", "finish", "done", "conversation"))
def end_conversation(
    ctx: ToolContext,
    reason: str = "task_complete",
    summary: str = "",
) -> ToolResult:
    """End the conversation and hand the final summary to the user.

    Call this only when the user's request is fully resolved or they asked to
    stop. The agent loop exits after this tool returns.

    Args:
        reason: Why the conversation is ending, e.g. task_complete or user_request.
        summary: Final answer for the user: what was done, what remains, and any caveats.
    """
    ctx.stop_requested = True
    ctx.stop_reason = reason or "task_complete"
    ctx.emit_event({"type": "conversation_end", "reason": ctx.stop_reason, "summary": summary})
    return ToolResult.text(
        summary or f"Conversation ended ({ctx.stop_reason}).",
        reason=ctx.stop_reason,
        summary=summary,
        ended=True,
    )


__all__ = ["end_conversation"]
