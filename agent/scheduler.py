"""Due alarms and timers.

Alarms and timers are persisted by the personal tools; this module decides which
ones have fired. A host (TUI, web server, cron) calls :func:`drain_due` on a
timer and renders the returned events. Repeating alarms are advanced instead of
being deleted, so no state is lost when a host is offline for a while.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from agent.state import StateStore, now_iso

ALARMS = "personal/alarms.json"
TIMERS = "personal/timers.json"

REPEAT_DELTAS = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1)}


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _next_repeat(moment: datetime, rule: str) -> datetime | None:
    if rule in REPEAT_DELTAS:
        return moment + REPEAT_DELTAS[rule]
    if rule == "weekdays":
        candidate = moment + timedelta(days=1)
        while candidate.weekday() >= 5:  # skip Saturday (5) and Sunday (6)
            candidate += timedelta(days=1)
        return candidate
    return None


def _advance_past(moment: datetime, rule: str, reference: datetime,
                  *, max_iterations: int = 400) -> datetime | None:
    """Advance a repeating alarm to its first occurrence after ``reference``.

    A host that was offline for a week must not fire the same alarm once per
    drain call: the schedule is fast-forwarded past ``now`` in one step.
    """
    candidate = _next_repeat(moment, rule)
    iterations = 0
    while candidate is not None and candidate <= reference and iterations < max_iterations:
        candidate = _next_repeat(candidate, rule)
        iterations += 1
    return candidate


def drain_due(
    state: StateStore,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Mark due alarms and timers as fired and return them.

    Returns a list of ``{"kind": "alarm"|"timer", ...record, "firedAt": ...}``.
    Safe to call repeatedly: once fired, a non-repeating entry is not returned
    again.
    """
    reference = now or datetime.now(timezone.utc)
    fired: list[dict[str, Any]] = []

    alarms = state.collection(ALARMS)
    changed = False
    for alarm in alarms:
        if alarm.get("status") != "scheduled":
            continue
        due = _parse(alarm["scheduledUtc"])
        if due > reference:
            continue
        fired.append({"kind": "alarm", "firedAt": now_iso(), **alarm})
        following = _advance_past(due, str(alarm.get("repeat", "none")).strip().lower(), reference)
        if following is None:
            alarm["status"] = "fired"
        else:
            alarm["scheduledUtc"] = following.astimezone(timezone.utc).isoformat(timespec="seconds")
            alarm["scheduledAt"] = following.isoformat(timespec="seconds")
            alarm["lastFiredAt"] = now_iso()
        changed = True
    if changed:
        state.save_collection(ALARMS, alarms)

    timers = state.collection(TIMERS)
    changed = False
    for timer in timers:
        if timer.get("status") != "running":
            continue
        if _parse(timer["endsAt"]) > reference:
            continue
        timer["status"] = "finished"
        timer["firedAt"] = now_iso()
        fired.append({"kind": "timer", "firedAt": timer["firedAt"], **timer})
        changed = True
    if changed:
        state.save_collection(TIMERS, timers)

    return fired[:limit]


def pending(state: StateStore, *, now: datetime | None = None) -> dict[str, int]:
    """Count scheduled alarms and running timers (for status displays)."""
    reference = now or datetime.now(timezone.utc)
    alarms = [a for a in state.collection(ALARMS) if a.get("status") == "scheduled"]
    upcoming = [a for a in alarms if _parse(a["scheduledUtc"]) > reference]
    timers = [t for t in state.collection(TIMERS) if t.get("status") == "running"]
    return {"alarmsScheduled": len(alarms), "alarmsUpcoming": len(upcoming), "timersRunning": len(timers)}


__all__ = ["drain_due", "pending"]
