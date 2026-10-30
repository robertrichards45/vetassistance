"""Email: provider selection, background sending, test-send page."""
import time

import pytest

from app.services import email_service as es


def test_not_configured_message(monkeypatch):
    for k in ("RESEND_API_KEY", "SENDGRID_API_KEY", "SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
        monkeypatch.delenv(k, raising=False)
    ok, msg = es._deliver("a@b.c", "s", "t", "")
    assert not ok and "not configured" in msg


def test_resend_used_when_key_present(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.setenv("EMAIL_FROM", "Team <support@example.org>")
    calls = {}

    class R:
        status_code = 200
        text = "{}"

    def fake_post(url, **kw):
        calls["url"] = url
        calls["json"] = kw["json"]
        return R()
    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    ok, _ = es._deliver("vet@example.com", "Hi", "text", "<p>html</p>")
    assert ok and calls["url"].endswith("/emails") and calls["json"]["from"] == "Team <support@example.org>"
    assert es.email_status()["provider"] == "Resend"


def test_background_send_does_not_block(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.setenv("EMAIL_FROM", "x@example.org")

    def slow(*a, **k):
        time.sleep(2)
        return True, "sent"
    monkeypatch.setattr(es, "_deliver", slow)
    monkeypatch.setattr(es, "_log_email_attempt", lambda *a, **k: None)
    t = time.time()
    ok, msg = es.send_email_html("a@b.c", "s", "t", "h", background=True)
    assert ok and msg == "queued" and time.time() - t < 0.5
