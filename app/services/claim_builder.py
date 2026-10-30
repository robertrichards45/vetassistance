"""AI claim builder.

Stage 1 (analyze): read every document, extract service facts the 21-526EZ needs, and recommend each
claim the evidence supports with an evidence-strength score and the reasons behind it.

Stage 2 (draft): for the claims staff choose, build the 21-526EZ answers and write a full first-person
VA Form 21-4138 statement for each claim, grounded only in what the record says. Anything the record
doesn't show becomes a [VETERAN TO CONFIRM: ...] placeholder, never an invented fact.

Both stages run in the background (app.services.queue.enqueue); the page polls for status.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

from flask import current_app

from app.extensions import SessionLocal
from app.models import ClaimPacket, Client, Document, FormData
from app.services.case_review import _parse_json, _rating_criteria, ensure_document_text
from app.services.case_stages import stage_label

MAX_DOC_CHARS = 14000
MAX_EVIDENCE_CHARS = 120000
BRANCHES = ["Army", "Navy", "Marine Corps", "Air Force", "Coast Guard", "Space Force", "NOAA", "USPHS"]

ANALYZE_PROMPT = """You are a senior VA disability claims analyst preparing an original claim (VA Form 21-526EZ)
for a veteran-run claims support team. You have the veteran's whole file. Your job:

1. Extract the facts the 21-526EZ asks for, but ONLY from the documents and profile. If a fact is not
   in the record, use null (booleans) or "" (strings). Never guess dates, branches, or places.
   - DD-214 boxes are the best source for service dates, branch, component, place of separation,
     and service number. Dates: "MM/DD/YYYY" when the day is known, otherwise "MM/YYYY".
2. Recommend every claim the evidence reasonably supports, including ones the veteran may not have
   thought of:
   - direct service connection (38 CFR 3.303), including chronic diseases and continuity (3.303(b), 3.309(a))
   - secondary conditions caused or aggravated by a condition already claimed or service-connected (3.310)
   - presumptives: PACT Act / burn pits and Gulf War (3.317, 3.320), herbicide (3.307, 3.309(e)),
     Camp Lejeune (3.307(a)(7), 3.309(f)), radiation (3.309(d), 3.311), POW (3.309(c)), tinnitus/hearing
     from documented noise exposure, combat (3.304(d))
   - aggravation of a pre-existing condition (3.306)
   Use the condition name the VA would use and the most likely 4-digit diagnostic code from 38 CFR Part 4.
   Do not recommend a claim with no current symptom or diagnosis anywhere in the record; list it under
   not_recommended with the reason instead.
3. Score each claim's approval likelihood 0-100 from the evidence actually in the file. Calibrate:
   - All four elements documented (current diagnosis, in-service event, medical nexus or a presumption,
     current severity findings): 75-92.
   - Presumption applies and diagnosis documented: 70-88 (no nexus letter needed).
   - Diagnosis + in-service event but no nexus opinion: 40-60. A nexus letter is the top action.
   - In-service event only, no current diagnosis: 10-30.
   - Secondary claim with diagnosis but no medical opinion linking it: 35-55.
   Never return 100. Explain the score in plain language and name the single biggest weakness.
4. Quote short snippets (under 25 words) from the documents as evidence, naming the file.

Write plain text (no markdown). Be specific to this veteran; generic advice is worthless.

Return STRICT JSON:
{
  "veteran": {"first_name": "", "middle_initial": "", "last_name": "", "dob": "MM/DD/YYYY or ''",
              "service_number": "", "va_file_number": "", "filed_before": true/false/null, "other_names": []},
  "service": {
    "branch": "one of Army | Navy | Marine Corps | Air Force | Coast Guard | Space Force | NOAA | USPHS | ''",
    "component": "Active | Reserves | National Guard | ''",
    "entry_date": "", "exit_date": "", "place_of_separation": "",
    "additional_periods": [{"from": "", "to": ""}],
    "combat_zone_since_911": true/false/null,
    "reserves_or_guard": true/false/null,
    "pow": true/false/null,
    "receiving_retired_pay": true/false/null,
    "separation_pay": true/false/null,
    "evidence": "which document these facts came from"
  },
  "exposures": {
    "gulf_war": {"served": true/false/null, "from": "MM/YYYY", "to": "MM/YYYY"},
    "herbicide": {"served": true/false/null, "locations": "", "from": "", "to": ""},
    "other": {"asbestos": false, "shad": false, "camp_lejeune": false, "mustard_gas": false,
              "mos_toxin": false, "radiation": false, "other": "", "from": "", "to": ""},
    "additional_details": ""
  },
  "treatment_facilities": [{"disability": "", "facility": "name, city, state", "date": "MM/YYYY or ''"}],
  "claims": [
    {
      "condition": "VA-style name, e.g. 'Right knee patellofemoral syndrome'",
      "diagnostic_code": "4-digit or ''",
      "claim_type": "direct | secondary | presumptive | aggravation",
      "secondary_to": "",
      "basis": "the regulation/theory, e.g. '38 CFR 3.310 secondary to service-connected lumbar strain'",
      "event": "short in-service event/exposure/injury for the form, under 50 characters",
      "date_began": "MM/YYYY or ''",
      "likelihood": 0-100,
      "likelihood_reason": "2-3 sentences: why this score, and the single biggest weakness",
      "elements": {
        "diagnosis": {"status": "met | partial | missing", "evidence": "snippet + file, or what is missing"},
        "in_service": {"status": "met | partial | missing | not_needed", "evidence": ""},
        "nexus": {"status": "met | partial | missing | not_needed", "evidence": ""},
        "severity": {"status": "met | partial | missing", "evidence": ""}
      },
      "estimated_rating": "the percent the current evidence most supports, as a number, or null",
      "evidence_needed": ["specific document or finding that would raise the score"],
      "supporting_forms": ["e.g. 21-0781 for PTSD, 21-4142 to release private records, 21-8940 for TDIU"],
      "statement_facts": ["every specific fact from the record a personal statement should use: dates, units, places, injuries, symptoms, treatment, work and daily-life impact"]
    }
  ],
  "not_recommended": [{"condition": "", "reason": ""}],
  "missing_info": ["facts the 21-526EZ needs that are not in the file, phrased as questions for the veteran"],
  "strategy": "4-6 sentences for the team: what to file, in what order, what to gather first, any timing issues (intent to file, one-year post-discharge window, PACT Act)"
}
"""

STATEMENT_PROMPT = """You are writing VA Form 21-4138, Statement in Support of Claim, in the veteran's own voice
(first person), for one claimed condition. It will be read by a VA rater and a C&P examiner, so it must
be specific, credible, and organized, and it must help the rater apply the rating criteria.

Hard rules:
- Use ONLY facts from the record and the claim facts provided. Never invent a date, unit, place, doctor,
  medication, injury, or symptom. Where the statement needs a detail the record does not contain, write a
  bracketed placeholder the veteran must complete, e.g. [VETERAN TO CONFIRM: month and year the pain began].
- No exaggeration and no legal threats. Calm, factual, specific. Plain words a 9th grader can read.
- Describe symptoms in terms of frequency, severity, duration, and functional effect, because that is
  what 38 CFR Part 4 rates. Use the rating criteria provided to know WHICH details matter, but only
  claim what the record supports (or leave a placeholder for the veteran to confirm).
- For flare-ups, say how often, how long, and what the veteran cannot do during them.
- Lay statements are competent evidence of observable symptoms (Jandreau v. Nicholson; Buchanan v.
  Nicholson). The veteran may describe what they experience; they must not diagnose themselves.
- Never claim the veteran is unemployable unless the record says so.

Structure (use these headings in capitals, each followed by full paragraphs):
RE: <condition>, claimed as <claim type> service connection
MY SERVICE AND WHAT HAPPENED: the in-service event, injury, or exposure: when, where, what unit or duty,
  what happened, who saw it, whether it was treated or reported. For presumptives, the qualifying
  service (where and when) instead.
SYMPTOMS SINCE SERVICE: onset and continuity from service to now, including periods without treatment
  and why (e.g. no insurance, toughed it out), if the record shows it.
HOW THIS AFFECTS ME NOW: current symptoms and frequency; flare-ups; effects on work (missed days,
  changed duties, lost jobs); effects on daily life (sleep, driving, chores, standing, walking,
  lifting, relationships, concentration as applicable to this condition).
TREATMENT: providers, facilities, medications, therapy, devices, with dates when the record has them.
WHY THIS IS CONNECTED TO MY SERVICE: one short paragraph naming the basis (e.g. 38 CFR 3.303 direct,
  3.310 secondary, 3.317 or the PACT Act presumption) and the specific evidence in the file
  (name the documents). Ask VA to obtain any records it needs under 38 U.S.C. 5103A and to
  provide a C&P examination, and to resolve reasonable doubt in the veteran's favor under 38 U.S.C. 5107(b).
Close with: "I certify that the statements above are true and correct to the best of my knowledge and belief."

Length: 450 to 900 words. Every paragraph should contain specifics from this file.

Return STRICT JSON:
{
  "statement": "the full statement text with \\n\\n between paragraphs",
  "form_explanation": "how the disability relates to service, for the narrow 21-526EZ column, under 70 characters",
  "placeholders": ["each [VETERAN TO CONFIRM: ...] item, listed"],
  "exam_points": ["what the veteran should be sure to tell the C&P examiner for this condition"]
}
"""


def _model() -> str:
    return (current_app.config.get("OPENAI_CLAIMS_MODEL") or os.environ.get("OPENAI_CLAIMS_MODEL")
            or current_app.config.get("OPENAI_MODEL") or "gpt-4.1")


def _ai_json(system: str, user: str, temperature: float = 0.2) -> dict:
    from openai import OpenAI
    client = OpenAI(api_key=current_app.config["OPENAI_API_KEY"], timeout=300, max_retries=1)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    kw = {"model": _model(), "messages": msgs}
    try:
        resp = client.chat.completions.create(response_format={"type": "json_object"}, temperature=temperature, **kw)
    except Exception as exc:  # some models reject response_format or temperature
        msg = str(exc)
        if "response_format" not in msg and "temperature" not in msg:
            raise
        resp = client.chat.completions.create(**kw)
    return _parse_json(resp.choices[0].message.content or "")


def _evidence(db, packet: ClaimPacket) -> tuple[str, list[str], list[str], int]:
    docs = db.query(Document).filter_by(org_id=packet.org_id, client_id=packet.client_id).order_by(Document.created_at.asc()).all()
    blocks, used, unreadable, total = [], [], [], 0
    # DD-214s and decision letters first: they carry the service facts the form needs.
    def rank(d):
        n = (d.filename or "").lower() + " " + (d.category or "").lower()
        return 0 if ("214" in n or "service records" in n) else 1 if "decision" in n else 2
    for d in sorted(docs, key=rank):
        text = ""
        if d.extracted_text_path and os.path.exists(d.extracted_text_path):
            with open(d.extracted_text_path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        if not text.strip():
            unreadable.append(d.filename)
            continue
        chunk = text[:MAX_DOC_CHARS]
        room = MAX_EVIDENCE_CHARS - total
        if room <= 500:
            break
        chunk = chunk[:room]
        blocks.append(f"=== FILE: {d.filename} (category: {d.category or 'Uncategorized'}) ===\n{chunk}")
        used.append(d.filename)
        total += len(chunk)
    return "\n\n".join(blocks), used, unreadable, len(docs)


def _profile(db, client: Client) -> dict:
    intake = {}
    fd = db.query(FormData).filter_by(org_id=client.org_id, client_id=client.id, form_key="INTAKE_V1").first()
    if fd and fd.data_json:
        try:
            raw = json.loads(fd.data_json)
            intake = {k: v for k, v in raw.items() if v and k not in ("ssn_full", "ssn_last4")}
        except Exception:
            intake = {}
    return {
        "name": client.full_legal_name or client.display_name(), "first_name": client.first_name,
        "last_name": client.last_name, "dob": client.dob, "branch": client.service_branch,
        "service_entry": client.service_entry_date, "service_discharge": client.service_discharge_date,
        "current_combined_rating": client.va_rating_percent, "retirement_status": client.retirement_status,
        "case_stage": stage_label(client.status), "intake_answers": intake,
    }


def _set(packet_id: int, **kw) -> None:
    db = SessionLocal()
    p = db.get(ClaimPacket, packet_id)
    for k, v in kw.items():
        setattr(p, k, v)
    p.updated_at = datetime.utcnow()
    db.commit()


def run_analysis(packet_id: int) -> None:
    db = SessionLocal()
    packet = db.get(ClaimPacket, packet_id)
    if not packet:
        return
    try:
        if not current_app.config.get("OPENAI_API_KEY"):
            raise RuntimeError("AI isn't set up yet. Add OPENAI_API_KEY in Railway's Variables.")
        _set(packet_id, status="READING", error="")
        docs = db.query(Document).filter_by(org_id=packet.org_id, client_id=packet.client_id).all()
        ensure_document_text(db, docs)
        _set(packet_id, status="ANALYZING")
        db = SessionLocal()
        packet = db.get(ClaimPacket, packet_id)
        client = db.get(Client, packet.client_id)
        text, used, unreadable, n_docs = _evidence(db, packet)
        if not used:
            raise RuntimeError("None of this client's documents could be read. Upload clear copies (PDF or photos) and try again.")
        prompt = (f"VETERAN PROFILE:\n{json.dumps(_profile(db, client), ensure_ascii=True, default=str)}\n\n"
                  f"DOCUMENTS ({len(used)} readable of {n_docs}; unreadable: {', '.join(unreadable) or 'none'}):\n{text}")
        result = _ai_json(ANALYZE_PROMPT, prompt)
        claims = []
        for c in result.get("claims") or []:
            if not isinstance(c, dict) or not c.get("condition"):
                continue
            dc = re.sub(r"\D", "", str(c.get("diagnostic_code") or ""))[:4]
            c["diagnostic_code"] = dc if len(dc) == 4 else ""
            try:
                c["likelihood"] = max(0, min(95, int(c.get("likelihood") or 0)))
            except (TypeError, ValueError):
                c["likelihood"] = 0
            claims.append(c)
        claims.sort(key=lambda c: -c["likelihood"])
        result["claims"] = claims
        result["documents_reviewed"] = used
        result["unreadable_files"] = unreadable
        result["analyzed_at"] = datetime.utcnow().isoformat()
        # Pre-select the claims that are worth filing (score 40+).
        selected = [i for i, c in enumerate(claims) if c["likelihood"] >= 40]
        _set(packet_id, status="READY", analysis_json=json.dumps(result), selected_json=json.dumps(selected),
             model=_model())
        try:
            from app.services.notify import notify_user
            notify_user(packet.created_by_user_id, f"Claim recommendations ready: {client.display_name()}",
                        f"{len(claims)} claim{'s' if len(claims) != 1 else ''} recommended.",
                        f"/claims/client/{client.id}", kind="ai")
        except Exception:
            pass
    except Exception as exc:
        SessionLocal().rollback()
        _set(packet_id, status="FAILED", error=str(exc)[:2000])


# ---------------- Stage 2: forms and statements ----------------

def _yn(v) -> str:
    return "yes" if v is True else "no" if v is False else ""


def _branch(v: str) -> str:
    v = (v or "").strip().lower()
    for b in BRANCHES:
        if b.lower() == v or b.lower() in v:
            return b
    return {"usmc": "Marine Corps", "marines": "Marine Corps", "usaf": "Air Force", "usn": "Navy", "usa": "Army",
            "uscg": "Coast Guard", "ussf": "Space Force"}.get(v, "")


def build_form(client: Client, analysis: dict, claims: list[dict]) -> dict:
    """The editable 21-526EZ answers. Profile data wins over AI-extracted data for identity fields."""
    vet, svc, exp = analysis.get("veteran") or {}, analysis.get("service") or {}, analysis.get("exposures") or {}
    gw, herb, oth = exp.get("gulf_war") or {}, exp.get("herbicide") or {}, exp.get("other") or {}
    first = client.first_name or vet.get("first_name") or ""
    last = client.last_name or vet.get("last_name") or ""
    dob = vet.get("dob") or ""
    if client.dob and re.fullmatch(r"\d{4}-\d{2}-\d{2}", client.dob.strip()):
        y, m, d = client.dob.strip().split("-")
        dob = f"{m}/{d}/{y}"
    def iso_to_us(v):
        v = (v or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            y, m, d = v.split("-")
            return f"{m}/{d}/{y}"
        return v
    any_other = any(oth.get(k) for k in ("asbestos", "shad", "camp_lejeune", "mustard_gas", "mos_toxin", "radiation")) or bool(oth.get("other"))
    toxic = gw.get("served") is True or herb.get("served") is True or any_other or any(
        (c.get("claim_type") == "presumptive") for c in claims)
    return {
        "claim_process": "STANDARD",
        "id": {
            "first": first, "mi": (vet.get("middle_initial") or "")[:1], "last": last,
            "va_file": vet.get("va_file_number") or "", "filed_before": _yn(vet.get("filed_before")),
            "dob": dob, "service_number": vet.get("service_number") or "",
            "phone": client.phone or "", "email": client.email or "", "electronic": bool(client.email),
            "street": client.mailing_address1 or "", "apt": client.mailing_address2 or "",
            "city": client.mailing_city or "", "state": client.mailing_state or "", "country": "US",
            "zip": client.mailing_zip or "",
        },
        "exposure": {
            "claiming_toxic": "yes" if toxic else "no",
            "gulf_war": _yn(gw.get("served")), "gw_from": gw.get("from") or "", "gw_to": gw.get("to") or "",
            "herbicide": _yn(herb.get("served")), "herb_locations": herb.get("locations") or "",
            "herb_from": herb.get("from") or "", "herb_to": herb.get("to") or "",
            "asbestos": bool(oth.get("asbestos")), "shad": bool(oth.get("shad")),
            "camp_lejeune": bool(oth.get("camp_lejeune")), "mustard_gas": bool(oth.get("mustard_gas")),
            "mos_toxin": bool(oth.get("mos_toxin")), "radiation": bool(oth.get("radiation")),
            "other": oth.get("other") or "", "exp_from": oth.get("from") or "", "exp_to": oth.get("to") or "",
            "additional": exp.get("additional_details") or "",
        },
        "claims": [{"condition": c.get("condition", ""), "event": c.get("event", ""),
                    "explanation": c.get("form_explanation") or "", "date": c.get("date_began", "")} for c in claims],
        "treatment": [{"text": f"{t.get('disability', '')}: {t.get('facility', '')}".strip(": "),
                       "date": t.get("date") or "", "no_date": not t.get("date")}
                      for t in (analysis.get("treatment_facilities") or [])[:3] if isinstance(t, dict)],
        "service": {
            "other_names": "; ".join(vet.get("other_names") or []),
            "branch": _branch(svc.get("branch") or client.service_branch or ""),
            "component": svc.get("component") or "",
            "entry": iso_to_us(svc.get("entry_date") or client.service_entry_date),
            "exit": iso_to_us(svc.get("exit_date") or client.service_discharge_date),
            "place_sep": svc.get("place_of_separation") or "",
            "addl_from": ((svc.get("additional_periods") or [{}])[0] or {}).get("from", "") if svc.get("additional_periods") else "",
            "addl_to": ((svc.get("additional_periods") or [{}])[0] or {}).get("to", "") if svc.get("additional_periods") else "",
            "combat_911": _yn(svc.get("combat_zone_since_911")), "reserves": _yn(svc.get("reserves_or_guard")),
            "pow": _yn(svc.get("pow")), "retired_pay": _yn(svc.get("receiving_retired_pay")),
            "sep_pay": _yn(svc.get("separation_pay")),
        },
    }


def run_drafting(packet_id: int) -> None:
    db = SessionLocal()
    packet = db.get(ClaimPacket, packet_id)
    if not packet:
        return
    try:
        if not current_app.config.get("OPENAI_API_KEY"):
            raise RuntimeError("AI isn't set up yet. Add OPENAI_API_KEY in Railway's Variables.")
        client = db.get(Client, packet.client_id)
        analysis = json.loads(packet.analysis_json or "{}")
        chosen = [i for i in json.loads(packet.selected_json or "[]") if isinstance(i, int)]
        claims = [analysis["claims"][i] for i in chosen if 0 <= i < len(analysis.get("claims") or [])]
        if not claims:
            raise RuntimeError("Choose at least one claim first.")
        text, used, _, _ = _evidence(db, packet)
        profile = json.dumps(_profile(db, client), ensure_ascii=True, default=str)
        service = json.dumps({"service": analysis.get("service"), "exposures": analysis.get("exposures")}, ensure_ascii=True)
        statements = []
        for c in claims:
            crit = _rating_criteria(c.get("diagnostic_code") or "") if c.get("diagnostic_code") else []
            user = (f"VETERAN PROFILE:\n{profile}\n\nSERVICE FACTS:\n{service}\n\n"
                    f"CLAIM:\n{json.dumps(c, ensure_ascii=True)}\n\n"
                    f"RATING CRITERIA FOR DC {c.get('diagnostic_code') or 'unknown'} (38 CFR Part 4):\n"
                    f"{json.dumps(crit, ensure_ascii=True) if crit else 'not available'}\n\n"
                    f"RECORD:\n{text[:90000]}")
            out = _ai_json(STATEMENT_PROMPT, user, temperature=0.4)
            c["form_explanation"] = (out.get("form_explanation") or c.get("basis") or "")[:90]
            statements.append({"condition": c.get("condition", ""), "diagnostic_code": c.get("diagnostic_code", ""),
                               "text": (out.get("statement") or "").strip(),
                               "placeholders": out.get("placeholders") or [],
                               "exam_points": out.get("exam_points") or []})
            _set(packet_id, statements_json=json.dumps(statements))  # progress: statements appear one by one
        form = build_form(client, analysis, claims)
        _set(packet_id, status="DRAFTED", form_json=json.dumps(form), statements_json=json.dumps(statements), error="")
        try:
            from app.services.notify import notify_user
            notify_user(packet.created_by_user_id, f"Claim forms drafted: {client.display_name()}",
                        "Review the 21-526EZ and statements before printing.", f"/claims/client/{client.id}", kind="ai")
        except Exception:
            pass
    except Exception as exc:
        SessionLocal().rollback()
        _set(packet_id, status="READY", error="Drafting stopped: " + str(exc)[:1900])
