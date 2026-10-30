"""The case pipeline every client moves through.

Client.status stores the stage label. Older records used free-form values
("Intake Received", "Active", "Pending", "Closed"); stage_key() maps those
onto the pipeline so nothing needs a data migration.
"""
from __future__ import annotations

STAGES: list[tuple[str, str, str]] = [
    # key, label (stored in Client.status), what the client sees
    ("intake", "New intake", "We received your information and are reviewing it."),
    ("gathering", "Gathering evidence", "We're collecting records and statements for your claim."),
    ("review", "Ready for review", "Your packet is being checked for gaps before filing."),
    ("filed", "Filed with VA", "Your claim has been submitted to the VA."),
    ("waiting", "Awaiting VA decision", "The VA is reviewing your claim. This step often takes months."),
    ("decision", "Decision received", "The VA made a decision. We'll go over it with you."),
    ("closed", "Closed", "This case is complete."),
]

STAGE_KEYS = [s[0] for s in STAGES]
STAGE_LABELS = {k: label for k, label, _ in STAGES}
STAGE_CLIENT_TEXT = {k: text for k, _, text in STAGES}

_LEGACY = {
    "intake received": "intake",
    "new": "intake",
    "active": "gathering",
    "in progress": "gathering",
    "pending": "waiting",
    "submitted": "filed",
    "closed": "closed",
    "complete": "closed",
    "completed": "closed",
}


def stage_key(status: str | None) -> str:
    s = (status or "").strip().lower()
    for key, label, _ in STAGES:
        if s == label.lower() or s == key:
            return key
    return _LEGACY.get(s, "intake")


def stage_index(status: str | None) -> int:
    return STAGE_KEYS.index(stage_key(status))


def stage_label(status: str | None) -> str:
    return STAGE_LABELS[stage_key(status)]
