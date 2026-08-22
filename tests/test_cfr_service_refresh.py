"""Tests for the stale-data auto-refresh trigger in cfr_service.py."""
import os
import time
from datetime import datetime, timedelta, timezone

import app.services.queue as queue_module
import scripts.build_va_ratings_chart_ecfr as build_script
from app.services.cfr_service import maybe_trigger_stale_refresh, REFRESH_STALE_DAYS


class FakeQueue:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, func, *args, **kwargs):
        self.enqueued.append(func)


def _payload(age_days):
    generated = datetime.now(timezone.utc) - timedelta(days=age_days)
    return {"generated_at": generated.isoformat()}


def test_fresh_data_does_not_trigger_a_refresh(tmp_path, monkeypatch):
    fake_queue = FakeQueue()
    monkeypatch.setattr(queue_module, "get_queue", lambda name="default": fake_queue)

    maybe_trigger_stale_refresh(str(tmp_path), _payload(age_days=1))

    assert fake_queue.enqueued == []


def test_stale_data_enqueues_a_rebuild(tmp_path, monkeypatch):
    fake_queue = FakeQueue()
    monkeypatch.setattr(queue_module, "get_queue", lambda name="default": fake_queue)

    maybe_trigger_stale_refresh(str(tmp_path), _payload(age_days=REFRESH_STALE_DAYS + 1))

    assert fake_queue.enqueued == [build_script.build_chart]


def test_stale_refresh_is_not_re_enqueued_within_the_cooldown(tmp_path, monkeypatch):
    fake_queue = FakeQueue()
    monkeypatch.setattr(queue_module, "get_queue", lambda name="default": fake_queue)
    os.makedirs(tmp_path / "cfr_data", exist_ok=True)

    stale = _payload(age_days=REFRESH_STALE_DAYS + 1)
    maybe_trigger_stale_refresh(str(tmp_path), stale)
    maybe_trigger_stale_refresh(str(tmp_path), stale)

    assert len(fake_queue.enqueued) == 1


def test_a_missing_or_unparsable_generated_at_is_a_no_op(tmp_path, monkeypatch):
    fake_queue = FakeQueue()
    monkeypatch.setattr(queue_module, "get_queue", lambda name="default": fake_queue)

    maybe_trigger_stale_refresh(str(tmp_path), {})
    maybe_trigger_stale_refresh(str(tmp_path), {"generated_at": "not-a-date"})

    assert fake_queue.enqueued == []


def test_redis_being_unavailable_never_breaks_the_caller(tmp_path, monkeypatch):
    def boom(name="default"):
        raise ConnectionError("redis is down")
    monkeypatch.setattr(queue_module, "get_queue", boom)

    # Must not raise, even though get_queue() blows up.
    maybe_trigger_stale_refresh(str(tmp_path), _payload(age_days=REFRESH_STALE_DAYS + 1))
