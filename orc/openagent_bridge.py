"""
OpenAgent Bridge for Oryn.
Provides seamless access to the 65-tool OpenAgent suite:
Google Drive, Timers, Alarms, Calendar, Places/Maps, Sports, Three.js 3D, and SketchUp.
"""

import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from orc.logger import get_logger

logger = get_logger('openagent_bridge')

# Ensure OpenAgent package is importable
ORYN_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ORYN_ROOT / '.env', override=True)
candidate_dirs = [
    ORYN_ROOT / 'openagent' / 'agent-checkpoint-1',
    ORYN_ROOT / 'openagent',
    ORYN_ROOT,
]
for d in candidate_dirs:
    if (d / 'agent' / 'tools').exists():
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))
        break

_initialized = False
_ctx = None
_registry = None


def _init_openagent():
    global _initialized, _ctx, _registry
    load_dotenv(ORYN_ROOT / '.env', override=True)
    if _initialized:
        from agent.config import Settings
        _ctx.settings = Settings.load(ORYN_ROOT)
        return _registry, _ctx

    try:
        from agent.tools.base import REGISTRY, ToolContext
        from agent.config import Settings
        from agent.state import StateStore

        REGISTRY.load_all()
        settings = Settings.load(ORYN_ROOT)
        state_dir = ORYN_ROOT / 'data' / 'openagent_state'
        state = StateStore(state_dir)
        _ctx = ToolContext(settings=settings, state=state)
        _registry = REGISTRY
        _initialized = True
        logger.info(f"OpenAgent bridge initialized with {len(_registry.all())} tools")
        return _registry, _ctx
    except Exception as e:
        logger.error(f"Failed to initialize OpenAgent bridge: {e}")
        return None, None


def call_openagent_tool(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
    """Execute any tool from the 65-tool OpenAgent catalog by name."""
    reg, ctx = _init_openagent()
    if not reg or not ctx:
        return "error: OpenAgent tool bridge not initialized"

    if not reg.has(tool_name):
        return f"error: tool '{tool_name}' not found in OpenAgent catalog"

    try:
        tool = reg.get(tool_name)
        args = arguments or {}
        if isinstance(args, str):
            import json
            try:
                args = json.loads(args)
            except Exception:
                args = {}

        # Auto-alias common parameter synonyms to prevent Pydantic validation errors
        if isinstance(args, dict):
            args = dict(args)
            if tool_name == 'alarm_create_v0':
                if 'time' in args and 'at' not in args:
                    args['at'] = args.pop('time')
                elif 'time_iso' in args and 'at' not in args:
                    args['at'] = args.pop('time_iso')
            elif tool_name in ('calendar_search_v0', 'event_search_v0'):
                if 'start_date' in args and 'start' not in args:
                    args['start'] = args.pop('start_date')
                if 'end_date' in args and 'end' not in args:
                    args['end'] = args.pop('end_date')
            elif tool_name in ('event_create_v0', 'event_create_v1'):
                if 'start_time' in args and 'start' not in args:
                    args['start'] = args.pop('start_time')
                if 'end_time' in args and 'end' not in args:
                    args['end'] = args.pop('end_time')
            elif tool_name == 'str_replace':
                if 'replacements' not in args and ('old_str' in args or 'old' in args):
                    old = args.pop('old_str', args.pop('old', ''))
                    new = args.pop('new_str', args.pop('new', ''))
                    args['replacements'] = [{'old': old, 'new': new}]
            elif tool_name in ('memory_read', 'memory_delete'):
                if 'key' in args and 'name' not in args:
                    args['name'] = args.pop('key')
            elif tool_name in ('memory_write', 'memory_append'):
                if 'key' in args and 'name' not in args:
                    args['name'] = args.pop('key')
                if 'value' in args and 'content' not in args:
                    args['content'] = args.pop('value')
            elif tool_name == 'memory_str_replace':
                if 'key' in args and 'name' not in args:
                    args['name'] = args.pop('key')
                if 'old_str' in args and 'old' not in args:
                    args['old'] = args.pop('old_str')
                if 'new_str' in args and 'new' not in args:
                    args['new'] = args.pop('new_str')
            elif tool_name == 'message_compose_v1':
                if 'recipient' in args and 'to' not in args:
                    args['to'] = args.pop('recipient')
                if 'message' in args and 'body' not in args:
                    args['body'] = args.pop('message')
                if 'to' in args and isinstance(args['to'], str):
                    args['to'] = [args['to']]
            elif tool_name == 'ask_user_input_v0':
                if 'prompt' in args and 'question' not in args:
                    args['question'] = args.pop('prompt')
            elif tool_name == 'recipe_display_v0':
                if 'instructions' in args and 'steps' not in args:
                    args['steps'] = args.pop('instructions')
            elif tool_name == 'suggest_connectors':
                if 'intent' in args and 'goal' not in args:
                    args['goal'] = args.pop('intent')
                elif 'query' in args and 'goal' not in args:
                    args['goal'] = args.pop('query')
            elif tool_name == 'chart_display_v0':
                if 'type' in args and 'chart_type' not in args:
                    args['chart_type'] = args.pop('type')
                if 'labels' in args and 'values' in args and 'data' not in args:
                    labels = args.pop('labels', [])
                    values = args.pop('values', [])
                    args['data'] = [{'label': l, 'value': v} for l, v in zip(labels, values)]
                    args['x'] = 'label'
                    args['y'] = 'value'
            elif tool_name == 'mcp__visualize__show_widget':
                if 'data' in args and 'content' not in args:
                    import json
                    args['content'] = json.dumps(args.pop('data'))
                if args.get('widget_type') not in ('html', 'svg', 'markdown'):
                    args['widget_type'] = 'markdown'
            elif tool_name == 'mcp__Three_js_3D_Viewer__show_threejs_scene':
                if 'scene' in args and isinstance(args['scene'], str):
                    import json
                    try:
                        args['scene'] = json.loads(args['scene'])
                    except Exception:
                        pass
            elif tool_name == 'mcp__Google_Drive__copy_file':
                if 'new_name' in args and 'name' not in args:
                    args['name'] = args.pop('new_name')
            elif tool_name == 'mcp__Google_Drive__download_file_content':
                if 'destination' not in args:
                    args['destination'] = f"downloads/{args.get('file_id', 'file')}"
            elif tool_name == 'mcp__Google_Drive__share_file':
                args.pop('type', None)
                if 'email' not in args:
                    for k in ('user', 'to', 'recipient'):
                        if k in args:
                            args['email'] = args.pop(k)
                            break
                    if 'email' not in args:
                        args['email'] = 'anyone@example.invalid'

        res = tool.invoke(args, ctx)
        return res.text_content()
    except Exception as e:
        err_str = str(e)
        if 'NotConfigured' in type(e).__name__ or 'is not configured' in err_str:
            return f"[Tool Not Configured] {err_str}"
        logger.warning(f"Error calling {tool_name}: {e}")
        return f"tool execution error ({tool_name}): {e}"


def search_openagent_tools(query: str = "") -> str:
    """Search available tools in the 65-tool catalog by keyword or group."""
    reg, _ = _init_openagent()
    if not reg:
        return "OpenAgent catalog unavailable"

    query_lower = query.lower().strip()
    matches = []
    for t in reg.all():
        if not query_lower or query_lower in t.name.lower() or query_lower in t.group.lower() or query_lower in t.description.lower():
            schema = t.params.model_json_schema()
            props = list(schema.get('properties', {}).keys())
            matches.append(f"- {t.name} [{t.group}]: {t.description[:80]}... (args: {', '.join(props)})")

    if not matches:
        return f"No tools matching '{query}' found in 65-tool catalog."
    return f"Found {len(matches)} tool(s):\n" + "\n".join(matches[:15])


# ══════════════════════════════════════════════════════════════════════════════
#  HIGH-FREQUENCY NATIVE TOOL WRAPPERS
# ══════════════════════════════════════════════════════════════════════════════

def _alert_background(delay_seconds: float, title: str, message: str):
    """Play audible chime and show Windows alert when timer/alarm triggers."""
    import time
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    try:
        import winsound
        for _ in range(3):
            winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS)
            time.sleep(0.3)
    except Exception:
        pass
    try:
        import subprocess
        safe_msg = message.replace('"', "'")
        safe_title = title.replace('"', "'")
        ps = f'''$w = New-Object -ComObject Wscript.Shell; $w.Popup("{safe_msg}", 10, "{safe_title}", 64)'''
        subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, timeout=12)
    except Exception:
        pass


def _compute_alarm_delay(at_str: str) -> Optional[float]:
    """Calculate delay in seconds from now until alarm time."""
    from datetime import datetime, timedelta
    import re
    now = datetime.now()
    at_clean = at_str.strip().lower()
    m = re.match(r'^\+?(\d+)\s*([smhd])$', at_clean)
    if m:
        val, unit = int(m.group(1)), m.group(2)
        mult = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
        return float(val * mult[unit])
    m = re.match(r'^(\d{1,2}):(\d{2})$', at_clean)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        target = now.replace(hour=h, minute=mn, second=0, microsecond=0)
        if target < now:
            target += timedelta(days=1)
        return (target - now).total_seconds()
    return None


def open_windows_clock() -> str:
    """Launch the native Windows Clock app (Alarms, Timers, Stopwatch, World Clock)."""
    import subprocess
    subprocess.run(['cmd', '/c', 'start', 'ms-clock:'], capture_output=True)
    return "Windows Clock app launched."


def create_timer(seconds: int, label: str = "Timer") -> str:
    """Start a countdown timer on the machine with live audible chime and popup alert.
    
    Args:
        seconds: Duration of the timer in seconds (e.g. 120 for 2 minutes).
        label: Label or purpose of the timer.
        
    Returns:
        Timer confirmation with end time.
    """
    res = call_openagent_tool('timer_create_v0', {'seconds': seconds, 'label': label})
    import threading
    threading.Thread(target=_alert_background, args=(seconds, "Oryn Timer", f"Timer '{label}' finished ({seconds}s)!"), daemon=True).start()
    return res


def create_alarm(at: str, label: str = "Alarm") -> str:
    """Create a durable alarm for a specific time with live audible chime and popup alert.
    
    Args:
        at: When the alarm should ring: HH:MM (e.g. '16:50' or '04:50'), ISO 8601 (e.g. '2026-10-21T16:50:00Z'), or relative offset like '+15m'.
        label: Label or reason for the alarm.
        
    Returns:
        Alarm confirmation.
    """
    res = call_openagent_tool('alarm_create_v0', {'at': at, 'label': label})
    delay = _compute_alarm_delay(at)
    if delay and delay > 0:
        import threading
        threading.Thread(target=_alert_background, args=(delay, "Oryn Alarm", f"Alarm '{label}' is ringing!"), daemon=True).start()
    return res


def search_calendar(query: str = "", start: str = "", end: str = "") -> str:
    """Search calendar events by keyword and optional date range.
    
    Args:
        query: Text search query for calendar events.
        start: Optional start date ISO string (e.g. '2026-10-21').
        end: Optional end date ISO string (e.g. '2026-10-22').
        
    Returns:
        Matching calendar events.
    """
    args = {}
    if query:
        args['query'] = query
    if start:
        args['start'] = start
    if end:
        args['end'] = end
    return call_openagent_tool('calendar_search_v0', args)


def create_calendar_event(title: str, start: str, end: str = "", description: str = "", location: str = "") -> str:
    """Create a new calendar event.
    
    Args:
        title: Title/summary of the event.
        start: ISO start time string or date.
        end: Optional ISO end time string or date.
        description: Description or notes for the event.
        location: Optional location of the event.
        
    Returns:
        Confirmation of event creation.
    """
    args = {'title': title, 'start': start}
    if end:
        args['end'] = end
    if description:
        args['description'] = description
    if location:
        args['location'] = location
    return call_openagent_tool('event_create_v0', args)


def search_places(query: str, near: str = "", limit: int = 5) -> str:
    """Search for physical locations, shops, monuments, or places using OpenStreetMap Nominatim.
    
    Args:
        query: What to look for (e.g., 'coffee shop', 'Eiffel Tower', 'library').
        near: Optional city or locality to bias the search (e.g., 'Delhi', 'New York').
        limit: Max results to return (1-10).
        
    Returns:
        Found places with full addresses and coordinates.
    """
    args = {'query': query, 'limit': min(limit, 10)}
    if near:
        args['near'] = near
    return call_openagent_tool('places_search', args)


def get_sports_scoreboard(sport: str = "soccer", league: str = "eng.1") -> str:
    """Fetch live scores and fixtures from ESPN scoreboard.
    
    Args:
        sport: Sport name ('soccer', 'baseball', 'basketball', 'football').
        league: League identifier (e.g. 'eng.1' for Premier League, 'mlb', 'nba', 'nfl').
        
    Returns:
        Current scoreboard data.
    """
    return call_openagent_tool('fetch_sports_data', {'sport': sport, 'league': league})


def google_drive_action(action: str, query_or_name: str = "", file_id: str = "", content: str = "") -> str:
    """Perform Google Drive file operations (search, read, list, create, update, trash).
    
    Args:
        action: One of 'search', 'list_recent', 'read', 'create', 'update', 'trash'.
        query_or_name: Search query or filename.
        file_id: Google Drive file ID for read/update/trash actions.
        content: Text content for create/update actions.
        
    Returns:
        Google Drive operation outcome.
    """
    logger.info(f"Native tool google_drive_action called: {action} on {query_or_name or file_id}")
    act = action.lower().strip()
    if act == 'search':
        res = call_openagent_tool('mcp__Google_Drive__search_files', {'query': query_or_name})
    elif act == 'list_recent':
        res = call_openagent_tool('mcp__Google_Drive__list_recent_files', {})
    elif act == 'read':
        res = call_openagent_tool('mcp__Google_Drive__read_file_content', {'file_id': file_id})
    elif act == 'create':
        res = call_openagent_tool('mcp__Google_Drive__create_file', {'name': query_or_name, 'content': content})
    elif act == 'update':
        res = call_openagent_tool('mcp__Google_Drive__update_file', {'file_id': file_id, 'content': content})
    elif act == 'trash':
        res = call_openagent_tool('mcp__Google_Drive__trash_file', {'file_id': file_id})
    else:
        return f"unknown Google Drive action: {action}"

    if "HTTP 401" in str(res):
        return "Google Drive error: Your access token has expired (temporary OAuth tokens expire after 1 hour). Please run auth_drive.bat to refresh it or set up permanent OAuth credentials."
    return res


def send_email(to: str, subject: str, body: str) -> str:
    """Send an email directly to a recipient using your configured SMTP credentials (e.g. Gmail).
    
    Args:
        to: The recipient's email address (e.g. 'friend@example.com').
        subject: The subject line of the email.
        body: The plain text email body content.
        
    Returns:
        Status message confirming the email was sent or error details.
    """
    logger.info(f"Native tool send_email called: to={to}, subject={subject}")
    return call_openagent_tool('message_compose_v1', {
        'to': [to],
        'subject': subject,
        'body': body,
        'send': True,
    })


def draft_email(to: str, subject: str, body: str) -> str:
    """Draft an email without sending it, returning a mailto link and local draft.
    
    Args:
        to: The recipient's email address (e.g. 'friend@example.com').
        subject: The subject line of the email.
        body: The plain text email body content.
        
    Returns:
        Status message confirming the draft was created.
    """
    logger.info(f"Native tool draft_email called: to={to}, subject={subject}")
    return call_openagent_tool('message_compose_v1', {
        'to': [to],
        'subject': subject,
        'body': body,
        'send': False,
    })


def openagent_execute(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
    """Execute any tool from the 65-tool OpenAgent catalog by name with optional dictionary arguments.
    
    Args:
        tool_name: The exact tool name (e.g., 'mcp__Three_js_3D_Viewer__show_threejs_scene', 'places_map_display_v0', 'chart_display_v0', 'user_location_v0', 'user_time_v0').
        arguments: Optional dictionary of arguments matching the tool's parameters.
        
    Returns:
        Tool execution output.
    """
    return call_openagent_tool(tool_name, arguments)
