"""Command line interface.

Examples::

    python -m agent "add a /health endpoint and test it"
    python -m agent tools --group "Files and code"
    python -m agent call view --args '{"path": "agent/cli.py"}'
    python -m agent call tool_search --args '{"query": "drive"}'
    python -m agent schema --deferred --out schemas.json
    python -m agent verify
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from agent.config import Settings
from agent.llm import LLMError, build_provider
from agent.loop import Agent
from agent.runtime import Dispatcher, build_context
from agent.tools import checkpoint_counts, load_all_tools, verify_checkpoints
from agent.tools.base import REGISTRY


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings.load(getattr(args, "workspace", None))
    if getattr(args, "model", None):
        object.__setattr__(settings, "model", args.model)
    return settings


def _print_result(result, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
    else:
        print(result.text_content() or json.dumps(result.structured, indent=2, default=str))
    return 1 if result.is_error else 0


def cmd_run(args: argparse.Namespace) -> int:
    settings = _settings(args)
    ctx = build_context(settings)
    try:
        provider = build_provider(settings, provider=args.provider)
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("hint: set ANTHROPIC_API_KEY or OPENAI_API_KEY, or use AGENT_PROVIDER=scripted",
              file=sys.stderr)
        return 2

    agent = Agent(ctx, provider)
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        print("error: empty prompt", file=sys.stderr)
        return 2
    run = agent.run(prompt, max_steps=args.max_steps)
    print(run.text)
    if getattr(args, "verbose", False):
        print("\n--- tool calls ---", file=sys.stderr)
        for record in run.tool_calls:
            status = "error" if record.is_error else "ok"
            print(f"{record.name}: {status} ({record.duration_ms:.0f} ms)", file=sys.stderr)
        print(f"stop: {run.stop_reason} after {run.steps} step(s)", file=sys.stderr)
    return 1 if run.error else 0


def cmd_chat(args: argparse.Namespace) -> int:
    settings = _settings(args)
    ctx = build_context(settings)
    try:
        provider = build_provider(settings, provider=args.provider)
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    agent = Agent(ctx, provider)
    print("OpenAgent chat — Ctrl+C or 'exit' to quit.")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line.lower() in {"exit", "quit"}:
            return 0
        if not line:
            continue
        run = agent.run(line, max_steps=args.max_steps)
        print(run.text)
        if run.stop_reason == "end_conversation":
            return 0


def cmd_tools(args: argparse.Namespace) -> int:
    REGISTRY.load_all()
    groups = REGISTRY.by_group()
    if args.group:
        items = [
            tool_obj for name, tool_list in groups.items()
            if name.lower() == args.group.lower() for tool_obj in tool_list
        ]
        if not items:
            print(f"no group matching {args.group!r}; groups: {', '.join(groups)}", file=sys.stderr)
            return 2
    else:
        items = REGISTRY.all()
    if args.json:
        print(json.dumps([t.schema() for t in items], indent=2, ensure_ascii=False))
        return 0
    for group, tool_list in groups.items():
        if args.group and group.lower() != args.group.lower():
            continue
        print(f"\n{group} ({len(tool_list)})")
        for tool_obj in tool_list:
            suffix = " [deferred]" if tool_obj.requires_tool_search else ""
            print(f"  - {tool_obj.name}{suffix}")
    print(f"\n{len(REGISTRY)} tools total")
    return 0


def cmd_call(args: argparse.Namespace) -> int:
    settings = _settings(args)
    ctx = build_context(settings)
    dispatcher = Dispatcher(ctx)
    arguments = {}
    if args.args:
        try:
            arguments = json.loads(args.args)
        except json.JSONDecodeError as exc:
            print(f"error: --args is not valid JSON: {exc}", file=sys.stderr)
            return 2
    result = dispatcher.call(args.tool, arguments)
    return _print_result(result, as_json=args.json)


def cmd_schema(args: argparse.Namespace) -> int:
    REGISTRY.load_all()
    schemas = [t.schema() for t in REGISTRY.all()
               if args.deferred or not t.requires_tool_search]
    payload = json.dumps(schemas, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
        print(f"wrote {len(schemas)} schemas to {args.out}")
    else:
        print(payload)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    load_all_tools()
    report = verify_checkpoints()
    counts = checkpoint_counts()
    print(json.dumps({"counts": counts, "report": report,
                      "groups": REGISTRY.by_group() and
                      {g: len(v) for g, v in REGISTRY.by_group().items()}}, indent=2))
    return 0 if all(report.values()) or not args.strict else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openagent", description="Self-modifying agentic runtime")
    parser.add_argument("--workspace", help="Workspace root (defaults to cwd)")
    parser.add_argument("--model", help="Override AGENT_MODEL")
    parser.add_argument("--provider", choices=["anthropic", "openai", "scripted", "auto"],
                        help="LLM provider")
    parser.add_argument("--verbose", action="store_true", help="Print tool call trace")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Run one prompt")
    run.add_argument("prompt", nargs="+")
    run.add_argument("--max-steps", type=int, default=None)
    # SUPPRESS so the global --verbose is not clobbered by the subparser default.
    run.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    run.set_defaults(func=cmd_run)

    chat = sub.add_parser("chat", help="Interactive session")
    chat.add_argument("--max-steps", type=int, default=None)
    chat.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    chat.set_defaults(func=cmd_chat)

    tools = sub.add_parser("tools", help="List registered tools")
    tools.add_argument("--group")
    tools.add_argument("--json", action="store_true")
    tools.set_defaults(func=cmd_tools)

    call = sub.add_parser("call", help="Call one tool directly")
    call.add_argument("tool")
    call.add_argument("--args", help="JSON object of arguments")
    call.add_argument("--json", action="store_true")
    call.set_defaults(func=cmd_call)

    schema = sub.add_parser("schema", help="Dump JSON Schemas")
    schema.add_argument("--deferred", action="store_true", help="Include deferred tools")
    schema.add_argument("--out")
    schema.set_defaults(func=cmd_schema)

    verify = sub.add_parser("verify", help="Check checkpoint counts")
    verify.add_argument("--strict", action="store_true")
    verify.set_defaults(func=cmd_verify)
    return parser


VALUE_FLAGS = {"--workspace", "--model", "--provider"}
BOOLEAN_FLAGS = {"--verbose", "-h", "--help"}
SUBCOMMANDS = {"run", "chat", "tools", "call", "schema", "verify"}


def _first_positional(argv: list[str]) -> int | None:
    """Index of the first argument that is not a flag (or a flag's value)."""
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in {"-h", "--help"}:
            return None
        if token in VALUE_FLAGS:
            index += 2  # skip the flag and its value
            continue
        if token.startswith("-"):
            index += 1  # boolean flag, `--flag=value`, or unknown flag
            continue
        return index
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    # Bare prompt: `openagent "fix the bug"` behaves like `openagent run ...`.
    # Global flags (`--workspace X`) must stay in front of the subcommand, so the
    # insert happens at the first real positional, not at argv[0].
    position = _first_positional(argv)
    if position is not None and argv[position] not in SUBCOMMANDS:
        argv = [*argv[:position], "run", *argv[position:]]

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    os.environ.setdefault("AGENT_SESSION_ID", uuid.uuid4().hex[:12])
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
