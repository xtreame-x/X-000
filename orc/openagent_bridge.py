"""
OpenAgent Bridge for Oryn.
Provides seamless access to the 65-tool OpenAgent suite:
Google Drive, Timers, Alarms, Calendar, Places/Maps, Sports, Three.js 3D, and SketchUp.
"""

import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
from orc.logger import get_logger

logger = get_logger('openagent_bridge')

# Ensure OpenAgent package is importable
ORYN_ROOT = Path(__file__).resolve().parent.parent
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
    if _initialized:
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
        res = tool.invoke(args, ctx)
        return res.text_content()
    except Exception as e:
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

def create_timer(seconds: int, label: str = "Timer") -> str:
    """Start a countdown timer on the machine.
    
    Args:
        seconds: Duration of the timer in seconds (e.g. 300 for 5 minutes).
        label: Label or purpose of the timer.
        
    Returns:
        Timer confirmation with end time.
    """
    return call_openagent_tool('timer_create_v0', {'seconds': seconds, 'label': label})


def create_alarm(time_iso: str, label: str = "Alarm") -> str:
    """Create a durable alarm for a specific time.
    
    Args:
        time_iso: ISO 8601 timestamp for the alarm (e.g. '2026-10-21T18:00:00Z').
        label: Label or reason for the alarm.
        
    Returns:
        Alarm confirmation.
    """
    return call_openagent_tool('alarm_create_v0', {'time': time_iso, 'label': label})


def search_calendar(query: str, start_date: str = "", end_date: str = "") -> str:
    """Search calendar events by keyword and optional date range.
    
    Args:
        query: Text search query for calendar events.
        start_date: Optional start date ISO string (e.g. '2026-10-21').
        end_date: Optional end date ISO string (e.g. '2026-10-22').
        
    Returns:
        Matching calendar events.
    """
    args = {'query': query}
    if start_date:
        args['start_date'] = start_date
    if end_date:
        args['end_date'] = end_date
    return call_openagent_tool('calendar_search_v0', args)


def create_calendar_event(title: str, start_time: str, end_time: str, description: str = "") -> str:
    """Create a new calendar event.
    
    Args:
        title: Title/summary of the event.
        start_time: ISO start time string.
        end_time: ISO end time string.
        description: Description or notes for the event.
        
    Returns:
        Confirmation of event creation.
    """
    return call_openagent_tool('event_create_v0', {
        'title': title,
        'start_time': start_time,
        'end_time': end_time,
        'description': description
    })


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
    act = action.lower().strip()
    if act == 'search':
        return call_openagent_tool('mcp__Google_Drive__search_files', {'query': query_or_name})
    elif act == 'list_recent':
        return call_openagent_tool('mcp__Google_Drive__list_recent_files', {})
    elif act == 'read':
        return call_openagent_tool('mcp__Google_Drive__read_file_content', {'file_id': file_id})
    elif act == 'create':
        return call_openagent_tool('mcp__Google_Drive__create_file', {'name': query_or_name, 'content': content})
    elif act == 'update':
        return call_openagent_tool('mcp__Google_Drive__update_file', {'file_id': file_id, 'content': content})
    elif act == 'trash':
        return call_openagent_tool('mcp__Google_Drive__trash_file', {'file_id': file_id})
    return f"unknown Google Drive action: {action}"


def openagent_execute(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
    """Execute any tool from the 65-tool OpenAgent catalog by name with optional dictionary arguments.
    
    Args:
        tool_name: The exact tool name (e.g., 'mcp__Three_js_3D_Viewer__show_threejs_scene', 'places_map_display_v0', 'chart_display_v0', 'user_location_v0', 'user_time_v0').
        arguments: Optional dictionary of arguments matching the tool's parameters.
        
    Returns:
        Tool execution output.
    """
    return call_openagent_tool(tool_name, arguments)
