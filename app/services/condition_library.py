"""Condition library: data access, the rules that apply per body system, and AI guides."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from functools import lru_cache

from flask import current_app

GENERAL_RULES = [
    ("38 CFR 4.3", "Reasonable doubt goes to the veteran when the evidence is roughly balanced."),
    ("38 CFR 4.7", "When the picture falls between two levels, the higher level applies if the findings more nearly approximate it."),
    ("38 CFR 4.21", "A rating doesn't require every listed symptom; the overall picture controls."),
    ("38 CFR 4.14", "No pyramiding: the same symptoms can't be rated twice under different codes."),
]

SYSTEM_RULES = {
    "Musculoskeletal": [
        ("38 CFR 4.40 and 4.45", "Functional loss from pain, weakness, fatigability, and incoordination counts, not just measured range of motion (DeLuca v. Brown)."),
        ("38 CFR 4.59", "Painful motion of a joint warrants at least the minimum compensable rating for that joint."),
        ("Correia v. McDonald (2016)", "Joint exams must test active and passive motion, weight-bearing and non-weight-bearing, and the opposite joint when undamaged."),
        ("Sharp v. Shulkin (2017)", "Examiners must estimate additional loss of motion during flare-ups, using the veteran's own description if they can't observe one."),
        ("Separate knee ratings", "Limited flexion (5260), limited extension (5261), and instability (5257) can be rated separately (VAOPGCPREC 9-2004 and 23-97)."),
        ("38 CFR 4.26", "When both arms or both legs are affected, add the bilateral factor before combining with other ratings."),
    ],
    "Muscle injuries": [
        ("38 CFR 4.56", "Muscle injuries are rated slight, moderate, moderately severe, or severe based on the wound, treatment, and cardinal signs."),
        ("38 CFR 4.55", "Rules limit combining muscle-group ratings that act on the same joint."),
    ],
    "Mental health": [
        ("38 CFR 4.130", "All mental disorders use one General Rating Formula based on occupational and social impairment."),
        ("38 CFR 4.126", "Rate on frequency, severity, and duration of symptoms and the resulting impairment, not the diagnosis."),
        ("Mauerhan v. Principi (2002)", "The listed symptoms are examples; symptoms not listed still count toward a level."),
        ("Vazquez-Claudio v. Shinseki (2013)", "The symptoms must cause the level of occupational and social impairment described."),
        ("One rating", "Co-existing mental conditions get one combined mental-health rating, not separate ratings."),
    ],
    "Neurological": [
        ("38 CFR 4.124a", "Peripheral nerves are rated by degree of incomplete paralysis: mild, moderate, moderately severe, severe; or complete."),
        ("Sensory only", "When involvement is wholly sensory, the rating should be mild or at most moderate."),
        ("38 CFR 4.123 and 4.124", "Neuritis is capped at severe incomplete paralysis without organic changes; neuralgia at moderate."),
        ("Migraine (8100)", "Ratings turn on how often prostrating attacks occur over the last several months; a headache log is strong evidence."),
    ],
    "Ear": [("Tinnitus (6260)", "One 10% rating, whether it's in one ear or both.")],
    "Hearing": [
        ("38 CFR 4.85", "Hearing loss is rated mechanically from puretone averages and Maryland CNC speech scores using Tables VI, VIa, and VII."),
        ("38 CFR 4.86", "Exceptional patterns of hearing loss can use Table VIa for a higher result."),
    ],
    "Respiratory": [("38 CFR 4.96", "Most respiratory codes can't be combined with each other; rate the predominant disability.")],
    "Cardiovascular": [
        ("METs testing", "Many heart ratings turn on METs from exercise testing or an examiner's estimate (38 CFR 4.104)."),
        ("Hypertension (7101)", "Readings must be confirmed two or more times on at least three different days."),
    ],
    "Digestive": [("38 CFR 4.113 and 4.114", "Many digestive codes can't be combined with each other; rate the predominant disability, elevating it if the overall picture warrants.")],
    "Skin": [("Skin formula", "Most skin conditions are rated by percentage of total or exposed body area affected, or by the type and length of treatment in the past 12 months.")],
}

GUIDE_PROMPT = """You write concise, accurate staff reference guides for a veteran-run VA disability claims team.
You are given the official 38 CFR Part 4 criteria for one diagnostic code. Explain how the code is rated
so staff can write strong, accurate rating write-ups. Ground everything in the criteria provided; when you add
general VA practice, keep it standard and well established. Do not invent case law or numbers.
Plain text values only (no markdown). Return STRICT JSON:
{
  "overview": "2-3 sentences: what this code covers and how it's rated",
  "levels": [{"percent": number, "in_plain_english": "what this level looks like day to day",
              "evidence_that_supports_it": ["specific findings, records, or test results"]}],
  "exam_checklist": ["what the C&P exam / DBQ must document for an accurate rating"],
  "common_secondary_conditions": ["conditions commonly claimed secondary to this one"],
  "common_mistakes": ["frequent reasons this condition is underrated or denied, and how to prevent them"],
  "writeup_tips": ["how to argue for the correct level in a write-up"]
}"""


def _data_path() -> str:
    return os.path.join(current_app.root_path, "..", "cfr_data", "rating_explorer.json")


@lru_cache(maxsize=2)
def _load(path: str, mtime: float) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["_by_dc"] = {c["diagnostic_code"]: c for c in data.get("conditions", [])}
    data["_formulas"] = {f["name"]: f for f in data.get("formulas", [])}
    return data


def library() -> dict:
    p = _data_path()
    return _load(p, os.path.getmtime(p)) if os.path.exists(p) else {"conditions": [], "formulas": [], "_by_dc": {}, "_formulas": {}}


def condition(dc: str) -> dict | None:
    return library()["_by_dc"].get(dc)


def rules_for(system: str) -> list:
    return SYSTEM_RULES.get(system, [])


def criteria_text(c: dict) -> str:
    lib = library()
    lines = [f"{c['condition']} (DC {c['diagnostic_code']}, 38 CFR {c['cfr_section']}, {c.get('system', '')})"]
    if c.get("instruction"):
        lines.append("How it is rated: " + c["instruction"])
    for r in c.get("criteria", []):
        lines.append(f"{r['percent']}%: {r['text']}")
    for fn in c.get("formula_names", []):
        f = lib["_formulas"].get(fn)
        if f:
            lines.append(f"{f['name']}:")
            lines += [f"  {r['percent']}%: {r['text']}" for r in f.get("criteria", [])]
            lines += [f"  {n}" for n in f.get("notes", [])[:8]]
    lines += c.get("notes", [])[:10]
    return "\n".join(lines)[:12000]


def related(c: dict, limit: int = 8) -> list:
    """Nearby codes in the same body system, plus the 'see also' code."""
    lib = library()
    same = [x for x in lib["conditions"] if x.get("system") == c.get("system") and x["diagnostic_code"] != c["diagnostic_code"]]
    dc = int(c["diagnostic_code"])
    same.sort(key=lambda x: abs(int(x["diagnostic_code"]) - dc))
    out = same[:limit]
    if c.get("see_also") and lib["_by_dc"].get(c["see_also"]):
        out = [lib["_by_dc"][c["see_also"]]] + [x for x in out if x["diagnostic_code"] != c["see_also"]][: limit - 1]
    return out


def generate_guide(dc: str) -> None:
    """Background job: write and cache the AI guide for one diagnostic code."""
    from app.extensions import SessionLocal
    from app.models import ConditionGuide
    from app.services.case_review import _openai_json, _parse_json
    db = SessionLocal()
    g = db.query(ConditionGuide).filter_by(diagnostic_code=dc).first()
    if not g:
        return
    try:
        c = condition(dc)
        if not c:
            raise ValueError("Unknown diagnostic code")
        if not current_app.config.get("OPENAI_API_KEY"):
            raise RuntimeError("AI isn't set up yet. Add OPENAI_API_KEY in Railway's Variables.")
        g.status = "RUNNING"
        db.commit()
        content = _parse_json(_openai_json(GUIDE_PROMPT, "OFFICIAL CRITERIA:\n" + criteria_text(c)))
        g.content_json = json.dumps(content)
        g.model = current_app.config.get("OPENAI_MODEL") or ""
        g.status, g.error, g.updated_at = "READY", "", datetime.utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        g = db.query(ConditionGuide).filter_by(diagnostic_code=dc).first()
        if g:
            g.status, g.error, g.updated_at = "FAILED", str(exc)[:1000], datetime.utcnow()
            db.commit()


WRITEUP_KINDS = {
    "analysis": ("Rating analysis",
                 "Write a staff rating analysis memo: the evidence, which criteria it meets at the target level, why it does "
                 "not fit the level below, gaps that could lower it, and what would support the next level up. Cite 38 CFR "
                 "sections and the diagnostic code. Neutral, professional tone."),
    "argument": ("Argument for a higher rating",
                 "Write a persuasive but accurate argument (for a supplemental claim, higher-level review, or increase) that "
                 "the evidence supports the target level. Tie each finding to the specific criteria language. Cite the "
                 "diagnostic code and 38 CFR sections, and the general rules in 4.3, 4.7, and 4.21 where they help."),
    "statement": ("Veteran statement draft",
                  "Write a first-person statement in support of claim in the veteran's own voice describing how the "
                  "condition affects daily life, work, and family, consistent with the facts. Don't cite codes or regulations. "
                  "Use [brackets] only for one or two specific facts that are missing. End with: 'Draft for the veteran to "
                  "review, personalize, and sign.'"),
}


def writeup_messages(c: dict, kind: str, target: str, facts: str, client_ctx: str) -> list:
    title, instruction = WRITEUP_KINDS.get(kind, WRITEUP_KINDS["analysis"])
    system = ("You help a veteran-run claims team write accurate VA disability write-ups. Use only the facts given and "
              "the official criteria provided. Never invent medical findings, dates, or test results; where a fact is "
              "missing, say what's needed. No guarantees, no legal advice. Plain text, short paragraphs, no markdown "
              "headings. " + instruction)
    rules = "\n".join(f"{a}: {b}" for a, b in GENERAL_RULES + rules_for(c.get("system", "")))
    user = (f"TYPE: {title}\nTARGET RATING: {target or 'not specified; recommend the level the facts support'}\n\n"
            f"OFFICIAL CRITERIA:\n{criteria_text(c)}\n\nRULES THAT APPLY:\n{rules}\n\n"
            f"CLIENT CONTEXT:\n{client_ctx or 'none'}\n\nFACTS AND FINDINGS FROM THE FILE:\n{facts or 'none provided'}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user[:20000]}]
