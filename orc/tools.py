"""
Native Tools Suite for Oryn.
Follows modern agentic architecture with typed function declarations for native tool use.
"""

import os
import subprocess
from typing import List, Callable, Optional
from orc.logger import get_logger
from orc.openagent_bridge import (
    create_timer,
    create_alarm,
    open_windows_clock,
    search_calendar,
    create_calendar_event,
    search_places,
    get_sports_scoreboard,
    google_drive_action,
    send_email,
    draft_email,
    search_openagent_tools,
    openagent_execute,
)

logger = get_logger('tools')


def run_command(command: str) -> str:
    """Execute a system command (PowerShell / CMD) on the Windows machine.
    
    Args:
        command: The Windows shell command to run (e.g., 'Get-Process', 'taskkill /PID 1234 /F').
        
    Returns:
        The command output or error message.
    """
    logger.info(f"Native tool run_command: {command}")
    from orc.sysbridge import is_blacklisted
    if is_blacklisted(command):
        return "blocked: command not permitted for safety"

    try:
        proc = subprocess.run(
            ['powershell', '-NoProfile', '-Command', command],
            capture_output=True,
            text=True,
            timeout=25,
            cwd=os.path.expanduser('~')
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        
        output = []
        if stdout:
            output.append(stdout)
        if stderr:
            output.append(f"Error: {stderr}")
        if not stdout and not stderr:
            output.append("done." if proc.returncode == 0 else f"exited with code {proc.returncode}")
            
        full_res = "\n".join(output)
        # Prevent context blowup by capping output length
        if len(full_res) > 4000:
            full_res = full_res[:4000] + "\n...[truncated output]"
        return full_res
    except subprocess.TimeoutExpired:
        return "error: command timed out after 25s"
    except Exception as e:
        return f"error executing command: {e}"


def get_system_status() -> str:
    """Get fast live telemetry of the machine: CPU usage, RAM used/free, Disk, and top processes by memory consumption.
    
    Returns:
        Structured system telemetry.
    """
    logger.info("Native tool get_system_status called")
    from orc.sysbridge import get_sysinfo
    info = get_sysinfo()
    if info:
        lines = []
        if 'cpu_usage' in info and info['cpu_usage'] >= 0:
            lines.append(f"CPU: {info['cpu_usage']:.1f}%")
        if 'ram_percent' in info and info['ram_percent'] >= 0:
            lines.append(f"RAM: {info['ram_percent']:.1f}% ({info.get('ram_used_mb', 0):.0f}MB used / {info.get('ram_total_mb', 0):.0f}MB total)")
        if 'disk_percent' in info and info['disk_percent'] >= 0:
            lines.append(f"Disk: {info['disk_percent']:.1f}% ({info.get('disk_used_gb', 0):.1f}GB used / {info.get('disk_total_gb', 0):.1f}GB total)")
        processes = info.get('processes', [])
        if processes:
            lines.append("Top memory-consuming processes:")
            for p in processes[:7]:
                lines.append(f"  - {p.get('name', 'unknown')}: {p.get('ram_mb', 0):.1f}MB")
        return "\n".join(lines)

    # Fallback to PowerShell query if sysinfo.exe unavailable
    return run_command("Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 7 ProcessName, @{N='RAM_MB';E={[math]::Round($_.WorkingSet64/1MB,1)}} | Format-Table -AutoSize")


def file_operation(action: str, path: str, content: str = "", destination: str = "") -> str:
    """Perform file system operations on the local machine.
    
    Args:
        action: One of 'read', 'write', 'create', 'append', 'list', 'delete', 'move', 'copy', 'search'.
        path: Path to target file or directory.
        content: Text content to write or append (required for write/create/append).
        destination: Target destination path for move or copy actions.
        
    Returns:
        Operation outcome or file contents.
    """
    logger.info(f"Native tool file_operation: {action} on {path}")
    from orc.files import handle_file_op
    
    act_lower = action.lower().strip()
    if act_lower in ('create', 'write'):
        return handle_file_op(f"create|{path}|{content}")
    elif act_lower == 'read':
        return handle_file_op(f"read|{path}")
    elif act_lower == 'append':
        return handle_file_op(f"append|{path}|{content}")
    elif act_lower == 'list':
        return handle_file_op(f"list|{path}")
    elif act_lower == 'delete':
        return handle_file_op(f"delete|{path}")
    elif act_lower == 'move':
        return handle_file_op(f"move|{path}|{destination}")
    elif act_lower == 'copy':
        return handle_file_op(f"copy|{path}|{destination}")
    elif act_lower == 'search':
        return handle_file_op(f"search|{content}|{path}")
    else:
        return f"unknown file action: {action}"


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for real-time facts, documentation, news, or answers.
    
    Args:
        query: The search query string.
        max_results: Maximum number of search results to return (default: 5).
        
    Returns:
        Formatted web search results.
    """
    logger.info(f"Native tool web_search: {query}")
    from orc.web import search
    return search(query, max_results=max_results)


def manage_memory(action: str, topic_or_phrase: str = "") -> str:
    """Manage short-term (STM) or long-term (LTM) memories about the user and preferences.
    
    Args:
        action: 'fetch_ltm' (find past facts), 'save_ltm' (store permanent fact like 'tag:note'), or 'save_stm' (record short-term active note).
        topic_or_phrase: The topic to look up or the memory string to save.
        
    Returns:
        Retrieved memory content or confirmation.
    """
    logger.info(f"Native tool manage_memory: {action}")
    from orc.memory import fetch_ltm, save_ltm_entry, append_stm
    
    act_lower = action.lower().strip()
    if act_lower == 'fetch_ltm':
        res = fetch_ltm(topic_or_phrase)
        return res or "no matching memory found"
    elif act_lower == 'save_ltm':
        save_ltm_entry(topic_or_phrase)
        return f"saved to long-term memory: {topic_or_phrase}"
    elif act_lower == 'save_stm':
        append_stm(topic_or_phrase)
        return f"saved to short-term memory: {topic_or_phrase}"
    return f"unknown memory action: {action}"


def ui_control(command: str, argument: str = "") -> str:
    """Control browser navigation and common UI shortcuts.
    
    Args:
        command: One of 'navigate', 'new_tab', 'close_tab', 'next_tab', 'prev_tab', 'reopen_tab', 'close_browser'.
        argument: URL to navigate to when command is 'navigate'.
        
    Returns:
        Outcome of the UI action.
    """
    logger.info(f"Native tool ui_control: {command}")
    from orc.executor import UI_COMMANDS, format_output
    
    cmd_lower = command.lower().strip()
    if cmd_lower in UI_COMMANDS:
        fn = UI_COMMANDS[cmd_lower]
        stdout, stderr, code = fn(argument) if argument else fn()
        return format_output(stdout, stderr, code)
    return f"unknown UI command: {command}"


def create_or_modify_tool(tool_type: str, name: str, code: str, description: str = "", command: str = "") -> str:
    """Dynamically create or update a new plugin or custom action, immediately hot-reloading it into Oryn.
    
    Args:
        tool_type: 'plugin' (for full plugin in plugins/) or 'action' (for custom action script in orc/actions/).
        name: Name of the tool (snake_case for plugin, UPPER_CASE for action).
        code: Python execute code body.
        description: Description of what the tool does.
        command: Command trigger name for plugins.
        
    Returns:
        Creation and hot-reload status message.
    """
    logger.info(f"Native tool create_or_modify_tool: {tool_type} {name}")
    from orc.creator import create_plugin, create_action
    
    if tool_type.lower() == 'plugin':
        cmd = command or name
        return create_plugin(name, description or f"{name} plugin", cmd, code)
    elif tool_type.lower() == 'action':
        return create_action(name.upper(), code)
    return f"unknown tool_type: {tool_type}"


def get_native_tools() -> List[Callable]:
    """Return the list of native callable tools for model binding."""
    return [
        # Core system & laptop control (7 tools)
        run_command,
        get_system_status,
        file_operation,
        web_search,
        manage_memory,
        ui_control,
        create_or_modify_tool,
        # OpenAgent 75-tool bridge & high-frequency tools (12 tools)
        create_timer,
        create_alarm,
        open_windows_clock,
        search_calendar,
        create_calendar_event,
        search_places,
        get_sports_scoreboard,
        google_drive_action,
        send_email,
        draft_email,
        search_openagent_tools,
        openagent_execute,
    ]
