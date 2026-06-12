from flask import Blueprint, render_template, redirect, url_for, flash, send_file, current_app, request, Response, stream_with_context
import json
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models import Client, AIRun, Document, FormData
from app.services.queue import get_queue
from app.services.ai_engine import run_client_analysis, answer_doc_question, DOC_QA_PROMPT
from app.services.doc_text import extract_text
from app.services.audit_service import log as audit_log
from app.services.cfr_service import CFRService
from app.services.docx_export import export_docx
from app.services.email_service import send_email_html

ai_bp = Blueprint("ai", __name__, url_prefix="/ai")

def _load_ai_context(db, client_id: int):
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return None, None, None, None
    latest = db.query(AIRun).filter_by(org_id=current_user.org_id, client_id=c.id).order_by(AIRun.created_at.desc()).first()
    docs = db.query(Document).filter_by(org_id=current_user.org_id, client_id=c.id).order_by(Document.created_at.desc()).all()
    parsed = None
    if latest and latest.result_json:
        try:
            parsed = json.loads(latest.result_json)
        except Exception:
            parsed = None
    return c, latest, docs, parsed

def _load_qa_history(db, client_id: int):
    fd = db.query(FormData).filter_by(org_id=current_user.org_id, client_id=client_id, form_key="AI_DOC_QA").first()
    if fd and fd.data_json:
        try:
            data = json.loads(fd.data_json) or {}
            if isinstance(data, list):
                return data, ""
            return (data.get("messages") or []), (data.get("last_doc_id") or "")
        except Exception:
            return [], ""
    return [], ""

def _save_qa_history(db, client_id: int, messages, last_doc_id: str = ""):
    fd = db.query(FormData).filter_by(org_id=current_user.org_id, client_id=client_id, form_key="AI_DOC_QA").first()
    if not fd:
        fd = FormData(org_id=current_user.org_id, client_id=client_id, form_key="AI_DOC_QA", data_json="[]")
        db.add(fd)
    fd.data_json = json.dumps({"messages": messages, "last_doc_id": last_doc_id}, indent=2)
    db.commit()

@ai_bp.get("/client/<int:client_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def client_ai(client_id: int):
    db = SessionLocal()
    c, latest, docs, parsed = _load_ai_context(db, client_id)
    if not c:
        return "Not found", 404
    messages, last_doc_id = _load_qa_history(db, c.id)
    return render_template("ai/client_ai.html", client=c, latest=latest, docs=docs, parsed=parsed, qa_messages=messages, qa_doc_id=last_doc_id)

@ai_bp.post("/client/<int:client_id>/ask")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def ask_ai(client_id: int):
    db = SessionLocal()
    c, latest, docs, parsed = _load_ai_context(db, client_id)
    if not c:
        return "Not found", 404
    doc_id = request.form.get("doc_id") or ""
    question = (request.form.get("question") or "").strip()
    if not question:
        for k, v in request.form.items():
            if k in ("csrf_token", "doc_id"):
                continue
            if isinstance(v, str) and v.strip():
                question = v.strip()
                break
    messages, last_doc_id = _load_qa_history(db, c.id)
    if not doc_id:
        doc_id = last_doc_id or "all"
    history_lines = []
    for m in messages[-6:]:
        role = m.get("role") or ""
        content = (m.get("content") or "").strip()
        if content:
            history_lines.append(f"{role.upper()}: {content}")
    history_text = "\n".join(history_lines)
    intake_data = {}
    try:
        intake = db.query(FormData).filter_by(org_id=current_user.org_id, client_id=c.id, form_key="INTAKE_V1").first()
        if intake and intake.data_json:
            intake_data = json.loads(intake.data_json)
    except Exception:
        intake_data = {}
    latest_summary = ""
    try:
        if latest and latest.result_json:
            parsed_latest = json.loads(latest.result_json)
            if isinstance(parsed_latest, dict):
                latest_summary = parsed_latest.get("summary","") or ""
    except Exception:
        latest_summary = ""
    extra_context = json.dumps({
        "client": {
            "name": c.display_name(),
            "email": c.email,
            "phone": c.phone,
            "service_branch": c.service_branch,
            "service_entry_date": c.service_entry_date,
            "service_discharge_date": c.service_discharge_date,
            "dob": c.dob,
            "status": c.status,
            "claim_number": c.claim_number,
        },
        "intake": intake_data,
        "latest_ai_summary": latest_summary,
    }, ensure_ascii=True)
    def _doc_text_for(d):
        text = ""
        if d.extracted_text_path:
            try:
                with open(d.extracted_text_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except Exception:
                text = ""
        if not text and d.storage_path:
            text = extract_text(d.storage_path)
        return text

    if doc_id == "all":
        combined = []
        total = 0
        for d in docs:
            t = _doc_text_for(d)
            if not t.strip():
                continue
            chunk = f"\n\n--- FILE: {d.filename} ---\n{t}\n"
            combined.append(chunk)
            total += len(chunk)
            if total >= 12000:
                break
        doc_text = "".join(combined)
        answer = answer_doc_question(doc_text, question, history_text, extra_context)
        messages.append({"role": "user", "content": question})
        messages.append({"role": "assistant", "content": answer})
        _save_qa_history(db, c.id, messages[-40:], doc_id)
        return render_template("ai/client_ai.html", client=c, latest=latest, docs=docs, parsed=parsed, qa_answer=answer, qa_doc=None, qa_question=question, qa_messages=messages, qa_doc_id=doc_id)

    if not doc_id.isdigit():
        return render_template("ai/client_ai.html", client=c, latest=latest, docs=docs, parsed=parsed, qa_error="Document not found.", qa_messages=messages, qa_doc_id=doc_id)
    d = db.get(Document, int(doc_id))
    if not d or d.org_id != current_user.org_id or d.client_id != c.id:
        return render_template("ai/client_ai.html", client=c, latest=latest, docs=docs, parsed=parsed, qa_error="Document not found.", qa_messages=messages, qa_doc_id=doc_id)
    doc_text = _doc_text_for(d)
    answer = answer_doc_question(doc_text, question, history_text, extra_context)
    messages.append({"role": "user", "content": question})
    messages.append({"role": "assistant", "content": answer})
    _save_qa_history(db, c.id, messages[-40:], doc_id)
    return render_template("ai/client_ai.html", client=c, latest=latest, docs=docs, parsed=parsed, qa_answer=answer, qa_doc=d, qa_question=question, qa_messages=messages, qa_doc_id=doc_id)

@ai_bp.post("/client/<int:client_id>/ask/clear")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def clear_ai_chat(client_id: int):
    db = SessionLocal()
    c, latest, docs, parsed = _load_ai_context(db, client_id)
    if not c:
        return "Not found", 404
    _save_qa_history(db, c.id, [], "")
    flash("AI chat cleared.", "success")
    return redirect(url_for("ai.client_ai", client_id=c.id))


@ai_bp.post("/client/<int:client_id>/ask/stream")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def ask_ai_stream(client_id: int):
    db = SessionLocal()
    c, latest, docs, parsed = _load_ai_context(db, client_id)
    if not c:
        return "Not found", 404
    doc_id = request.form.get("doc_id") or ""
    question = (request.form.get("question") or "").strip()
    if not question:
        return Response("Please enter a question.", mimetype="text/plain")
    messages, last_doc_id = _load_qa_history(db, c.id)
    if not doc_id:
        doc_id = last_doc_id or "all"
    history_lines = []
    for m in messages[-6:]:
        role = m.get("role") or ""
        content = (m.get("content") or "").strip()
        if content:
            history_lines.append(f"{role.upper()}: {content}")
    history_text = "\n".join(history_lines)
    intake_data = {}
    try:
        intake = db.query(FormData).filter_by(org_id=current_user.org_id, client_id=c.id, form_key="INTAKE_V1").first()
        if intake and intake.data_json:
            intake_data = json.loads(intake.data_json)
    except Exception:
        intake_data = {}
    latest_summary = ""
    try:
        if latest and latest.result_json:
            parsed_latest = json.loads(latest.result_json)
            if isinstance(parsed_latest, dict):
                latest_summary = parsed_latest.get("summary","") or ""
    except Exception:
        latest_summary = ""
    extra_context = json.dumps({
        "client": {
            "name": c.display_name(),
            "email": c.email,
            "phone": c.phone,
            "service_branch": c.service_branch,
            "service_entry_date": c.service_entry_date,
            "service_discharge_date": c.service_discharge_date,
            "dob": c.dob,
            "status": c.status,
            "claim_number": c.claim_number,
        },
        "intake": intake_data,
        "latest_ai_summary": latest_summary,
    }, ensure_ascii=True)

    def _doc_text_for(d):
        text = ""
        if d.extracted_text_path:
            try:
                with open(d.extracted_text_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except Exception:
                text = ""
        if not text and d.storage_path:
            text = extract_text(d.storage_path)
        return text

    if doc_id == "all":
        combined = []
        total = 0
        for d in docs:
            t = _doc_text_for(d)
            if not t.strip():
                continue
            chunk = f"\n\n--- FILE: {d.filename} ---\n{t}\n"
            combined.append(chunk)
            total += len(chunk)
            if total >= 12000:
                break
        doc_text = "".join(combined)
    else:
        if not doc_id.isdigit():
            return Response("Document not found.", mimetype="text/plain")
        d = db.get(Document, int(doc_id))
        if not d or d.org_id != current_user.org_id or d.client_id != c.id:
            return Response("Document not found.", mimetype="text/plain")
        doc_text = _doc_text_for(d)

    from openai import OpenAI
    if not current_app.config.get("OPENAI_API_KEY"):
        return Response("AI is not configured yet (OPENAI_API_KEY missing).", mimetype="text/plain")
    client = OpenAI(api_key=current_app.config["OPENAI_API_KEY"])
    model = current_app.config.get("OPENAI_MODEL", "gpt-4.1-mini")
    prompt = (
        "DOCUMENT_TEXT:\n"
        f"{doc_text[:12000]}\n\n"
        f"CLIENT_CONTEXT:\n{extra_context[:8000]}\n\n"
        f"CHAT_HISTORY:\n{history_text}\n\n"
        f"QUESTION:\n{question.strip()}\n"
    )

    def generate():
        answer_parts = []
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": DOC_QA_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                stream=True,
            )
            for event in stream:
                delta = event.choices[0].delta.content if event.choices else None
                if delta:
                    answer_parts.append(delta)
                    yield delta
        finally:
            answer = "".join(answer_parts).strip()
            if answer:
                messages.append({"role": "user", "content": question})
                messages.append({"role": "assistant", "content": answer})
                _save_qa_history(db, c.id, messages[-40:], doc_id)

    return Response(stream_with_context(generate()), mimetype="text/plain")

@ai_bp.post("/client/<int:client_id>/run")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def run_ai(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    run = AIRun(org_id=current_user.org_id, client_id=c.id, requested_by_user_id=current_user.id, status="QUEUED")
    db.add(run); db.commit()
    audit_log(current_user.org_id, current_user.id, "AI_RUN_QUEUED", "AIRun", run.id, detail=f"client_id={c.id}")
    try:
        q = get_queue("ai")
        q.enqueue(run_client_analysis, run.id)
        flash("AI analysis started. Refresh in a moment to see results.", "success")
    except Exception:
        run_client_analysis(run.id)
        flash("AI analysis completed.", "success")
    return redirect(url_for("ai.client_ai", client_id=c.id))


@ai_bp.post("/client/<int:client_id>/email")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def email_ai_results(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    if not c.email:
        flash("Client email is missing.", "error")
        return redirect(url_for("ai.client_ai", client_id=c.id))
    latest = db.query(AIRun).filter_by(org_id=current_user.org_id, client_id=c.id).order_by(AIRun.created_at.desc()).first()
    if not latest or not latest.result_json:
        flash("No AI results available to email.", "error")
        return redirect(url_for("ai.client_ai", client_id=c.id))

    subject = f"AI Scan Results - {c.display_name()}"
    summary = ""
    try:
        parsed = json.loads(latest.result_json)
        summary = parsed.get("summary","") if isinstance(parsed, dict) else ""
    except Exception:
        summary = ""
    text_body = f"""AI Scan Results for {c.display_name()}

Status: {latest.status}

Summary:
{summary}

For full details, please log in to your Client Portal.
"""
    html_body = f"""<!doctype html><html><body style='font-family:Arial,sans-serif;'>
<h2>AI Scan Results</h2>
<p><strong>Client:</strong> {c.display_name()}</p>
<p><strong>Status:</strong> {latest.status}</p>
<h3>Summary</h3>
<p>{summary}</p>
<p style='font-size:12px;color:#555'>For full details, please log in to your Client Portal.</p>
</body></html>"""
    ok, msg = send_email_html(c.email, subject, text_body, html_body, context="AI_RESULTS", org_id=current_user.org_id, actor_user_id=current_user.id)
    if ok:
        audit_log(current_user.org_id, current_user.id, "AI_RESULTS_EMAILED", "AIRun", latest.id, detail=f"client_id={c.id}")
        flash("AI results emailed to client.", "success")
    else:
        flash(f"Email failed: {msg}", "error")
    return redirect(url_for("ai.client_ai", client_id=c.id))


@ai_bp.get("/client/<int:client_id>/cfr-report.docx")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def export_cfr_report(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404

    latest = db.query(AIRun).filter_by(org_id=current_user.org_id, client_id=c.id).order_by(AIRun.created_at.desc()).first()
    if not latest or not latest.result_json:
        return "No AI results available.", 404

    try:
        parsed = json.loads(latest.result_json)
    except Exception:
        return "AI results are not valid JSON.", 400

    conditions = parsed.get("conditions") or []
    findings = [{"condition_name": (cnd.get("name") or "").strip()} for cnd in conditions if cnd.get("name")]
    svc = CFRService(base_dir=current_app.root_path + "/..")
    svc.load()
    enriched = svc.enrich_findings(findings)
    cfr_map = {e.get("condition_name"): e for e in enriched}

    lines = [
        f"AI CFR Mapping Report",
        f"Client: {c.display_name()}",
        f"Claim #: {getattr(c, 'claim_number', '')}",
        "",
        "Summary:",
        str(parsed.get("summary") or ""),
        "",
    ]
    lines.append("Presumptive Conditions (Gulf War - Present):")
    lines.append("")
    presumptive = parsed.get("presumptive") or {}
    pres_conditions = presumptive.get("conditions") or []
    if pres_conditions:
        for p in pres_conditions:
            lines.append(f"Condition: {p.get('name') or ''}")
            lines.append(f"Category: {p.get('category') or ''}")
            lines.append(f"Authority: {p.get('authority') or ''}")
            lines.append(f"Confirmation: {p.get('confirmation') or ''}")
            lines.append(f"Evidence Minimum: {p.get('evidence_minimum') or ''}")
            lines.append(f"Nexus Required: {p.get('nexus_required') or ''}")
            lines.append(f"Claim Summary: {p.get('claim_summary') or ''}")
            lines.append(f"Claim Language: {p.get('claim_language') or ''}")
            lines.append("")
    else:
        lines.append("None identified.")
        lines.append("")

    if presumptive.get("secondary_conditions"):
        lines.append("Secondary Conditions to Consider:")
        for s in presumptive.get("secondary_conditions") or []:
            lines.append(f"- {s}")
        lines.append("")

    if presumptive.get("evidence_needed"):
        lines.append("Evidence Still Needed:")
        for s in presumptive.get("evidence_needed") or []:
            lines.append(f"- {s}")
        lines.append("")

    if presumptive.get("strategy"):
        lines.append("Recommended Claim Strategy:")
        for s in presumptive.get("strategy") or []:
            lines.append(f"- {s}")
        lines.append("")

    if presumptive.get("logic_log"):
        lines.append("Presumptive Logic Log:")
        for s in presumptive.get("logic_log") or []:
            lines.append(f"- {s}")
        lines.append("")

    if presumptive.get("disclaimer"):
        lines.append("Compliance Disclaimer:")
        lines.append(str(presumptive.get("disclaimer") or ""))
        lines.append("")

    lines.append("Conditions & CFR References:")
    lines.append("")
    for cnd in conditions:
        name = cnd.get("name") or ""
        lines.append(f"Condition: {name}")
        lines.append(f"Score: {cnd.get('score')}")
        lines.append(f"Rationale: {cnd.get('rationale') or ''}")
        mapped = cfr_map.get(name) or {}
        if mapped.get("cfr"):
            lines.append(f"CFR: {mapped.get('cfr')}")
        if mapped.get("diagnostic_code"):
            lines.append(f"Diagnostic Code: {mapped.get('diagnostic_code')}")
        if mapped.get("cfr_title"):
            lines.append(f"Title: {mapped.get('cfr_title')}")
        if mapped.get("rating_criteria"):
            lines.append("Rating Criteria:")
            for rc in mapped.get("rating_criteria") or []:
                lines.append(f"- {rc}")
        if mapped.get("evidence_tips"):
            lines.append("Evidence Tips:")
            for tip in mapped.get("evidence_tips") or []:
                lines.append(f"- {tip}")
        lines.append("")

    title = f"CFR Map — {c.display_name()}"
    path = export_docx(current_app.config.get("UPLOAD_FOLDER", ""), current_user.org_id, c.id, title, "\n".join(lines))
    audit_log(current_user.org_id, current_user.id, "AI_CFR_REPORT_EXPORTED", "AIRun", latest.id, detail=f"client_id={c.id}")
    return send_file(path, as_attachment=True)
