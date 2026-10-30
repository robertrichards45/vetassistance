from app.services import uptime_watch as uw


def make(results):
    sent, clock = [], {"t": 100000.0}
    state = {u: list(r) for u, r in results.items()}
    w = uw.Watcher(lambda s, b: sent.append(s), checker=lambda url: state[url].pop(0), now=lambda: clock["t"])
    return w, sent, clock


def test_alerts_after_two_failures_then_recovery():
    r = {uw.SITES["resumebuilder.shop"]: [False, False, False, True], uw.SITES["createdocuments.org"]: [True] * 4}
    w, sent, clock = make(r)
    w.run_once(); assert sent == []
    w.run_once(); assert sent == ["resumebuilder.shop looks down"]
    w.run_once(); assert len(sent) == 1  # no repeat within 6 hours
    w.run_once(); assert sent[-1] == "resumebuilder.shop is back up"


def test_not_started_outside_railway(monkeypatch):
    class A: config = {"TESTING": False}
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    assert uw.start(A()) is False
