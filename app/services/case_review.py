"""AI case review (v2).

Reads every document in the client's file (running OCR first where needed), then asks
the model for a claim map: for each condition, which of the four things a VA claim
needs are in the record (diagnosis, in-service event, nexus, current severity), how
strong the evidence is, what's missing, and a prioritized action plan.

Runs in the background (see app.services.queue.enqueue); the page polls for status.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

from flask import current_app

from app.extensions import SessionLocal
from app.models import AIRun, Client, ClientTask, Document, FormData
from app.services.case_stages import stage_label

MAX_DOC_CHARS = 9000        # per document
MAX_EVIDENCE_CHARS = 90000  # total across documents

REVIEW_PROMPT = """You are a senior VA disability claims reviewer working for a veteran-run claims support team.
You review a veteran's file and tell the team exactly where the claim stands and what to do next.

A VA service-connection claim needs four things for each condition:
- diagnosis: a current diagnosed disability (or, for presumptive conditions, the qualifying condition)
- in_service: an in-service event, injury, illness, or exposure (or qualifying service for a presumption)
- nexus: a medical link between the two (not needed when a presumption applies; say so)
- severity: current findings that map to the rating criteria in 38 CFR Part 4

Use ONLY the documents and profile provided. If something isn't in the record, mark it missing; never assume.
Quote short snippets (under 25 words) from the documents as evidence, naming the file.
Pick the most likely 4-digit diagnostic code from 38 CFR Part 4 for each condition.
Do not diagnose, do not guarantee outcomes, and do not give legal advice.
Write plain text values (no markdown).

Return STRICT JSON:
{
  "summary": "3-5 sentences: who the veteran is, what they're claiming, and the overall state of the file",
  "recommended_next_step": "the single most valuable thing to do next, specific and actionable",
  "conditions": [
    {
      "name": "condition as the VA would name it",
      "diagnostic_code": "4-digit DC or empty string",
      "claim_type": "new | increase | secondary | presumptive | unclear",
      "secondary_to": "primary condition if secondary, else empty",
      "strength": 0-100,
      "elements": {
        "diagnosis":  {"status": "met | partial | missing", "evidence": "snippet + file, or what is missing"},
        "in_service": {"status": "met | partial | missing | not_needed", "evidence": "..."},
        "nexus":      {"status": "met | partial | missing | not_needed", "evidence": "..."},
        "severity":   {"status": "met | partial | missing", "evidence": "current findings relevant to rating"}
      },
      "supported_rating": "the rating percent the current evidence most supports, as a number, or null if unknown",
      "rating_reasoning": "which criteria the findings meet and what would be needed for the next level",
      "missing_evidence": ["specific documents or findings still needed"],
      "exam_tips": ["what the C&P exam or DBQ should document for this condition"]
    }
  ],
  "action_plan": [
    {"title": "short imperative", "why": "one sentence", "owner": "client | staff",
     "kind": "request_document | staff_task | client_step", "priority": "high | medium | low"}
  ],
  "gaps": ["file-wide gaps"],
  "conflicts": ["inconsistencies between documents, e.g. dates, diagnoses, statements"],
  "deadlines": ["any decision dates, appeal windows, exam dates found, with the date"],
  "presumptive": {
    "eligible": true or false,
    "conditions": [{"name": "", "category": "", "authority": "e.g. 38 CFR 3.317 or PACT Act", "claim_summary": ""}],
    "evidence_needed": [""],
    "disclaimer": ""
  },
  "documents_reviewed": ["file names actually used"]
}
"""


def _openai_json(system: str, user: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=current_app.config["OPENAI_API_KEY"], timeout=240, max_retries=1)
    model = current_app.config.get("OPENAI_MODEL") or "gpt-4.1-mini"
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        resp = client.chat.completions.create(model=model, messages=msgs, response_format={"type": "json_object"})
    except Exception as exc:  # some models don't support response_format
        if "response_format" not in str(exc):
            raise
        resp = client.chat.completions.create(model=model, messages=msgs)
    return resp.choices[0].message.content or ""


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise ValueError("The AI did not return valid JSON.")


def ensure_document_text(db, docs) -> int:
    """Run text extraction / OCR for documents that don't have text yet. Returns how many were read."""
    from app.services.jobs import extract_document_text
    n = 0
    for d in docs:
        if d.extracted_text_path and os.path.exists(d.extracted_text_path):
            continue
        if not d.storage_path or not os.path.exists(d.storage_path):
            continue
        extract_document_text(d.id)
        n += 1
    db.expire_all()
    return n


def _rating_criteria(dc: str) -> list:
    path = os.path.join(current_app.root_path, "..", "cfr_data", "rating_explorer.json")
    try:
        with open(path, encoding="utf-8") as f:
            for c in json.load(f).get("conditions", []):
                if c.get("diagnostic_code") == dc:
                    return [{"percent": r["percent"], "text": r["text"]} for r in c.get("criteria", [])][:12]
    except Exception:
        pass
    return []


def run_case_review(run_id: int) -> None:
    db = SessionLocal()
    run = db.get(AIRun, run_id)
    if not run:
        return
    try:
        if not current_app.config.get("OPENAI_API_KEY"):
            raise RuntimeError("AI isn't set up yet. Add OPENAI_API_KEY in Railway's Variables.")
        run.status = "READING"
        db.commit()
        client = db.get(Client, run.client_id)
        docs = db.query(Document).filter_by(org_id=run.org_id, client_id=run.client_id).order_by(Document.created_at.asc()).all()
        ensure_document_text(db, docs)
        docs = db.query(Document).filter_by(org_id=run.org_id, client_id=run.client_id).order_by(Document.created_at.asc()).all()

        run.status = "RUNNING"
        db.commit()

        blocks, used, total, unreadable = [], [], 0, []
        for d in docs:
            text = ""
            if d.extracted_text_path and os.path.exists(d.extracted_text_path):
                with open(d.extracted_text_path, encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            if not text.strip():
                unreadable.append(d.filename)
                continue
            chunk = text[:MAX_DOC_CHARS]
            if total + len(chunk) > MAX_EVIDENCE_CHARS:
                chunk = chunk[: max(0, MAX_EVIDENCE_CHARS - total)]
            if not chunk:
                break
            blocks.append(f"=== FILE: {d.filename} (category: {d.category or 'Uncategorized'}) ===\n{chunk}")
            used.append(d.filename)
            total += len(chunk)

        intake = {}
        fd = db.query(FormData).filter_by(org_id=run.org_id, client_id=run.client_id, form_key="INTAKE_V1").first()
        if fd and fd.data_json:
            try:
                raw = json.loads(fd.data_json)
                intake = {k: v for k, v in raw.items() if k not in ("ssn_full", "ssn_last4", "phone", "mailing_address1",
                                                                   "mailing_address2", "dob") and v}
            except Exception:
                intake = {}
        open_steps = [t.title for t in db.query(ClientTask).filter_by(client_id=run.client_id, is_completed=False).all()]
        profile = {
            "branch": client.service_branch, "service_entry": client.service_entry_date,
            "service_discharge": client.service_discharge_date, "current_combined_rating": client.va_rating_percent,
            "retirement_status": client.retirement_status, "case_stage": stage_label(client.status),
            "intake": intake, "already_requested_or_open_steps": open_steps,
            "unreadable_files": unreadable,
        }
        prompt = (f"VETERAN PROFILE (no identifiers):\n{json.dumps(profile, ensure_ascii=True)}\n\n"
                  f"DOCUMENTS ({len(used)} readable of {len(docs)}):\n" + ("\n\n".join(blocks) or "(no readable documents)"))
        result = _parse_json(_openai_json(REVIEW_PROMPT, prompt))

        # Attach the official criteria for each identified diagnostic code (server-side, never AI-written).
        for cnd in result.get("conditions") or []:
            dc = re.sub(r"\D", "", str(cnd.get("diagnostic_code") or ""))[:4]
            cnd["diagnostic_code"] = dc if len(dc) == 4 else ""
            cnd["criteria"] = _rating_criteria(cnd["diagnostic_code"]) if cnd["diagnostic_code"] else []
            try:
                cnd["strength"] = max(0, min(100, int(cnd.get("strength") or 0)))
            except (TypeError, ValueError):
                cnd["strength"] = 0
        result["schema"] = 2
        result["documents_reviewed"] = used
        result["unreadable_files"] = unreadable
        result["document_count"] = len(docs)
        run.result_json = json.dumps(result, indent=1)
        run.status = "COMPLETED"
        run.error = ""
        run.completed_at = datetime.utcnow()
        db.commit()
        try:
            from app.services.notify import notify_user
            notify_user(run.requested_by_user_id, f"AI case review ready: {client.display_name()}",
                        result.get("recommended_next_step", "")[:240], f"/ai/client/{client.id}", kind="ai")
        except Exception:
            pass
    except Exception as exc:
        db.rollback()
        run = db.get(AIRun, run_id)
        if run:
            run.status = "FAILED"
            run.error = str(exc)[:2000]
            run.completed_at = datetime.utcnow()
            db.commit()
