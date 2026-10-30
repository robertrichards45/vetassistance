from app.services import error_alerts


class FakeApp:
    def __init__(self): self.config = {"SUPPORT_EMAIL": "owner@example.test"}


def test_alert_sends_once_per_window(monkeypatch):
    sent = []
    monkeypatch.setattr("app.services.email_service.send_email", lambda *a, **k: sent.append(a) or (True, "ok"))
    error_alerts._last["t"] = 0
    app = FakeApp()
    assert error_alerts.notify(app, "/x", "GET", "ValueError") is True
    assert error_alerts.notify(app, "/x", "GET", "ValueError") is False
    assert len(sent) == 1 and "ValueError" in sent[0][2] and sent[0][0] == "owner@example.test"


def test_no_alert_while_testing():
    app = FakeApp(); app.config["TESTING"] = True
    error_alerts._last["t"] = 0
    assert error_alerts.notify(app, "/x", "GET", "E") is False
