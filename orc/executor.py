"""Decoupled proposal/validation executor.

Architecture:
  LLM proposes → Executor.validate() checks safety/budgets → Executor.execute() runs

Per-tool validation ensures no dangerous commands reach the shell.
Integration with circuit breaker, loop detector, and budgets.
"""

import subprocess
import os
import pyautogui  # type: ignore
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any
from orc.loop_detector import LoopDetector
from orc.budgets import BudgetGroup
from orc.logger import get_logger

logger = get_logger('executor')

TIMEOUT = 10

from orc.sysbridge import BLACKLIST, is_blacklisted

APP_ALIASES = {
    'chrome':     'start chrome',
    'edge':       'start msedge',
    'firefox':    'start firefox',
    'notepad':    'notepad',
    'explorer':   'explorer',
    'calculator': 'calc',
    'spotify':    'start spotify',
    'discord':    'start discord',
    'vscode':     'code',
    'terminal':   'start wt',
    'cmd':        'start cmd',
    'powershell': 'start powershell',
    'task manager': 'taskmgr',
    'paint':      'mspaint',
    'wordpad':    'write',
    'vlc':        'start vlc',
}


@dataclass
class CommandProposal:
    raw: str
    tool: str  # 'shell' | 'ui' | 'file'
    args: Dict[str, Any]
    validated: bool = False
    allowed: bool = False
    reason: str = ""
    sanitized: Optional[str] = None


@dataclass
class CommandValidation:
    allowed: bool
    reason: str
    sanitized: Optional[str] = None


class Executor:
    """Decoupled propose/validate/execute.

    Usage:
        proposal = executor.propose("run cmd: ls -la")
        validation = executor.validate(proposal)
        if validation.allowed:
            result = executor.execute(proposal)
    """

    def __init__(self, loop_detector: Optional[LoopDetector] = None,
                 budgets: Optional[BudgetGroup] = None):
        self._ld = loop_detector or LoopDetector()
        self._budgets = budgets

    # ── Proposal ────────────────────────────────────────────────────────

    def propose(self, raw: str) -> CommandProposal:
        """Parse raw LLM output into a structured proposal."""
        raw_stripped = raw.strip()
        if raw_stripped.startswith('run cmd:'):
            return CommandProposal(
                raw=raw_stripped,
                tool='shell',
                args={'command': raw_stripped[len('run cmd:'):].strip()},
            )
        if raw_stripped.startswith('UI:'):
            parts = raw_stripped[len('UI:'):].strip().split(maxsplit=1)
            action = parts[0].strip() if parts else ''
            arg = parts[1].strip() if len(parts) > 1 else ''
            return CommandProposal(
                raw=raw_stripped,
                tool='ui',
                args={'action': action, 'arg': arg},
            )
        if raw_stripped.startswith('FILE_OP:'):
            return CommandProposal(
                raw=raw_stripped,
                tool='file',
                args={'op': raw_stripped[len('FILE_OP:'):].strip()},
            )
        return CommandProposal(raw=raw_stripped, tool='shell', args={'command': raw_stripped})

    # ── Validation ──────────────────────────────────────────────────────

    def validate(self, proposal: CommandProposal) -> CommandValidation:
        if proposal.tool == 'shell':
            return self._validate_shell(proposal)
        if proposal.tool == 'ui':
            return self._validate_ui(proposal)
        if proposal.tool == 'file':
            return self._validate_file(proposal)
        return CommandValidation(allowed=False, reason=f"unknown tool: {proposal.tool}")

    def _validate_shell(self, proposal: CommandProposal) -> CommandValidation:
        cmd = proposal.args.get('command', '')
        if not cmd:
            return CommandValidation(allowed=False, reason="empty command")
        if self._is_blacklisted(cmd):
            return CommandValidation(allowed=False, reason="blacklisted command")
        resolved = self._resolve_app(cmd)
        if self._ld:
            self._ld.record('shell', cmd[:80])
            if self._ld.is_looping():
                return CommandValidation(allowed=False, reason="loop detected on shell command")
        if self._budgets and not self._budgets.get('retries').consume():
            return CommandValidation(allowed=False, reason="retry budget exhausted")
        return CommandValidation(allowed=True, reason="allowed", sanitized=resolved)

    def _validate_ui(self, proposal: CommandProposal) -> CommandValidation:
        action = proposal.args.get('action', '')
        if action not in UI_COMMANDS:
            return CommandValidation(allowed=False, reason=f"unknown UI action: {action}")
        return CommandValidation(allowed=True, reason="allowed")

    def _validate_file(self, proposal: CommandProposal) -> CommandValidation:
        return CommandValidation(allowed=True, reason="allowed")

    # ── Execution ───────────────────────────────────────────────────────

    def execute(self, proposal: CommandProposal) -> str:
        if proposal.tool == 'shell':
            return self._run_shell(proposal)
        if proposal.tool == 'ui':
            return self._run_ui(proposal)
        if proposal.tool == 'file':
            return "file operations handled by files.py"
        return f"unknown tool: {proposal.tool}"

    def _run_shell(self, proposal: CommandProposal) -> str:
        cmd = proposal.sanitized or proposal.args.get('command', '')
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                cwd=os.path.expanduser('~'),
            )
            return self._format_output(result.stdout.strip(), result.stderr.strip(), result.returncode)
        except subprocess.TimeoutExpired:
            return "launched"
        except Exception as e:
            return f"error: {e}"

    def _run_ui(self, proposal: CommandProposal) -> str:
        action = proposal.args.get('action', '')
        handler = UI_COMMANDS.get(action)
        if handler:
            try:
                stdout, stderr, code = handler(proposal.args.get('arg', ''))
                return self._format_output(stdout, stderr, code)
            except Exception as e:
                return f"ui error: {e}"
        return f"unknown UI action: {action}"

    # ── helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _is_blacklisted(cmd):
        cmd_lower = cmd.lower()
        return any(b in cmd_lower for b in BLACKLIST)

    @staticmethod
    def _resolve_app(cmd):
        lower = cmd.strip().lower()
        for alias, launch in APP_ALIASES.items():
            if lower == alias or lower == f'open {alias}' or lower == f'launch {alias}':
                return launch
        return cmd

    @staticmethod
    def _format_output(stdout, stderr, returncode):
        lines = []
        if stdout:
            lines.append(stdout)
        if stderr:
            lines.append(f'\033[31m{stderr}\033[0m')
        if not stdout and not stderr:
            lines.append('done.' if returncode == 0 else f'exited with code {returncode}')
        return '\n'.join(lines)

    def _run_shell_raw(self, cmd: str, timeout: int = TIMEOUT) -> tuple:
        """Run shell command, return (stdout, stderr, returncode) — legacy compat."""
        cmd = self._resolve_app(cmd)
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=os.path.expanduser('~'),
            )
            return result.stdout.strip(), result.stderr.strip(), result.returncode
        except subprocess.TimeoutExpired:
            return 'launched', '', 0
        except Exception as e:
            return '', str(e), -1

    # Convenience: propose + validate + execute in one call
    def run(self, raw: str) -> str:
        proposal = self.propose(raw)
        validation = self.validate(proposal)
        if not validation.allowed:
            return f"blocked: {validation.reason}"
        proposal.validated = True
        proposal.allowed = True
        proposal.sanitized = validation.sanitized
        return self.execute(proposal)


# ── UI Automation (stateless) ─────────────────────────────────────────────

def close_current_tab(arg=''):
    pyautogui.hotkey('ctrl', 'w')
    time.sleep(0.3)
    return 'tab closed', '', 0


def open_new_tab(arg=''):
    pyautogui.hotkey('ctrl', 't')
    time.sleep(0.5)
    return 'new tab opened', '', 0


def navigate_url(url):
    pyautogui.hotkey('ctrl', 'l')
    time.sleep(0.2)
    pyautogui.hotkey('ctrl', 'a')
    pyautogui.typewrite(url, interval=0.02)
    pyautogui.press('enter')
    time.sleep(0.5)
    return f'navigated to {url}', '', 0


def close_browser(arg=''):
    pyautogui.hotkey('alt', 'f4')
    time.sleep(0.5)
    return 'browser closed', '', 0


def switch_tab_next(arg=''):
    pyautogui.hotkey('ctrl', 'tab')
    time.sleep(0.3)
    return 'switched to next tab', '', 0


def switch_tab_prev(arg=''):
    pyautogui.hotkey('ctrl', 'shift', 'tab')
    time.sleep(0.3)
    return 'switched to previous tab', '', 0


def reopen_closed_tab(arg=''):
    pyautogui.hotkey('ctrl', 'shift', 't')
    time.sleep(0.5)
    return 'reopened last closed tab', '', 0


def get_clipboard_text(arg=''):
    import pyperclip
    return pyperclip.paste(), '', 0


UI_COMMANDS = {
    'close_tab':      close_current_tab,
    'new_tab':        open_new_tab,
    'navigate':       navigate_url,
    'close_browser':  close_browser,
    'next_tab':       switch_tab_next,
    'prev_tab':       switch_tab_prev,
    'reopen_tab':     reopen_closed_tab,
    'clipboard':      get_clipboard_text,
    'get_clipboard':  get_clipboard_text,
}

# Backward-compat module-level wrappers
_run_executor = Executor()


def run_command(cmd, timeout=TIMEOUT):
    """Legacy wrapper — runs a shell command."""
    return _run_executor._run_shell_raw(cmd, timeout)


def format_output(stdout, stderr, returncode):
    return _run_executor._format_output(stdout, stderr, returncode)
