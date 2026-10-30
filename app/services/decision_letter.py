"""Explain a VA decision letter in plain English. Nothing the veteran uploads is saved."""
from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta

MAX_CHARS = 40000
OUTCOMES = {"granted", "denied", "increased", "reduced", "continued", "deferred", "other"}

SYSTEM = """You explain a U.S. Department of Veterans Affairs (VA) decision letter to the veteran in plain English.
Use ONLY what the letter says. Never guess a percentage, date or reason that is not in the text. If something is unclear, say so.
Return JSON with exactly these keys:
{
 "decision_date": "YYYY-MM-DD or empty string if not stated",
 "summary": "2-3 short sentences: what VA decided overall",
 "combined_rating": "the combined rating if the letter states one, else empty string",
 "items": [ {"condition": "...", "outcome": "granted|denied|increased|reduced|continued|deferred|other",
             "percent": "rating percent as written, or empty", "effective_date": "as written, or empty",
             "reason": "VA's reason in one or two plain sentences, from the letter"} ],
 "evidence_gaps": ["specific evidence the letter says was missing or not favorable, in plain words"],
 "next_steps": [ {"option": "Supplemental Claim|Higher-Level Review|Board Appeal|No action needed|Other",
                  "fits_when": "one sentence tied to THIS letter's facts"} ],
 "questions_to_check": ["things the veteran should double-check against their records"]
}
Keep every string short. Do not give legal advice; describe options."""


def extract_text(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:4] == b"%PDF":
        import fitz  # PyMuPDF
        with fitz.open(stream=data, filetype="pdf") as doc:
            parts = [page.get_text() for page in doc]
        return "\n".join(parts)
    return data.decode("utf-8", errors="ignore")


def clean_text(text: str) -> str:
    text = re.sub(r"\b\d{3}-?\d{2}-?\d{4}\b", "[number removed]", text or "")  # SSN or file number
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:MAX_CHARS]


def looks_like_decision(text: str) -> bool:
    t = text.lower()
    hits = sum(k in t for k in ("department of veterans affairs", "rating decision", "we made a decision", "service connection",
                                "evaluation", "effective date", "your claim", "granted", "denied", "va form"))
    return len(t) > 400 and hits >= 2


def parse_date(value: str) -> date | None:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def normalize(raw: dict) -> dict:
    def s(v, n=400):
        return str(v or "").strip()[:n]
    items = []
    for it in (raw.get("items") or [])[:30]:
        if not isinstance(it, dict) or not s(it.get("condition")):
            continue
        outcome = s(it.get("outcome"), 20).lower()
        items.append({"condition": s(it.get("condition"), 120), "outcome": outcome if outcome in OUTCOMES else "other",
                      "percent": s(it.get("percent"), 12), "effective_date": s(it.get("effective_date"), 40),
                      "reason": s(it.get("reason"), 500)})
    steps = [{"option": s(x.get("option"), 40), "fits_when": s(x.get("fits_when"), 300)}
             for x in (raw.get("next_steps") or [])[:5] if isinstance(x, dict) and s(x.get("option"))]
    decided = parse_date(s(raw.get("decision_date"), 30))
    return {
        "summary": s(raw.get("summary"), 800),
        "combined_rating": s(raw.get("combined_rating"), 20),
        "decisions": items,
        "evidence_gaps": [s(x, 300) for x in (raw.get("evidence_gaps") or [])[:8] if s(x)],
        "next_steps": steps,
        "questions": [s(x, 300) for x in (raw.get("questions_to_check") or [])[:6] if s(x)],
        "decision_date": decided.isoformat() if decided else "",
        # Supplemental Claim, Higher-Level Review and Board Appeal must generally be filed within one year of the decision date.
        "review_deadline": (decided + timedelta(days=365)).isoformat() if decided else "",
    }


def explain(text: str, ai_json) -> dict:
    return normalize(ai_json(SYSTEM, "VA decision letter text:\n\n" + text))
