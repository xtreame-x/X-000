"""Phone and personal tools (11).

Alarms, timers and events are stored durably under the state directory. Time
handling is timezone-aware end to end (``zoneinfo``), accepts ISO 8601 plus
``HH:MM`` and relative offsets, and always reports the resolved instant so the
model cannot misread a wall-clock time.
"""
from __future__ import annotations

import re
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent.config import NotConfigured
from agent.state import now_iso
from agent.tools.base import ToolContext, ToolResult, tool

EVENTS = "personal/events.json"
ALARMS = "personal/alarms.json"
TIMERS = "personal/timers.json"
DRAFTS = "personal/messages.json"

_RELATIVE = re.compile(r"^(?P<sign>[+-])(?P<amount>\d+)(?P<unit>[smhdw])$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _zone(ctx: ToolContext, tz: str | None) -> ZoneInfo:
    name = tz or ctx.settings.secret("AGENT_TIMEZONE") or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone {name!r}") from exc


def parse_when(value: str, tz: ZoneInfo, *, now: datetime | None = None) -> datetime:
    """Parse an instant.

    Accepts ISO 8601 (``2026-10-20T09:30``, trailing ``Z`` allowed), a bare
    ``HH:MM`` (today in ``tz``, or tomorrow if already past) and relative
    offsets such as ``+15m``, ``-2h``, ``+3d``.
    """
    reference = (now or datetime.now(timezone.utc)).astimezone(tz)
    text = value.strip()
    relative = _RELATIVE.match(text)
    if relative:
        seconds = _UNIT_SECONDS[relative.group("unit").lower()] * int(relative.group("amount"))
        if relative.group("sign") == "-":
            seconds = -seconds
        return reference + timedelta(seconds=seconds)

    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        hour, minute = (int(part) for part in text.split(":"))
        candidate = reference.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate < reference:
            candidate += timedelta(days=1)
        return candidate

    candidate = text.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed


def _iso(moment: datetime, tz: ZoneInfo) -> str:
    return moment.astimezone(tz).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Alarms and timers
# ---------------------------------------------------------------------------
@tool(group="Phone and personal", tags=("alarm", "wake", "reminder", "schedule"))
def alarm_create_v0(
    ctx: ToolContext,
    at: str,
    label: str = "",
    repeat: Literal["none", "daily", "weekdays", "weekly"] = "none",
    timezone_name: str | None = None,
) -> ToolResult:
    """Create an alarm.

    The alarm is stored durably and becomes due inside the agent loop; the host
    calls ``agent.scheduler.drain_due`` to collect fired alarms.

    Args:
        at: When the alarm should ring: ISO 8601, HH:MM, or a relative offset such as +15m.
        label: Text announced with the alarm.
        repeat: Repetition rule.
        timezone_name: IANA timezone for interpreting the time. Defaults to AGENT_TIMEZONE or UTC.
    """
    tz = _zone(ctx, timezone_name)
    moment = parse_when(at, tz)
    alarm = {
        "id": _new_id("alm"),
        "label": label,
        "repeat": repeat,
        "timezone": tz.key,
        "scheduledAt": _iso(moment, tz),
        "scheduledUtc": moment.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "status": "scheduled",
        "createdAt": now_iso(),
    }
    alarms = ctx.state.collection(ALARMS)
    alarms.append(alarm)
    ctx.state.save_collection(ALARMS, alarms)
    return ToolResult.text(
        f"alarm {alarm['id']} set for {alarm['scheduledAt']} ({tz.key}), repeat={repeat}",
        **alarm,
    )


@tool(group="Phone and personal", tags=("timer", "countdown", "reminder"))
def timer_create_v0(ctx: ToolContext, seconds: int, label: str = "") -> ToolResult:
    """Start a countdown timer.

    Args:
        seconds: Duration in seconds (1-86400).
        label: Text announced when the timer ends.
    """
    if not 1 <= int(seconds) <= 86400:
        return ToolResult.error("seconds must be between 1 and 86400", error="invalid_arguments")
    moment = datetime.now(timezone.utc) + timedelta(seconds=int(seconds))
    timer = {
        "id": _new_id("tmr"),
        "label": label,
        "seconds": int(seconds),
        "endsAt": moment.isoformat(timespec="seconds"),
        "status": "running",
        "createdAt": now_iso(),
    }
    timers = ctx.state.collection(TIMERS)
    timers.append(timer)
    ctx.state.save_collection(TIMERS, timers)
    return ToolResult.text(f"timer {timer['id']} ends at {timer['endsAt']}", **timer)


# ---------------------------------------------------------------------------
# Calendar events
# ---------------------------------------------------------------------------
def _events(ctx: ToolContext) -> list[dict]:
    return ctx.state.collection(EVENTS)


def _save_events(ctx: ToolContext, events: list[dict]) -> None:
    ctx.state.save_collection(EVENTS, events)


def _filter_range(
    events: list[dict], start: datetime | None, end: datetime | None
) -> list[dict]:
    selected = []
    for event in events:
        moment = datetime.fromisoformat(event["start"])
        if start and moment < start:
            continue
        if end and moment > end:
            continue
        selected.append(event)
    return selected


def _parse_bound(value: str | None, tz: ZoneInfo) -> datetime | None:
    return parse_when(value, tz) if value else None


@tool(group="Phone and personal", tags=("calendar", "search", "events", "agenda"))
def calendar_search_v0(
    ctx: ToolContext,
    query: str = "",
    start: str | None = None,
    end: str | None = None,
    limit: int = 20,
    timezone_name: str | None = None,
) -> ToolResult:
    """Search calendar events by text and date range.

    Args:
        query: Substring to match against title, location and description.
        start: Lower bound, inclusive.
        end: Upper bound, inclusive.
        limit: Maximum events to return.
        timezone_name: Timezone for interpreting bare dates.
    """
    tz = _zone(ctx, timezone_name)
    lower, upper = _parse_bound(start, tz), _parse_bound(end, tz)
    events = _filter_range(_events(ctx), lower, upper)
    needle = query.strip().lower()
    if needle:
        events = [
            e
            for e in events
            if needle in " ".join(
                str(e.get(key, "")) for key in ("title", "location", "description")
            ).lower()
        ]
    events.sort(key=lambda e: e["start"])
    events = events[: max(1, int(limit))]
    if not events:
        return ToolResult.text("No matching events.", events=[])
    body = "\n".join(
        f"- {e['start']} {e['title']}"
        + (f" @ {e['location']}" if e.get("location") else "")
        + f" [{e['id']}]"
        for e in events
    )
    return ToolResult.text(body, events=events, count=len(events))


@tool(group="Phone and personal", tags=("event", "create", "calendar", "meeting"))
def event_create_v0(
    ctx: ToolContext,
    title: str,
    start: str,
    end: str | None = None,
    location: str | None = None,
    description: str | None = None,
    timezone_name: str | None = None,
) -> ToolResult:
    """Create a calendar event.

    Args:
        title: Event title.
        start: Start instant (ISO 8601, HH:MM or relative offset such as +2h).
        end: End instant; defaults to one hour after start.
        location: Optional place.
        description: Optional notes.
        timezone_name: IANA timezone for interpreting the times.
    """
    tz = _zone(ctx, timezone_name)
    starts = parse_when(start, tz)
    ends = parse_when(end, tz) if end else starts + timedelta(hours=1)
    if ends <= starts:
        return ToolResult.error("end must be after start", error="invalid_arguments")
    event = {
        "id": _new_id("evt"),
        "title": title,
        "start": _iso(starts, tz),
        "end": _iso(ends, tz),
        "timezone": tz.key,
        "location": location,
        "description": description,
        "attendees": [],
        "remindersMinutes": [],
        "calendarId": "default",
        "allDay": False,
        "createdAt": now_iso(),
        "version": 0,
    }
    events = _events(ctx)
    events.append(event)
    _save_events(ctx, events)
    return ToolResult.text(
        f"created event {event['id']}: {title} at {event['start']}", **event
    )


@tool(group="Phone and personal", tags=("event", "create", "calendar", "attendees", "reminder"))
def event_create_v1(
    ctx: ToolContext,
    title: str,
    start: str,
    end: str | None = None,
    location: str | None = None,
    description: str | None = None,
    attendees: list[str] | None = None,
    reminders_minutes: list[int] | None = None,
    calendar_id: str = "default",
    all_day: bool = False,
    timezone_name: str | None = None,
) -> ToolResult:
    """Create a richer calendar event with attendees and reminders.

    Args:
        title: Event title.
        start: Start instant (ISO 8601, HH:MM or relative offset).
        end: End instant; defaults to one hour after start.
        location: Optional place.
        description: Optional notes.
        attendees: Email addresses or names to invite.
        reminders_minutes: Minutes before start to remind.
        calendar_id: Calendar to write to.
        all_day: Treat the event as spanning the whole day.
        timezone_name: IANA timezone for interpreting the times.
    """
    tz = _zone(ctx, timezone_name)
    starts = parse_when(start, tz)
    if all_day:
        starts = starts.replace(hour=0, minute=0, second=0, microsecond=0)
        ends = starts + timedelta(days=1)
    else:
        ends = parse_when(end, tz) if end else starts + timedelta(hours=1)
    if ends <= starts:
        return ToolResult.error("end must be after start", error="invalid_arguments")
    reminders = sorted({int(m) for m in (reminders_minutes or []) if int(m) >= 0})
    event = {
        "id": _new_id("evt"),
        "title": title,
        "start": _iso(starts, tz),
        "end": _iso(ends, tz),
        "timezone": tz.key,
        "location": location,
        "description": description,
        "attendees": list(attendees or []),
        "remindersMinutes": reminders,
        "calendarId": calendar_id,
        "allDay": bool(all_day),
        "createdAt": now_iso(),
        "version": 1,
    }
    events = _events(ctx)
    events.append(event)
    _save_events(ctx, events)
    return ToolResult.text(
        f"created event {event['id']}: {title} at {event['start']} "
        f"({len(event['attendees'])} attendee(s))",
        **event,
    )


@tool(group="Phone and personal", tags=("event", "update", "edit", "calendar"))
def event_update_v0(
    ctx: ToolContext,
    event_id: str,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    location: str | None = None,
    description: str | None = None,
    timezone_name: str | None = None,
) -> ToolResult:
    """Update fields of an existing event.

    Args:
        event_id: Identifier returned by an event_create tool.
        title: New title.
        start: New start instant.
        end: New end instant.
        location: New location.
        description: New notes.
        timezone_name: IANA timezone for interpreting new times.
    """
    events = _events(ctx)
    for event in events:
        if event["id"] != event_id:
            continue
        tz = _zone(ctx, timezone_name or event.get("timezone"))
        if title is not None:
            event["title"] = title
        if start is not None:
            event["start"] = _iso(parse_when(start, tz), tz)
        if end is not None:
            event["end"] = _iso(parse_when(end, tz), tz)
        if location is not None:
            event["location"] = location
        if description is not None:
            event["description"] = description
        if datetime.fromisoformat(event["end"]) <= datetime.fromisoformat(event["start"]):
            return ToolResult.error("end must be after start", error="invalid_arguments")
        event["updatedAt"] = now_iso()
        _save_events(ctx, events)
        return ToolResult.text(f"updated event {event_id}", **event)
    return ToolResult.error(f"no event with id {event_id!r}", error="not_found", eventId=event_id)


@tool(group="Phone and personal", tags=("event", "delete", "cancel", "calendar"))
def event_delete_v0(ctx: ToolContext, event_id: str) -> ToolResult:
    """Delete an event.

    Args:
        event_id: Identifier of the event to delete.
    """
    events = _events(ctx)
    remaining = [e for e in events if e["id"] != event_id]
    if len(remaining) == len(events):
        return ToolResult.error(f"no event with id {event_id!r}", error="not_found", eventId=event_id)
    _save_events(ctx, remaining)
    return ToolResult.text(f"deleted event {event_id}", eventId=event_id, deleted=True)


@tool(group="Phone and personal", tags=("event", "search", "calendar", "agenda"))
def event_search_v0(
    ctx: ToolContext,
    query: str = "",
    start: str | None = None,
    end: str | None = None,
    limit: int = 20,
    timezone_name: str | None = None,
) -> ToolResult:
    """Search events (alias of calendar_search_v0 with the same semantics).

    Args:
        query: Substring to match against title, location and description.
        start: Lower bound, inclusive.
        end: Upper bound, inclusive.
        limit: Maximum events to return.
        timezone_name: Timezone for interpreting bare dates.
    """
    return calendar_search_v0(
        ctx=ctx, query=query, start=start, end=end, limit=limit, timezone_name=timezone_name
    )


# ---------------------------------------------------------------------------
# Location and time
# ---------------------------------------------------------------------------
@tool(group="Phone and personal", tags=("location", "where", "gps", "city"))
def user_location_v0(ctx: ToolContext) -> ToolResult:
    """Report the user's approximate location.

    Uses AGENT_LATITUDE/AGENT_LONGITUDE/AGENT_LOCATION_LABEL when configured,
    otherwise falls back to coarse IP geolocation. Never raises: an offline
    machine gets a structured error instead.
    """
    latitude = ctx.settings.secret("AGENT_LATITUDE")
    longitude = ctx.settings.secret("AGENT_LONGITUDE")
    if latitude and longitude:
        return ToolResult.text(
            ctx.settings.secret("AGENT_LOCATION_LABEL") or f"{latitude}, {longitude}",
            latitude=float(latitude),
            longitude=float(longitude),
            source="configuration",
            precision="exact",
        )

    import httpx

    try:
        with httpx.Client(timeout=ctx.settings.request_timeout) as client:
            response = client.get("https://ipapi.co/json/")
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return ToolResult.error(
            f"location unavailable: {type(exc).__name__}: {exc}. "
            "Set AGENT_LATITUDE and AGENT_LONGITUDE to avoid geolocation lookups.",
            error="network_error",
        )
    city = payload.get("city") or ""
    region = payload.get("region") or ""
    country = payload.get("country_name") or ""
    label = ", ".join(part for part in (city, region, country) if part)
    return ToolResult.text(
        label or "unknown",
        latitude=payload.get("latitude"),
        longitude=payload.get("longitude"),
        city=city,
        region=region,
        country=country,
        timezone=payload.get("timezone"),
        source="ip-geolocation",
        precision="coarse (city-level)",
    )


@tool(group="Phone and personal", tags=("time", "clock", "now", "date", "timezone"))
def user_time_v0(ctx: ToolContext, timezone_name: str | None = None) -> ToolResult:
    """Report the current date and time.

    Args:
        timezone_name: IANA timezone to report in. Defaults to AGENT_TIMEZONE or UTC.
    """
    tz = _zone(ctx, timezone_name)
    moment = datetime.now(tz)
    offset = moment.utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    offset_label = f"UTC{sign}{abs(total_minutes) // 60:02d}:{abs(total_minutes) % 60:02d}"
    return ToolResult.text(
        f"{moment.strftime('%A, %d %B %Y %H:%M:%S')} {offset_label} ({tz.key})",
        iso=moment.isoformat(timespec="seconds"),
        date=moment.date().isoformat(),
        time=moment.strftime("%H:%M:%S"),
        weekday=moment.strftime("%A"),
        timezone=tz.key,
        utcOffset=offset_label,
        epochSeconds=int(moment.timestamp()),
        weekNumber=moment.isocalendar().week,
    )


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------
@tool(group="Phone and personal", tags=("message", "email", "compose", "draft", "send"))
def message_compose_v1(
    ctx: ToolContext,
    to: list[str],
    body: str,
    subject: str | None = None,
    channel: Literal["email", "sms", "chat"] = "email",
    send: bool = False,
) -> ToolResult:
    """Compose a message and optionally send it by email.

    Without send=true this only drafts the message and returns a mailto link, so
    nothing leaves the machine. Sending requires AGENT_SMTP_HOST, AGENT_SMTP_PORT,
    AGENT_SMTP_USER, AGENT_SMTP_PASSWORD and AGENT_SMTP_FROM.

    Args:
        to: Recipients (email addresses for channel=email).
        body: Message body.
        subject: Subject line; required to send email.
        channel: Delivery channel.
        send: Actually transmit the message instead of drafting it.
    """
    if not to:
        return ToolResult.error("at least one recipient is required", error="invalid_arguments")
    draft = {
        "id": _new_id("msg"),
        "to": list(to),
        "subject": subject,
        "body": body,
        "channel": channel,
        "createdAt": now_iso(),
        "status": "draft",
    }

    if not send:
        drafts = ctx.state.collection(DRAFTS)
        drafts.append(draft)
        ctx.state.save_collection(DRAFTS, drafts)
        mailto = ""
        if channel == "email":
            query = f"subject={quote(subject or '')}&body={quote(body)}"
            mailto = f"mailto:{','.join(to)}?{query}"
        return ToolResult.text(
            f"drafted {draft['id']} to {', '.join(to)}"
            + (f"\n{mailto}" if mailto else ""),
            **draft,
            mailtoLink=mailto,
        )

    if channel != "email":
        return ToolResult.error(
            f"no transport configured for channel={channel!r}; draft it and send manually",
            error="not_configured",
            channel=channel,
            draftId=draft["id"],
        )
    if not subject:
        return ToolResult.error("subject is required to send email", error="invalid_arguments")

    host = ctx.settings.secret("AGENT_SMTP_HOST")
    if not host:
        raise NotConfigured(
            "email sending is not configured: set AGENT_SMTP_HOST, AGENT_SMTP_PORT, "
            "AGENT_SMTP_USER, AGENT_SMTP_PASSWORD and AGENT_SMTP_FROM",
            provider="SMTP",
            env_var="AGENT_SMTP_HOST",
        )
    port = int(ctx.settings.secret("AGENT_SMTP_PORT") or 587)
    user = ctx.settings.secret("AGENT_SMTP_USER") or ""
    password = ctx.settings.secret("AGENT_SMTP_PASSWORD") or ""
    sender = ctx.settings.secret("AGENT_SMTP_FROM") or user

    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=ctx.settings.request_timeout) as smtp:
            smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.send_message(message)
    except Exception as exc:  # noqa: BLE001
        return ToolResult.error(
            f"SMTP send failed: {type(exc).__name__}: {exc}",
            error="send_failed",
            provider="SMTP",
        )

    draft["status"] = "sent"
    draft["sentAt"] = now_iso()
    drafts = ctx.state.collection(DRAFTS)
    drafts.append(draft)
    ctx.state.save_collection(DRAFTS, drafts)
    return ToolResult.text(f"sent {draft['id']} to {', '.join(to)}", **draft)


__all__ = [
    "alarm_create_v0",
    "timer_create_v0",
    "calendar_search_v0",
    "event_create_v0",
    "event_create_v1",
    "event_update_v0",
    "event_delete_v0",
    "event_search_v0",
    "user_location_v0",
    "user_time_v0",
    "message_compose_v1",
    "parse_when",
]
