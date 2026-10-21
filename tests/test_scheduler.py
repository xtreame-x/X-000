"""Scheduler: due alarms/timers, repeat handling, idempotent draining."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent.runtime import Dispatcher
from agent.scheduler import drain_due, pending


def test_due_alarm_fires_once(dispatcher: Dispatcher, ctx):
    dispatcher.call("alarm_create_v0", {"at": "+5m", "label": "later"})
    future = datetime.now(timezone.utc) + timedelta(minutes=10)

    fired = drain_due(ctx.state, now=future)
    assert len(fired) == 1
    assert fired[0]["kind"] == "alarm"
    assert fired[0]["label"] == "later"

    assert drain_due(ctx.state, now=future) == []
    assert pending(ctx.state, now=future)["alarmsScheduled"] == 0


def test_not_yet_due_alarm_does_not_fire(dispatcher: Dispatcher, ctx):
    dispatcher.call("alarm_create_v0", {"at": "+30m"})
    assert drain_due(ctx.state) == []


def test_daily_alarm_advances_instead_of_dying(dispatcher: Dispatcher, ctx):
    dispatcher.call("alarm_create_v0", {"at": "+1m", "repeat": "daily"})
    future = datetime.now(timezone.utc) + timedelta(minutes=2)

    fired = drain_due(ctx.state, now=future)
    assert len(fired) == 1

    stored = ctx.state.collection("personal/alarms.json")[0]
    assert stored["status"] == "scheduled"
    assert datetime.fromisoformat(stored["scheduledUtc"]) > future


def _seed_alarm(ctx, scheduled: datetime, repeat: str) -> None:
    ctx.state.save_collection(
        "personal/alarms.json",
        [
            {
                "id": "alm_test",
                "label": "seed",
                "repeat": repeat,
                "timezone": "UTC",
                "scheduledAt": scheduled.isoformat(),
                "scheduledUtc": scheduled.isoformat(),
                "status": "scheduled",
                "createdAt": scheduled.isoformat(),
            }
        ],
    )


def test_weekday_alarm_skips_the_weekend(ctx):
    # 2026-10-23 is a Friday 09:00; the next weekday occurrence is Monday 09:00.
    _seed_alarm(ctx, datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc), "weekdays")
    friday = datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc)

    assert len(drain_due(ctx.state, now=friday)) == 1
    stored = ctx.state.collection("personal/alarms.json")[0]
    following = datetime.fromisoformat(stored["scheduledUtc"])
    assert following.weekday() == 0
    assert following.date().isoformat() == "2026-10-26"
    assert following.hour == 9


def test_offline_host_fast_forwards_repeating_alarm(ctx):
    """A daily alarm drained 5 days late must not refire once per drain call."""
    _seed_alarm(ctx, datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc), "daily")
    late = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)

    assert len(drain_due(ctx.state, now=late)) == 1
    stored = ctx.state.collection("personal/alarms.json")[0]
    assert datetime.fromisoformat(stored["scheduledUtc"]) > late
    assert drain_due(ctx.state, now=late) == []


def test_timer_finishes_once(dispatcher: Dispatcher, ctx):
    dispatcher.call("timer_create_v0", {"seconds": 1, "label": "tea"})
    future = datetime.now(timezone.utc) + timedelta(seconds=5)

    fired = drain_due(ctx.state, now=future)
    assert len(fired) == 1
    assert fired[0]["kind"] == "timer"
    assert fired[0]["status"] == "finished"
    assert drain_due(ctx.state, now=future) == []
    assert pending(ctx.state, now=future)["timersRunning"] == 0


def test_pending_reports_counts(dispatcher: Dispatcher, ctx):
    dispatcher.call("alarm_create_v0", {"at": "+1h"})
    dispatcher.call("timer_create_v0", {"seconds": 600})
    status = pending(ctx.state)
    assert status == {"alarmsScheduled": 1, "alarmsUpcoming": 1, "timersRunning": 1}
