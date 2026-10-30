"""Intent to File reminders: 60, 30 and 7 days before the one-year deadline, plus a sign-up confirmation."""
from __future__ import annotations

import secrets
from datetime import date, timedelta

from app.extensions import SessionLocal
from app.models.itf_reminder import ItfReminder

STEPS = (60, 30, 7)


def deadline(filed_on: date) -> date:
    try:
        return filed_on.replace(year=filed_on.year + 1)
    except ValueError:  # Feb 29
        return filed_on + timedelta(days=365)


def _body(r: ItfReminder, days_left: int | None, base_url: str) -> str:
    due = deadline(r.filed_on)
    stop = f"{base_url}/tools/intent-to-file/stop/{r.token}"
    head = (f"Your Intent to File reminder is set. Your deadline is {due:%B %d, %Y}." if days_left is None else
            f"Your Intent to File expires in {days_left} days, on {due:%B %d, %Y}.")
    return (f"{head}\n\n"
            "An Intent to File holds your start date for back pay for one year. File your complete claim before the "
            "deadline to keep that date. After it passes, your start date becomes the day VA receives your claim.\n\n"
            "Check your Intent to File on VA.gov (Check your claim, decision review, or appeal status), or call VA at 800-827-1000.\n"
            f"Need help putting your claim together? {base_url}/become-client\n\n"
            f"You asked for these reminders at vetassistance.org. Stop them any time: {stop}\n")


def create(email: str, filed_on: date, base_url: str, send) -> ItfReminder:
    db = SessionLocal()
    r = ItfReminder(email=email.strip().lower()[:255], filed_on=filed_on, token=secrets.token_urlsafe(24))
    db.add(r); db.commit()
    send(r.email, "Your Intent to File reminder is set", _body(r, None, base_url))
    return r


def stop(token: str) -> bool:
    from datetime import datetime
    db = SessionLocal()
    r = db.query(ItfReminder).filter_by(token=token).first()
    if not r:
        return False
    r.stopped_at = r.stopped_at or datetime.utcnow(); db.commit()
    return True


def run_due(today: date, base_url: str, send) -> int:
    db = SessionLocal()
    n = 0
    for r in db.query(ItfReminder).filter(ItfReminder.stopped_at.is_(None)).all():
        left = (deadline(r.filed_on) - today).days
        done = set(filter(None, (r.sent or "").split(",")))
        for step in STEPS:
            if left <= step and str(step) not in done and left >= 0:
                # Send only the most urgent reminder due today; mark any skipped earlier ones as done.
                due_steps = [s for s in STEPS if left <= s and str(s) not in done]
                send(r.email, f"Your Intent to File expires in {left} days", _body(r, left, base_url))
                done |= {str(s) for s in due_steps}
                r.sent = ",".join(sorted(done, key=int, reverse=True))
                n += 1
                break
    db.commit()
    return n
