import os
import sys
import time
import threading
from dotenv import load_dotenv
from orc.config import load_config, get
from orc.memory import (
    load_stm, needs_compression, compress_stm,
    fetch_ltm, save_ltm_entry, append_stm
)
from orc.sysbridge import get_sysinfo, format_sysinfo
from orc.executor import Executor, run_command, format_output, UI_COMMANDS
from orc.status import render as render_status
from orc.files import handle_file_op
from orc.tag_dispatch import TagDispatch
from orc.circuit_breaker import CircuitBreaker
from orc.loop_detector import LoopDetector
from orc.web import search as web_search
from orc.tasks import get_manager as get_task_manager
from orc.updater import check_update, format_update_status
from orc.logger import get_logger, log_startup, log_shutdown
from orc.providers import ProviderManager, ChatMessage

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

logger = get_logger('shell')

# ══════════════════════════════════════════════════════════════════════════════
#  SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════════

BASE_SYSTEM = """You are Oryn - an autonomous personal laptop system agent running natively on the user's Windows machine.

Personality: precise, sharp, slightly dry wit, loyal. Short punchy responses. No markdown asterisks, no bullets. Plain text only.

Direct Action & Tool Use:
- You control this machine natively through your callable tools.
- When the user asks you to do something (inspect RAM/processes, manage files, send or draft emails via SMTP, search the web, control browser tabs, check hardware, manage timers/alarms/calendar, find places, access Google Drive or Notion, or run extended OpenAgent tools), CALL THE RELEVANT TOOL DIRECTLY.
- You have callable tools to send emails directly (send_email) using configured SMTP settings, or create drafts (draft_email).
- You have access to a 75-tool catalog: use search_openagent_tools or openagent_execute if an extended tool (Notion, SketchUp 3D, Three.js viewer, etc.) is needed.
- Never give manual instructions, never tell the user to open an editor, and never ask them to copy-paste commands you can execute yourself.
- You have memory: STM (short term active notes) and LTM (long term permanent knowledge).
- After tools execute, respond with a direct, concise message confirming results or taking the next step. Keep responses brief and punchy."""

BANNER = r"""
  ██████╗ ██████╗ ██╗   ██╗███╗   ██╗
 ██╔═══██╗██╔══██╗╚██╗ ██╔╝████╗  ██║
 ██║   ██║██████╔╝ ╚████╔╝ ██╔██╗ ██║
 ██║   ██║██╔══██╗  ╚██╔╝  ██║╚██╗██║
 ╚██████╔╝██║  ██║   ██║   ██║ ╚████║
  ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═══╝
  system agent v0.2  |  ARIS: standby
"""

COMMANDS = {
    "help":      "show available commands",
    "clear":     "clear the terminal",
    "memory":    "show current STM",
    "status":    "full system + oryn health dashboard",
    "sys":       "show live system stats",
    "update":    "check for updates",
    "plugins":   "list installed plugins",
    "tasks":     "list background tasks",
    "voice":     "toggle voice mode",
    "login":     "1-click browser sign-in (login drive | login notion)",
    "auth":      "alias for login (auth drive | auth notion)",
    "provider":  "show/switch AI provider",
    "create":    "create a plugin (interactive)",
    "templates": "list tool templates",
    "mytools":   "list your created tools",
    "orch":      "start orchestrator with a task",
    "orch-next": "run next step in orchestrator",
    "orch-run":  "run all orchestrator steps",
    "orch-plan": "show current plan",
    "orch-map":  "show codemap",
    "orch-abort":"abort current plan",
    "exit":      "shut down oryn",
    "quit":    "shut down oryn",
}


# ══════════════════════════════════════════════════════════════════════════════
#  SPINNER
# ══════════════════════════════════════════════════════════════════════════════

class Spinner:
    def __init__(self, msg="processing"):
        self._msg = msg
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        frames = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏']
        i = 0
        while not self._stop.is_set():
            print(f"\r\033[90m{frames[i % len(frames)]} {self._msg}...\033[0m", end='', flush=True)
            i += 1
            time.sleep(0.08)

    def start(self):
        self._t.start()

    def stop(self):
        self._stop.set()
        self._t.join()
        print('\r' + ' ' * (len(self._msg) + 6) + '\r', end='', flush=True)


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def print_banner():
    print("\033[34m" + BANNER + "\033[0m")
    print("\033[90m" + "─" * 48 + "\033[0m")
    print("\033[34m[oryn]\033[0m online. what do you need?")
    print("\033[90m" + "─" * 48 + "\033[0m\n")


def build_system_prompt(extra=None):
    stm = load_stm()
    data = _get_cached_sysinfo()
    username = os.environ.get('USERNAME') or os.environ.get('USER') or 'user'

    prompt = BASE_SYSTEM
    prompt += f"\n\n[LOGGED_IN_USER] {username}"
    if data:
        prompt += f"\n\n[LIVE SYSTEM]\n{format_sysinfo(data)}"
    if stm:
        prompt += f"\n\n[STM]\n{stm}"
    if extra:
        prompt += f"\n\n{extra}"
    return prompt


def parse_response(raw):
    """Extract message and action from LLM response.
    Supports clean plain text from native tools as well as legacy [MSG]/[ACT] tags.
    """
    if not raw:
        return "", ""
    if '[MSG]' not in raw and '[ACT]' not in raw:
        return raw.strip(), ""

    msg_lines = []
    acts = []
    for line in raw.split('\n'):
        stripped = line.strip()
        if stripped.startswith('[MSG]'):
            msg_lines.append(stripped[5:].strip())
        elif stripped.startswith('[ACT]'):
            acts.append(stripped[5:].strip())
        elif stripped and not acts:
            msg_lines.append(stripped)

    msg = '\n'.join(msg_lines) if msg_lines else ''
    act = acts[0] if acts else ''
    return msg, act


def execute_action(act, executor=None):
    """Run an [ACT] string, return (result_text, ok).
    
    Supports both old flat format and new [ACT][tool] tag dispatch format.
    """
    act = act.strip()

    # Try tag dispatch format first
    if act.startswith('[') and ']' in act:
        # Extract tool name from [ACT][tool] or [tool]
        tag_content = act
        if tag_content.startswith('[ACT]'):
            tag_content = tag_content[5:]
        # Try [tool] prefix
        if tag_content.startswith('['):
            end_bracket = tag_content.find(']')
            if end_bracket > 0:
                tool_name = tag_content[1:end_bracket]
                payload = tag_content[end_bracket+1:]
                if payload.endswith('[/ACT]'):
                    payload = payload[:-6]
                if tool_name == 'shell':
                    cmd = payload
                    if executor:
                        return executor.run(cmd), True
                    stdout, stderr, code = run_command(cmd)
                    return format_output(stdout, stderr, code), code == 0 or stdout != ''
                if tool_name == 'file':
                    result = handle_file_op(payload)
                    return result, True
                if tool_name == 'search':
                    query = payload
                    result = web_search(query, max_results=get('search.max_results', 5))
                    return result, True
                if tool_name == 'ui':
                    parts = payload.split('|', 1)
                    ui_cmd = parts[0]
                    arg = parts[1] if len(parts) > 1 else None
                    if ui_cmd in UI_COMMANDS:
                        fn = UI_COMMANDS[ui_cmd]
                        stdout, stderr, code = fn(arg) if arg else fn()
                        return format_output(stdout, stderr, code), code == 0
                    return f"unknown UI command: {ui_cmd}", False
                if tool_name == 'ltm':
                    if payload.startswith('fetch|'):
                        result = fetch_ltm(payload[6:])
                        return result or 'no matching entries', True
                    if payload.startswith('save|'):
                        save_ltm_entry(payload[5:])
                        return 'saved to LTM', True
                    return f"unknown LTM op: {payload}", False
                if tool_name == 'stm':
                    if payload.startswith('save|'):
                        append_stm(payload[5:])
                        return 'saved to STM', True
                    return f"unknown STM op: {payload}", False
                if tool_name == 'voice':
                    from orc.voice import VoiceManager
                    vm = VoiceManager(load_config())
                    ok = vm.speak(payload)
                    return "spoken" if ok else "tts unavailable", ok
                if tool_name == 'task':
                    if payload == 'list':
                        tasks = get_task_manager().list_active()
                        if not tasks:
                            return "no active tasks", True
                        lines = [f"{t.id} | {t.status.value} | {t.command[:40]}" for t in tasks]
                        return '\n'.join(lines), True
                    if payload.startswith('status|'):
                        task = get_task_manager().status(payload[7:])
                        if task:
                            return f"{task.id} | {task.status.value} | {task.output[:200] if task.output else task.error[:200]}", True
                        return f"task not found: {payload[7:]}", False
                    if payload.startswith('cancel|'):
                        ok = get_task_manager().cancel(payload[7:])
                        return f"task cancelled" if ok else "cannot cancel", ok
                    if payload.startswith('submit|'):
                        task_id = get_task_manager().submit(payload[7:])
                        return f"task submitted: {task_id}", True
                    return f"unknown task op: {payload}", False

    # Old flat format fallback
    if act.startswith('FILE_OP:'):
        result = handle_file_op(act[8:])
        return result, True

    if act.startswith('RUN_CMD:'):
        cmd = act[8:]
        stdout, stderr, code = run_command(cmd)
        result = format_output(stdout, stderr, code)
        return result, code == 0 or stdout != ''

    if act.startswith('SEARCH:'):
        query = act[7:]
        result = web_search(query, max_results=get('search.max_results', 5))
        return result, True

    if act.startswith('ASYNC_CMD:'):
        cmd = act[10:]
        task_id = get_task_manager().submit(cmd)
        return f"task submitted: {task_id}", True

    if act.startswith('TASK_STATUS:'):
        task_id = act[12:]
        task = get_task_manager().status(task_id)
        if task:
            return f"{task.id} | {task.status.value} | {task.output[:200] if task.output else task.error[:200]}", True
        return f"task not found: {task_id}", False

    if act.startswith('TASK_CANCEL:'):
        task_id = act[12:]
        ok = get_task_manager().cancel(task_id)
        return f"task {task_id} cancelled" if ok else f"cannot cancel: {task_id}", ok

    if act == 'TASK_LIST':
        tasks = get_task_manager().list_active()
        if not tasks:
            return "no active tasks", True
        lines = [f"{t.id} | {t.status.value} | {t.command[:40]}" for t in tasks]
        return '\n'.join(lines), True

    if act.startswith('VOICE_SPEAK:'):
        from orc.voice import VoiceManager
        vm = VoiceManager(load_config())
        text = act[12:]
        ok = vm.speak(text)
        return "spoken" if ok else "tts unavailable", ok

    if act.startswith('UI:'):
        ui_cmd = act[3:]
        arg = None
        if '|' in ui_cmd:
            ui_cmd, arg = ui_cmd.split('|', 1)
        if ui_cmd in UI_COMMANDS:
            fn = UI_COMMANDS[ui_cmd]
            stdout, stderr, code = fn(arg) if arg else fn()
            return format_output(stdout, stderr, code), code == 0
        return f"unknown UI command: {ui_cmd}", False

    if act.startswith('FETCH_LTM:'):
        topic = act[10:]
        result = fetch_ltm(topic)
        return result or 'no matching entries', True

    if act.startswith('SAVE_LTM:'):
        save_ltm_entry(act[9:])
        return 'saved to LTM', True

    if act.startswith('SAVE_STM:'):
        append_stm(act[9:])
        return 'saved to STM', True

    if act.startswith('CREATE_PLUGIN:'):
        from orc.creator import create_plugin_from_spec
        spec = act[14:]
        result = create_plugin_from_spec(spec)
        return result, True

    if act.startswith('CREATE_ACTION:'):
        from orc.creator import create_action
        parts = act[14:].split('|', 1)
        if len(parts) == 2:
            result = create_action(parts[0], parts[1])
        else:
            result = "usage: CREATE_ACTION:NAME|code"
        return result, True

    if act.startswith('DELETE_PLUGIN:'):
        from orc.creator import delete_plugin
        result = delete_plugin(act[14:])
        return result, True

    if act.startswith('DELETE_ACTION:'):
        from orc.creator import delete_action
        result = delete_action(act[14:])
        return result, True

    if act == 'LIST_PLUGINS':
        from orc.creator import list_plugins
        return list_plugins(), True

    if act == 'LIST_ACTIONS':
        from orc.creator import list_actions
        return list_actions(), True

    if act == 'LIST_TEMPLATES':
        from orc.creator import get_templates
        return get_templates(), True

    if act.startswith('CREATE_FROM_TEMPLATE:'):
        from orc.creator import create_from_template
        parts = act[21:].split('|')
        if len(parts) >= 2:
            result = create_from_template(parts[0], parts[1], parts[2] if len(parts) > 2 else None)
        else:
            result = "usage: CREATE_FROM_TEMPLATE:template|plugin_name|command"
        return result, True

    # Check for user-created custom action in orc/actions/
    actions_dir = os.path.join(os.path.dirname(__file__), 'actions')
    if os.path.isdir(actions_dir):
        action_name = None
        action_args = ""
        if ':' in act:
            potential_name, potential_args = act.split(':', 1)
            potential_file = os.path.join(actions_dir, f"{potential_name.strip().lower()}.py")
            if os.path.isfile(potential_file):
                action_name = potential_name.strip().lower()
                action_args = potential_args
        elif os.path.isfile(os.path.join(actions_dir, f"{act.strip().lower()}.py")):
            action_name = act.strip().lower()
            action_args = ""

        if action_name:
            try:
                import importlib.util
                action_file = os.path.join(actions_dir, f"{action_name}.py")
                spec = importlib.util.spec_from_file_location(f"action_{action_name}", action_file)
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    if hasattr(mod, 'execute'):
                        stdout, stderr, code = mod.execute(action_args)
                        if hasattr(mod, 'format_output'):
                            return mod.format_output(stdout, stderr, code), code == 0
                        return format_output(stdout, stderr, code), code == 0
            except Exception as e:
                return f"action error ({action_name}): {e}", False

    return f"unknown action: {act}", False


# ══════════════════════════════════════════════════════════════════════════════
#  BUILTINS
# ══════════════════════════════════════════════════════════════════════════════

def run_notion_login():
    """1-click Notion sign-in shared by `login notion` and `auth notion`."""
    from orc.notion_auth import NotionAuthError, login as notion_login

    print("\n\033[34m[notion]\033[0m opening Notion sign-in in your browser...")
    print("\033[90m[notion] pick a workspace, click Allow, then come back here.\033[0m")
    try:
        result = notion_login(announce=lambda line: print(f"\033[90m{line}\033[0m"))
    except NotionAuthError as exc:
        print(f"\n\033[31m[notion]\033[0m {exc}\n")
        return False
    except KeyboardInterrupt:
        print("\n\033[33m[notion]\033[0m cancelled. Nothing was changed.\n")
        return False

    workspace = result.workspace_name or "your workspace"
    print(f"\n\033[32m[notion]\033[0m connected to {workspace}.\n")
    return True


def run_drive_login():
    """1-click Google Drive sign-in shared by `login drive` and `auth drive`."""
    from orc.drive_auth import DriveAuthError, login

    print("\n\033[34m[drive]\033[0m opening Google sign-in in your browser...")
    print("\033[90m[drive] sign in, click Allow, then come back here.\033[0m")
    try:
        result = login(announce=lambda line: print(f"\033[90m{line}\033[0m"))
    except DriveAuthError as exc:
        print(f"\n\033[31m[drive]\033[0m {exc}\n")
        return False
    except KeyboardInterrupt:
        print("\n\033[33m[drive]\033[0m cancelled. Nothing was changed.\n")
        return False

    if result.has_refresh_token:
        print("\n\033[32m[drive]\033[0m connected permanently - Drive tools will refresh on their own.\n")
    else:
        print("\n\033[33m[drive]\033[0m connected, but Google sent no refresh token; "
              "access expires in about an hour.\n")
    return True


def handle_builtin(cmd, config, providers, voice_mgr, plugins, voice_mode_ref):
    cmd = cmd.strip().lower()

    if cmd in ("exit", "quit"):
        log_shutdown()
        print("\n\033[34m[oryn]\033[0m shutting down. stay safe.\n")
        sys.exit(0)

    if cmd == "clear":
        clear_screen()
        print_banner()
        return True

    if cmd == "help":
        print("\n\033[90mbuilt-in commands:\033[0m")
        for k, v in COMMANDS.items():
            print(f"  \033[34m{k:<12}\033[0m {v}")
        if plugins:
            plugin_cmds = {}
            for p in plugins.plugins.values():
                for c, h in p.get_commands().items():
                    plugin_cmds[c] = h
            if plugin_cmds:
                print("\n\033[90mplugin commands:\033[0m")
                for k, v in plugin_cmds.items():
                    print(f"  \033[34m{k:<12}\033[0m {v}")
        print("\033[90m\nanything else goes straight to oryn.\033[0m\n")
        return True

    if cmd == "memory":
        stm = load_stm()
        print(f"\n\033[90m[STM]\033[0m {stm or 'empty'}\n")
        return True

    if cmd == "status":
        print()
        print(render_status())
        print()
        return True

    if cmd == "sys":
        data = get_sysinfo()
        print(f"\n{format_sysinfo(data)}\n")
        return True

    if cmd == "update":
        result = check_update(config)
        print(f"\n{format_update_status(result)}\n")
        return True

    if cmd == "plugins":
        if plugins:
            plist = plugins.list_plugins()
            if not plist:
                print("\n\033[90mno plugins loaded\033[0m\n")
            else:
                print(f"\n\033[90m{len(plist)} plugin(s):\033[0m")
                for p in plist:
                    cmds = ', '.join(p['commands']) if p['commands'] else 'none'
                    print(f"  \033[34m{p['name']}\033[0m v{p['version']} — {p['description']}")
                    print(f"    commands: {cmds}")
                print()
        return True

    if cmd == "tasks":
        tasks = get_task_manager().list_active()
        if not tasks:
            print("\n\033[90mno active tasks\033[0m\n")
        else:
            print(f"\n\033[90m{len(tasks)} active task(s):\033[0m")
            for t in tasks:
                print(f"  {t.id} | {t.status.value} | {t.command[:50]}")
            print()
        return True

    if cmd == "voice":
        voice_mode_ref['active'] = not voice_mode_ref['active']
        state = "\033[32mON\033[0m" if voice_mode_ref['active'] else "\033[31mOFF\033[0m"
        print(f"\n\033[34m[voice]\033[0m {state}")
        if voice_mode_ref['active']:
            print("\033[90mspeak after each response. type 'voice' again to disable.\033[0m")
        print()
        return True

    if cmd in {
        "login", "auth",
        "login drive", "auth drive",
        "drive login", "drive auth",
        "login google", "auth google",
    }:
        run_drive_login()
        return True

    if cmd in {
        "login notion", "auth notion",
        "notion login", "notion auth",
    }:
        run_notion_login()
        return True

    if cmd == "provider":
        print(f"\n\033[90mactive:\033[0m \033[34m{providers.active_name}\033[0m")
        print(f"\033[90mmodel:\033[0m  {providers.active_model}")
        avail = providers.list_available()
        for p in avail:
            print(f"\n\033[90m{p['name']}:\033[0m {len(p['models'])} models")
            for m in p['models'][:5]:
                print(f"  {m}")
            if len(p['models']) > 5:
                print(f"  ... +{len(p['models'])-5} more")
        print()
        return True

    if cmd == "templates":
        from orc.creator import get_templates
        print(f"\n{get_templates()}\n")
        print("\033[90muse [ACT]CREATE_FROM_TEMPLATE:template|plugin_name to create\033[0m\n")
        return True

    if cmd == "mytools":
        from orc.creator import list_plugins, list_actions
        print()
        print(list_plugins())
        print()
        print(list_actions())
        print()
        return True

    if cmd == "create":
        print("\n\033[90mcreate a plugin by describing it to oryn.\033[0m")
        print("\033[90mexample: 'create a plugin that converts celsius to fahrenheit'\033[0m")
        print("\033[90mor use [ACT]CREATE_PLUGIN: with a spec:\033[0m")
        print("\033[90m  name: converter\033[0m")
        print("\033[90m  description: temperature converter\033[0m")
        print("\033[90m  command: convert\033[0m")
        print("\033[90m  code: return str(float(args) * 9/5 + 32)\033[0m\n")
        return True

    # Orchestrator commands
    if cmd.startswith("orch ") or cmd.startswith("orch\t"):
        task = cmd[4:].strip()
        if not task:
            print("\n\033[31musage: orch <task description>\033[0m\n")
            return True
        from orc.orchestrator import Orchestrator
        orch = Orchestrator(providers)
        result = orch.start(task)
        print(f"\n{result}\n")
        return True

    if cmd == "orch-next":
        from orc.orchestrator import Orchestrator
        orch = Orchestrator(providers)
        result = orch.step()
        print(f"\n{result}\n")
        return True

    if cmd == "orch-run":
        from orc.orchestrator import Orchestrator
        orch = Orchestrator(providers)
        def progress(msg):
            print(f"  \033[90m{msg}\033[0m")
        result = orch.run_all(callback=progress)
        print(f"\n{result}\n")
        return True

    if cmd == "orch-plan":
        from orc.orchestrator import Orchestrator
        orch = Orchestrator(providers)
        plan = orch.get_plan()
        if plan:
            print(f"\n{plan.summary()}\n")
        else:
            print("\nno active plan\n")
        return True

    if cmd == "orch-map":
        from orc.orchestrator import Orchestrator
        orch = Orchestrator(providers)
        print(f"\n{orch.get_codemap().summary()}\n")
        return True

    if cmd == "orch-abort":
        from orc.orchestrator import Orchestrator
        orch = Orchestrator(providers)
        result = orch.abort()
        print(f"\n{result}\n")
        return True

    return False


def handle_raw(user_input):
    cmd = user_input[1:].strip()
    if not cmd:
        return
    print()
    stdout, stderr, code = run_command(cmd)
    print(format_output(stdout, stderr, code))
    print()


# ══════════════════════════════════════════════════════════════════════════════
#  TOKEN GUARDS
# ══════════════════════════════════════════════════════════════════════════════

MAX_HISTORY = 10
MAX_CHAIN_DEPTH = 3
MAX_RESULT_CHARS = 500
STM_COMPRESS_INTERVAL = 8
SYSINFO_CACHE_SEC = 30

_sysinfo_cache = {'data': None, 'time': 0}


def _get_cached_sysinfo():
    now = time.time()
    if now - _sysinfo_cache['time'] > SYSINFO_CACHE_SEC:
        _sysinfo_cache['data'] = get_sysinfo()
        _sysinfo_cache['time'] = now
    return _sysinfo_cache['data']


def _trim_history(history):
    """Keep only last MAX_HISTORY exchanges, each content truncated."""
    trimmed = history[-MAX_HISTORY * 2:]  # account for user+assistant pairs
    result = []
    for entry in trimmed:
        content = entry['content']
        if isinstance(content, str) and len(content) > MAX_RESULT_CHARS:
            content = content[:MAX_RESULT_CHARS] + '\n... [truncated]'
        result.append({'role': entry['role'], 'content': content})
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  CHAT
# ══════════════════════════════════════════════════════════════════════════════

def is_coding_task(text):
    """Detect if the user input is a coding task."""
    coding_keywords = [
        'code', 'script', 'function', 'class', 'method', 'program', 'debug',
        'error', 'bug', 'fix', 'implement', 'write a', 'create a plugin',
        'create a script', 'html', 'css', 'javascript', 'python', 'java',
        'algorithm', 'api', 'database', 'sql', 'regex', 'syntax', 'import',
        'def ', 'return', 'loop', 'array', 'list', 'dict', 'string',
    ]
    lower = text.lower()
    return any(kw in lower for kw in coding_keywords)


def parse_all_actions(raw):
    """Extract ALL [ACT] lines from response. Filters empty actions."""
    acts = []
    for line in raw.split('\n'):
        stripped = line.strip()
        if stripped.startswith('[ACT]'):
            action = stripped[5:].strip()
            if action:
                acts.append(action)
    return acts


def chat(providers, history, user_input, msg_count, voice_mgr=None):
    # Window and truncate history before appending
    history = _trim_history(history)
    history.append({"role": "user", "content": user_input})

    system_prompt = build_system_prompt()
    messages = [ChatMessage(role="system", content=system_prompt)]
    messages.extend(ChatMessage(role=m['role'], content=m['content']) for m in history)

    from orc.tools import get_native_tools
    native_tools = get_native_tools()

    use_coding = is_coding_task(user_input)
    response = providers.chat(messages, coding=use_coding, tools=native_tools)
    raw = response.content if response else ""
    if not raw:
        return history

    msg, _ = parse_response(raw)
    acts = parse_all_actions(raw)

    # Legacy action execution fallback (if model returned [ACT] tags)
    all_results = []
    if acts:
        for act in acts:
            spinner = Spinner("working")
            spinner.start()
            result, ok = execute_action(act)
            spinner.stop()

            tag = "[RESULT]" if ok else "[ERR]"
            result_trunc = result[:MAX_RESULT_CHARS] + ('...' if len(result) > MAX_RESULT_CHARS else '')
            all_results.append(f"{tag} {act}:\n{result_trunc}")

            if ok:
                print(f"\033[90m  \u2713 {act}\033[0m")
            else:
                print(f"\033[31m  \u2717 {act}: {result_trunc[:100]}\033[0m")

    if msg:
        print(f"\033[34m[oryn]\033[0m {msg}\n")

    if voice_mgr and voice_mgr.tts_enabled and msg:
        threading.Thread(target=voice_mgr.speak, args=(msg,), daemon=True).start()

    if acts:
        summary = msg[:MAX_RESULT_CHARS]
        if all_results:
            summary += "\n" + "\n".join(all_results)
        history.append({"role": "assistant", "content": summary[:MAX_RESULT_CHARS * 2]})
    else:
        history.append({"role": "assistant", "content": msg[:MAX_RESULT_CHARS]})

    # STM compression at a sane interval
    if msg_count % STM_COMPRESS_INTERVAL == 0 and needs_compression():
        compress_stm(providers)

    # Periodic task cleanup
    if msg_count % 5 == 0:
        try:
            get_task_manager().cleanup()
        except Exception:
            pass

    return history


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY
# ══════════════════════════════════════════════════════════════════════════════

def start():
    # Load config
    config = load_config()

    # Initialize providers
    providers = ProviderManager(config)
    if not providers.providers:
        print("\n\033[33m[setup]\033[0m No AI provider keys found in environment or .env.\n")
        print("Oryn runs on Google Gemini (Free Tier: 15 RPM, 1M TPM).")
        print("Get your free Gemini API key at: https://aistudio.google.com/\n")
        try:
            user_key = input("Enter your Gemini API key: ").strip()
            if user_key:
                env_file = os.path.join(os.path.dirname(__file__), '..', '.env')
                with open(env_file, 'a', encoding='utf-8') as f:
                    f.write(f"\nGEMINI_API_KEY={user_key}\n")
                os.environ['GEMINI_API_KEY'] = user_key
                load_dotenv(env_file, override=True)
                providers = ProviderManager(config)
        except (KeyboardInterrupt, EOFError):
            print("\nSetup cancelled.")
            sys.exit(1)

        if not providers.providers:
            print("\033[31m[error]\033[0m No AI providers could be initialized. Check your API key in .env.\n")
            sys.exit(1)

    # Initialize plugins
    from orc.plugins import PluginLoader
    plugins = PluginLoader()
    if config.get('plugins', {}).get('enabled', True):
        plugins.load_all()

    # Initialize voice
    from orc.voice import VoiceManager
    voice_mgr = VoiceManager(config)
    voice_mode = False  # Voice mode toggle

    # Log startup
    log_startup()

    # Check for updates on start
    if config.get('update', {}).get('check_on_start', True):
        update_result = check_update(config)
        if update_result.get('available'):
            print(f"\033[33mupdate available: v{update_result['current']} -> v{update_result['latest']}\033[0m")
            print(f"\033[90m{update_result.get('url', '')}\033[0m\n")

    clear_screen()
    print_banner()
    print(f"\033[90mprovider: {providers.active_name} | model: {providers.active_model}\033[0m")
    print(f"\033[90mvoice: OFF (type 'voice' to enable)\033[0m\n")

    history = []
    msg_count = 0
    voice_mode_ref = {'active': False}

    while True:
        try:
            # Get prompt based on voice mode
            if voice_mode_ref['active']:
                prompt = "\033[90m\033[34m[VOICE]\033[0m \033[97m"
            else:
                prompt = "\033[90moryn@sys:~$\033[0m \033[97m"

            # Voice mode: auto-listen after response
            if voice_mode_ref['active'] and voice_mgr:
                print("\033[34m[voice]\033[0m listening... (speak now)")
                voice_input = voice_mgr.listen()
                if voice_input:
                    print(f"\033[90myou: {voice_input}\033[0m")
                    user_input = voice_input
                else:
                    user_input = input(prompt).strip()
                    print("\033[0m", end="")
            else:
                user_input = input(prompt).strip()
                print("\033[0m", end="")

            if not user_input:
                continue

            # Plugin message hooks
            plugin_response = plugins.on_message(user_input)
            if plugin_response:
                print(f"\033[34m[oryn]\033[0m {plugin_response}\n")
                continue

            # Raw command mode
            if user_input.startswith('!'):
                handle_raw(user_input)
                continue

            # Built-in commands
            if handle_builtin(user_input, config, providers, voice_mgr, plugins, voice_mode_ref):
                continue

            # AI chat
            msg_count += 1
            history = chat(providers, history, user_input, msg_count, voice_mgr)

        except KeyboardInterrupt:
            print("\n\n\033[34m[oryn]\033[0m ctrl+c caught. type 'exit' to shut down.\n")
        except Exception as e:
            logger.error(f"Shell error: {e}")
            print(f"\n\033[31m[error]\033[0m {e}\n")
