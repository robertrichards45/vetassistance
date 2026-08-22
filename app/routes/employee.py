from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
import json
from datetime import datetime
from pathlib import Path
from flask_login import login_required, current_user

from app.extensions import SessionLocal, csrf
from app.models import (
    Client,
    User,
    OnboardingItem,
    EmployeeOnboardingProgress,
    Document,
    FormData,
    ClaimUpdate,
    EvidenceChecklist,
    EvidenceChecklistItem,
    DocumentScan,
    Reminder,
    RenderedArtifact,
)
from app.models.user import Role
from app.routes._authz import require_roles
from app.services.audit_service import log as audit_log
from app.services.ai_engine import run_claim_review, draft_rating_justification
from app.services.cfr_service import load_va_ratings_chart
from app.services.doc_text import extract_text
from app.services.docx_export import export_docx
from app.services.pdf_export import export_text_pdf
import os
import re

employee_bp = Blueprint("employee", __name__, url_prefix="/employee")


@employee_bp.get("")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def dashboard():
    db = SessionLocal()
    assigned = db.query(Client).filter(
        Client.org_id == current_user.org_id,
        Client.assigned_user_id == current_user.id,
        Client.is_archived == False,  # noqa: E712
    ).order_by(Client.created_at.desc()).all()
    return render_template("employee/dashboard.html", assigned=assigned)


@employee_bp.get("/clients")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def clients_redirect():
    return redirect(url_for("clients.list_clients"))


def _seed_onboarding_items(db):
    data_path = Path(current_app.root_path) / "data" / "onboarding_items.json"
    if not data_path.exists():
        return
    if db.query(OnboardingItem).count():
        return
    try:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    except Exception:
        return
    for row in payload:
        db.add(OnboardingItem(
            title=row.get("title", "").strip(),
            description=row.get("description", "").strip(),
            task_url=row.get("task_url", "").strip(),
            sort_order=int(row.get("sort_order") or 0),
            is_active=bool(row.get("is_active", True)),
            is_required=bool(row.get("is_required", True)),
        ))
    db.commit()


def _load_onboarding_items(db):
    _seed_onboarding_items(db)
    return db.query(OnboardingItem).filter_by(is_active=True).order_by(OnboardingItem.sort_order.asc(), OnboardingItem.id.asc()).all()


@employee_bp.get("/hub")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub():
    db = SessionLocal()
    items = _load_onboarding_items(db)
    completed = db.query(EmployeeOnboardingProgress).filter_by(employee_id=current_user.id, is_complete=True).all()
    done_ids = {c.item_id for c in completed}
    required_items = [i for i in items if i.is_required]
    progress = int((len([i for i in required_items if i.id in done_ids]) / len(required_items)) * 100) if required_items else 0
    return render_template(
        "employee/hub.html",
        items=items,
        done_keys=done_ids,
        progress=progress,
    )


def _doc_match(doc: Document, patterns: list[str]) -> bool:
    hay = " ".join([
        doc.filename or "",
        doc.category or "",
        doc.tags or "",
    ]).lower()
    return any(p in hay for p in patterns)


def _build_rule_checks(client: Client, intake_data: dict, docs: list[Document]) -> dict:
    checks = []
    missing = []
    score = 100

    def add(level: str, title: str, detail: str, fix: str, missing_items=None, weight=0):
        nonlocal score
        checks.append({
            "level": level,
            "title": title,
            "detail": detail,
            "fix": fix,
            "missing_evidence": missing_items or [],
        })
        if missing_items:
            missing.extend(missing_items)
        if level in ("red", "yellow"):
            score -= weight

    # Basic profile
    if not client.full_legal_name and not client.display_name():
        add("red", "Missing client name", "No name on file.", "Add full legal name.", ["Full legal name"], 10)
    if not client.dob:
        add("yellow", "Missing DOB", "Date of birth is missing.", "Add date of birth.", ["Date of birth"], 5)
    if not client.service_branch:
        add("yellow", "Missing service branch", "Service branch not recorded.", "Add branch of service.", ["Service branch"], 5)
    if not client.service_entry_date:
        add("yellow", "Missing service dates", "Service entry date missing.", "Add service dates.", ["Service dates"], 5)

    # Intake
    if not intake_data:
        add("red", "No structured intake", "INTAKE_V1 form not found.", "Complete intake form.", ["INTAKE_V1 intake form"], 15)

    # Evidence documents
    if not docs:
        add("red", "No documents uploaded", "Client has no documents.", "Upload evidence documents.", ["Evidence documents"], 25)
    else:
        if not any(_doc_match(d, ["dd214", "dd-214"]) for d in docs):
            add("red", "Missing DD214", "No DD214 found.", "Upload DD214 or equivalent discharge document.", ["DD214"], 15)
        if not any(_doc_match(d, ["str", "service treatment", "service medical"]) for d in docs):
            add("yellow", "Missing STRs", "No service treatment records found.", "Upload STRs or request from VA/NPRC.", ["Service Treatment Records"], 10)
        if not any(_doc_match(d, ["rating decision", "decision letter", "award letter", "codesheet", "code sheet"]) for d in docs):
            add("yellow", "Missing rating decision", "No VA rating decision/award letter found.", "Upload rating decision or award letter.", ["Rating decision / award letter"], 10)
        if not any(_doc_match(d, ["c&p", "compensation and pension", "exam"]) for d in docs):
            add("yellow", "Missing C&P exam", "No C&P exam found.", "Upload C&P exam or request it from VA.", ["C&P exam"], 8)
        if not any(_doc_match(d, ["nexus", "opinion", "imo", "ime"]) for d in docs):
            add("yellow", "Missing nexus evidence", "No nexus opinion or medical link document found.", "Add nexus letter if needed.", ["Nexus opinion"], 10)
        if not any(_doc_match(d, ["treatment", "clinic", "progress", "medical", "therapy"]) for d in docs):
            add("yellow", "Limited treatment evidence", "No ongoing treatment evidence found.", "Upload recent treatment notes.", ["Current treatment records"], 8)

    score = max(0, min(100, score))
    if not checks:
        checks.append({
            "level": "green",
            "title": "No rule-based gaps detected",
            "detail": "Client record has core items.",
            "fix": "Continue with claim drafting and review.",
            "missing_evidence": [],
        })
    return {"score": score, "checks": checks, "missing_evidence": sorted(set(missing))}


def _build_ai_prompt(client: Client, intake_data: dict, docs: list[Document]) -> str:
    doc_blocks = []
    text_budget = 12000
    used = 0
    for d in docs:
        snippet = ""
        if d.extracted_text_path and os.path.exists(d.extracted_text_path):
            try:
                with open(d.extracted_text_path, "r", encoding="utf-8", errors="ignore") as f:
                    snippet = f.read(4000)
            except Exception:
                snippet = ""
        if not snippet and d.storage_path and os.path.exists(d.storage_path):
            try:
                snippet = extract_text(d.storage_path)[:2000]
            except Exception:
                snippet = ""
        block = [
            f"FILE: {d.filename}",
            f"CATEGORY: {d.category}",
            f"TAGS: {d.tags}",
        ]
        if snippet:
            block.append(snippet)
        block_text = "\n".join(block)
        if used + len(block_text) > text_budget:
            break
        doc_blocks.append(block_text)
        used += len(block_text)

    def _detect_crsc_hints() -> dict:
        def grab(*keys):
            parts = []
            for k in keys:
                v = intake_data.get(k) if isinstance(intake_data, dict) else None
                if v:
                    parts.append(str(v))
            return " ".join(parts).lower()

        text = " ".join([
            grab("issues", "notes", "deadlines"),
            (client.status or "").lower(),
        ])
        doc_text = " ".join([(d.filename or "").lower() for d in docs])

        retire_kw = ["retire", "retired", "medical retirement", "tdrl", "pdrl", "dod retired pay", "crdp", "crsc"]
        combat_kw = ["combat", "ied", "deployment", "hazardous", "purple heart", "cib", "cab", "car", "hostile fire", "simulated war", "instrumentality"]
        retired = any(k in text for k in retire_kw) or any(k in doc_text for k in retire_kw)
        combat = any(k in text for k in combat_kw) or any(k in doc_text for k in combat_kw)
        return {"retired_hint": retired, "combat_hint": combat}

    profile = {
        "client": {
            "name": client.display_name(),
            "email": client.email,
            "phone": client.phone,
            "dob": client.dob,
            "service_branch": client.service_branch,
            "service_entry_date": client.service_entry_date,
            "service_discharge_date": client.service_discharge_date,
            "status": client.status,
            "claim_number": client.claim_number,
        },
        "intake": intake_data,
        "crsc_hints": _detect_crsc_hints(),
    }
    prompt = (
        "CLIENT_PROFILE:\n"
        f"{json.dumps(profile, ensure_ascii=True)}\n\n"
        "DOCUMENTS:\n"
        f"{chr(10).join(doc_blocks)}\n"
    )
    return prompt


def _load_intake_data(db, client_id: int) -> dict:
    fd = db.query(FormData).filter_by(org_id=current_user.org_id, client_id=client_id, form_key="INTAKE_V1").first()
    if not fd or not fd.data_json:
        return {}
    try:
        payload = json.loads(fd.data_json)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _build_checklist_from_intake(intake_data: dict) -> list[dict]:
    base = [
        {"label": "DD214 (or equivalent discharge document)", "category": "Service Records"},
        {"label": "Service Treatment Records (STRs)", "category": "Service Records"},
        {"label": "VA Rating Decision / Award Letter", "category": "VA Records"},
        {"label": "C&P Exam Reports", "category": "VA Records"},
        {"label": "Current Treatment Records (last 12 months)", "category": "Medical Evidence"},
        {"label": "Nexus Letter (if required)", "category": "Medical Evidence"},
        {"label": "Private DBQs (if available)", "category": "Medical Evidence"},
        {"label": "Buddy Statements / Lay Evidence", "category": "Lay Evidence"},
    ]
    issues_raw = (intake_data.get("issues") or "").strip()
    if issues_raw:
        parts = []
        for line in issues_raw.replace(";", "\n").splitlines():
            line = line.strip(" -•\t")
            if line:
                parts.append(line)
        for item in parts[:12]:
            base.append({"label": f"Diagnosis and treatment records for: {item}", "category": "Condition Evidence"})
            base.append({"label": f"In-service event evidence for: {item}", "category": "Condition Evidence"})
    return base


def _scan_document(doc: Document) -> tuple[int, list[dict], str]:
    issues = []
    score = 100
    path = doc.storage_path or ""
    if not path or not os.path.exists(path):
        issues.append({"level": "red", "title": "Missing file", "detail": "Document file not found on disk."})
        return 0, issues, "File missing."
    size_kb = int(os.path.getsize(path) / 1024) if os.path.exists(path) else 0
    if size_kb < 50:
        issues.append({"level": "yellow", "title": "Very small file", "detail": "Low file size can mean missing pages or blank scans."})
        score -= 15
    text = ""
    try:
        text = extract_text(path)[:20000]
    except Exception:
        text = ""
    text_len = len((text or "").strip())
    if text_len < 200:
        issues.append({"level": "red", "title": "No readable text", "detail": "Likely a scanned image that needs OCR."})
        score -= 45
    elif text_len < 1000:
        issues.append({"level": "yellow", "title": "Low text volume", "detail": "Text extraction is limited. Verify readability."})
        score -= 20
    if text_len >= 1000 and size_kb > 50:
        issues.append({"level": "green", "title": "Readable text detected", "detail": "Text extraction looks healthy."})
    score = max(0, min(100, score))
    summary = "Manual review recommended." if score < 70 else "Looks readable."
    return score, issues, summary


@employee_bp.get("/hub/claim-review")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_claim_review():
    db = SessionLocal()
    clients = db.query(Client).filter_by(org_id=current_user.org_id).order_by(Client.created_at.desc()).all()
    return render_template("employee/hub_claim_review.html", clients=clients)


@employee_bp.post("/hub/claim-review")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_claim_review_post():
    client_id = int(request.form.get("client_id") or 0)
    run_ai = (request.form.get("run_ai") or "") == "1"
    db = SessionLocal()
    client = db.get(Client, client_id)
    if not client or client.org_id != current_user.org_id:
        flash("Client not found.", "error")
        return redirect(url_for("employee.hub_claim_review"))

    intake_data = {}
    fd = db.query(FormData).filter_by(org_id=current_user.org_id, client_id=client.id, form_key="INTAKE_V1").first()
    if fd and fd.data_json:
        try:
            intake_data = json.loads(fd.data_json)
        except Exception:
            intake_data = {}
    docs = db.query(Document).filter_by(org_id=current_user.org_id, client_id=client.id).order_by(Document.created_at.desc()).all()

    rule_result = _build_rule_checks(client, intake_data, docs)
    ai_result = None
    if run_ai:
        prompt = _build_ai_prompt(client, intake_data, docs)
        raw = run_claim_review(prompt)
        try:
            ai_result = json.loads(raw)
        except Exception:
            ai_result = {"raw": raw}

    return render_template(
        "employee/hub_claim_review.html",
        clients=db.query(Client).filter_by(org_id=current_user.org_id).order_by(Client.created_at.desc()).all(),
        selected_client=client,
        rule_result=rule_result,
        ai_result=ai_result,
        ran_ai=run_ai,
    )


@employee_bp.get("/hub/ratings")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_ratings():
    base_dir = current_app.root_path + "/.."
    data = load_va_ratings_chart(base_dir)
    return render_template("employee/ratings_tools.html", **data)


@employee_bp.post("/hub/ratings/justification")
@csrf.exempt
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_ratings_justification():
    body = request.get_json(silent=True) or {}
    selections = body.get("selections") or []
    if not selections:
        return {"error": "Select at least one condition first."}, 400
    notes = str(body.get("notes") or "")[:4000]
    text = draft_rating_justification(selections, notes)
    return {"justification": text}


@employee_bp.get("/hub/crsc")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_crsc():
    db = SessionLocal()
    clients = db.query(Client).filter_by(org_id=current_user.org_id).order_by(Client.created_at.desc()).all()
    client_id = int(request.args.get("client_id") or 0)
    selected = db.get(Client, client_id) if client_id else None
    docs = []
    crsc_form = {}
    if selected and selected.org_id == current_user.org_id:
        docs = db.query(Document).filter_by(org_id=current_user.org_id, client_id=selected.id).order_by(Document.created_at.desc()).limit(50).all()
        fd = db.query(FormData).filter_by(org_id=current_user.org_id, client_id=selected.id, form_key="CRSC_INTAKE").first()
        if fd and fd.data_json:
            try:
                crsc_form = json.loads(fd.data_json)
            except Exception:
                crsc_form = {}
    return render_template("employee/hub_crsc.html", clients=clients, selected_client=selected, documents=docs, crsc_form=crsc_form)


@employee_bp.post("/hub/crsc/generate")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_crsc_generate():
    client_id = int(request.form.get("client_id") or 0)
    db = SessionLocal()
    client = db.get(Client, client_id)
    if not client or client.org_id != current_user.org_id:
        flash("Client not found.", "error")
        return redirect(url_for("employee.hub_crsc"))
    branch = ""
    retirement_type = ""
    fd = db.query(FormData).filter_by(org_id=current_user.org_id, client_id=client.id, form_key="CRSC_INTAKE").first()
    if fd and fd.data_json:
        try:
            data = json.loads(fd.data_json)
            branch = (data.get("branch") or "").strip()
            retirement_type = (data.get("retirement_type") or "").strip()
        except Exception:
            branch = ""
    title = f"CRSC Packet Summary - {client.display_name()}"
    body = "\n".join([
        "CRSC Packet Summary",
        "",
        f"Branch form: {branch or 'Not specified'}",
        f"Retirement type: {retirement_type or 'Not specified'}",
        "",
        "Checklist:",
        "- Service-specific CRSC application (Army / Navy / Marine Corps / Air Force)",
        "- VA rating decision(s) and code sheet",
        "- Medical evidence for combat-related condition(s)",
        "- Line-of-duty / incident reports",
        "- Combat awards, deployment records, or hazardous duty evidence",
        "- CRSC nexus statement (combat category)",
        "",
        "Notes:",
        "- CRSC is a DoD program, not a VA benefit.",
        "- Ensure combat nexus category is clearly documented.",
    ])
    docx_path = export_docx(current_app.config["STORAGE_ROOT"], current_user.org_id, client.id, title, body)
    pdf_path = export_text_pdf(current_app.config["STORAGE_ROOT"], current_user.org_id, client.id, title, title, body)
    artifact = RenderedArtifact(
        org_id=current_user.org_id,
        client_id=client.id,
        created_by_user_id=current_user.id,
        artifact_type="CRSC",
        title=title,
        web_copy=body,
        docx_path=docx_path,
        pdf_path=pdf_path,
    )
    db.add(artifact)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "CRSC_PACKET_GENERATED", "RenderedArtifact", artifact.id, detail=f"client_id={client.id}")
    flash("CRSC packet summary generated.", "success")
    return redirect(url_for("artifacts.for_client", client_id=client.id))


@employee_bp.post("/hub/crsc/nexus")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_crsc_generate_nexus():
    client_id = int(request.form.get("client_id") or 0)
    combat_category = (request.form.get("combat_category") or "").strip()
    condition = (request.form.get("condition") or "").strip()
    evidence = (request.form.get("evidence") or "").strip()
    db = SessionLocal()
    client = db.get(Client, client_id)
    if not client or client.org_id != current_user.org_id:
        flash("Client not found.", "error")
        return redirect(url_for("employee.hub_crsc"))
    if not combat_category or not condition:
        flash("Combat category and condition are required.", "error")
        return redirect(url_for("employee.hub_crsc", client_id=client.id))

    narrative = "\n".join([
        f"Combat-Related Special Compensation (CRSC) Nexus Narrative",
        "",
        f"Veteran: {client.display_name()}",
        f"Condition: {condition}",
        f"Combat category: {combat_category}",
        "",
        "Summary:",
        "The veteran's condition is combat-related and aligns with the selected category. The evidence below supports",
        "a direct connection between the in-service event and the current diagnosis. This narrative is intended for",
        "service board review and should be paired with the listed evidence.",
        "",
        "Evidence used:",
        evidence or "- (Add evidence list)",
        "",
        "Regulatory alignment:",
        f"- Category: {combat_category}",
        "- Documented exposure/event tied to qualifying combat criteria",
        "- VA-rated condition with supporting medical documentation",
    ])

    title = f"CRSC Nexus Narrative - {condition}"
    docx_path = export_docx(current_app.config["STORAGE_ROOT"], current_user.org_id, client.id, title, narrative)
    pdf_path = export_text_pdf(current_app.config["STORAGE_ROOT"], current_user.org_id, client.id, title, title, narrative)
    artifact = RenderedArtifact(
        org_id=current_user.org_id,
        client_id=client.id,
        created_by_user_id=current_user.id,
        artifact_type="CRSC_NEXUS",
        title=title,
        web_copy=narrative,
        docx_path=docx_path,
        pdf_path=pdf_path,
    )
    db.add(artifact)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "CRSC_NEXUS_GENERATED", "RenderedArtifact", artifact.id, detail=f"client_id={client.id}")
    flash("CRSC nexus narrative generated.", "success")
    return redirect(url_for("artifacts.for_client", client_id=client.id))


@employee_bp.post("/hub/crsc/intake")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_crsc_intake_save():
    client_id = int(request.form.get("client_id") or 0)
    db = SessionLocal()
    client = db.get(Client, client_id)
    if not client or client.org_id != current_user.org_id:
        flash("Client not found.", "error")
        return redirect(url_for("employee.hub_crsc"))
    payload = {
        "branch": (request.form.get("branch") or "").strip(),
        "retirement_type": (request.form.get("retirement_type") or "").strip(),
        "va_rating": (request.form.get("va_rating") or "").strip(),
        "combat_indicator": (request.form.get("combat_indicator") or "").strip(),
        "deployment_history": (request.form.get("deployment_history") or "").strip(),
        "awards": (request.form.get("awards") or "").strip(),
        "injury_origin": (request.form.get("injury_origin") or "").strip(),
        "receives_va_comp": (request.form.get("receives_va_comp") or "").strip(),
        "receives_retired_pay": (request.form.get("receives_retired_pay") or "").strip(),
        "notes": (request.form.get("notes") or "").strip(),
    }
    fd = db.query(FormData).filter_by(org_id=current_user.org_id, client_id=client.id, form_key="CRSC_INTAKE").first()
    if not fd:
        fd = FormData(org_id=current_user.org_id, client_id=client.id, form_key="CRSC_INTAKE", created_by_user_id=current_user.id)
        db.add(fd)
    fd.data_json = json.dumps(payload)
    fd.updated_at = datetime.utcnow()
    db.commit()
    flash("CRSC intake saved.", "success")
    return redirect(url_for("employee.hub_crsc", client_id=client.id))


@employee_bp.get("/hub/crsc-training")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_crsc_training():
    return render_template("employee/hub_crsc_training.html")


@employee_bp.get("/hub/crsc-appeals")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_crsc_appeals():
    return render_template("employee/hub_crsc_appeals.html")


@employee_bp.get("/hub/claim-tracker")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_claim_tracker():
    db = SessionLocal()
    clients = db.query(Client).filter_by(org_id=current_user.org_id).order_by(Client.created_at.desc()).all()
    client_id = int(request.args.get("client_id") or 0)
    selected = db.get(Client, client_id) if client_id else None
    updates = []
    if selected and selected.org_id == current_user.org_id:
        updates = db.query(ClaimUpdate).filter_by(org_id=current_user.org_id, client_id=selected.id).order_by(ClaimUpdate.created_at.desc()).all()
    return render_template("employee/hub_claim_tracker.html", clients=clients, selected_client=selected, updates=updates)


@employee_bp.post("/hub/claim-tracker")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_claim_tracker_post():
    client_id = int(request.form.get("client_id") or 0)
    status_label = (request.form.get("status_label") or "").strip()
    status_key = (request.form.get("status_key") or "").strip()
    note = (request.form.get("note") or "").strip()
    db = SessionLocal()
    client = db.get(Client, client_id)
    if not client or client.org_id != current_user.org_id:
        flash("Client not found.", "error")
        return redirect(url_for("employee.hub_claim_tracker"))
    row = ClaimUpdate(
        org_id=current_user.org_id,
        client_id=client.id,
        status_key=status_key,
        status_label=status_label or status_key,
        note=note,
        source_role="STAFF",
        created_by_user_id=current_user.id,
    )
    db.add(row)
    if status_label:
        client.status = status_label
    db.commit()
    flash("Claim update added.", "success")
    return redirect(url_for("employee.hub_claim_tracker", client_id=client.id))


@employee_bp.get("/hub/checklists")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_checklists():
    db = SessionLocal()
    clients = db.query(Client).filter_by(org_id=current_user.org_id).order_by(Client.created_at.desc()).all()
    client_id = int(request.args.get("client_id") or 0)
    selected = db.get(Client, client_id) if client_id else None
    checklists = []
    items = []
    if selected and selected.org_id == current_user.org_id:
        checklists = db.query(EvidenceChecklist).filter_by(org_id=current_user.org_id, client_id=selected.id, is_active=True).order_by(EvidenceChecklist.created_at.desc()).all()
        if checklists:
            items = db.query(EvidenceChecklistItem).filter_by(checklist_id=checklists[0].id).order_by(EvidenceChecklistItem.id.asc()).all()
    return render_template("employee/hub_checklists.html", clients=clients, selected_client=selected, checklists=checklists, items=items)


@employee_bp.post("/hub/checklists/create")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_checklists_create():
    client_id = int(request.form.get("client_id") or 0)
    title = (request.form.get("title") or "Evidence Checklist").strip()
    from_intake = (request.form.get("from_intake") or "") == "1"
    db = SessionLocal()
    client = db.get(Client, client_id)
    if not client or client.org_id != current_user.org_id:
        flash("Client not found.", "error")
        return redirect(url_for("employee.hub_checklists"))
    checklist = EvidenceChecklist(
        org_id=current_user.org_id,
        client_id=client.id,
        title=title or "Evidence Checklist",
        created_by_user_id=current_user.id,
    )
    db.add(checklist)
    db.commit()
    items = _build_checklist_from_intake(_load_intake_data(db, client.id)) if from_intake else []
    if not items:
        items = _build_checklist_from_intake({})
    for it in items:
        db.add(EvidenceChecklistItem(
            checklist_id=checklist.id,
            label=it["label"],
            category=it.get("category") or "Evidence",
        ))
    db.commit()
    flash("Checklist created.", "success")
    return redirect(url_for("employee.hub_checklists", client_id=client.id))


@employee_bp.post("/hub/checklists/item/<int:item_id>/toggle")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_checklists_toggle(item_id: int):
    db = SessionLocal()
    item = db.get(EvidenceChecklistItem, item_id)
    if not item:
        flash("Item not found.", "error")
        return redirect(url_for("employee.hub_checklists"))
    checklist = db.get(EvidenceChecklist, item.checklist_id)
    if not checklist or checklist.org_id != current_user.org_id:
        flash("Checklist not found.", "error")
        return redirect(url_for("employee.hub_checklists"))
    item.is_done = not item.is_done
    item.done_at = datetime.utcnow() if item.is_done else None
    item.done_by_user_id = current_user.id if item.is_done else None
    db.commit()
    return redirect(url_for("employee.hub_checklists", client_id=checklist.client_id))


@employee_bp.get("/hub/doc-scan")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_doc_scan():
    db = SessionLocal()
    clients = db.query(Client).filter_by(org_id=current_user.org_id).order_by(Client.created_at.desc()).all()
    client_id = int(request.args.get("client_id") or 0)
    selected = db.get(Client, client_id) if client_id else None
    docs = []
    scan = None
    scan_issues = []
    if selected and selected.org_id == current_user.org_id:
        docs = db.query(Document).filter_by(org_id=current_user.org_id, client_id=selected.id).order_by(Document.created_at.desc()).all()
        scan_id = int(request.args.get("scan_id") or 0)
        if scan_id:
            scan = db.get(DocumentScan, scan_id)
            if scan and scan.issues_json:
                try:
                    parsed = json.loads(scan.issues_json)
                    if isinstance(parsed, list):
                        scan_issues = parsed
                except Exception:
                    scan_issues = []
    return render_template("employee/hub_doc_scan.html", clients=clients, selected_client=selected, documents=docs, scan=scan, scan_issues=scan_issues)


@employee_bp.post("/hub/doc-scan")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_doc_scan_post():
    client_id = int(request.form.get("client_id") or 0)
    doc_id = int(request.form.get("document_id") or 0)
    db = SessionLocal()
    client = db.get(Client, client_id)
    doc = db.get(Document, doc_id)
    if not client or client.org_id != current_user.org_id or not doc or doc.client_id != client.id:
        flash("Document not found.", "error")
        return redirect(url_for("employee.hub_doc_scan"))
    score, issues, summary = _scan_document(doc)
    scan = DocumentScan(
        org_id=current_user.org_id,
        client_id=client.id,
        document_id=doc.id,
        summary=summary,
        score=score,
        issues_json=json.dumps(issues, ensure_ascii=True),
        created_by_user_id=current_user.id,
    )
    db.add(scan)
    db.commit()
    flash("Scan complete.", "success")
    return redirect(url_for("employee.hub_doc_scan", client_id=client.id, scan_id=scan.id))


@employee_bp.get("/hub/reminders")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_reminders():
    db = SessionLocal()
    clients = db.query(Client).filter_by(org_id=current_user.org_id).order_by(Client.created_at.desc()).all()
    client_id = int(request.args.get("client_id") or 0)
    selected = db.get(Client, client_id) if client_id else None
    reminders = []
    if selected and selected.org_id == current_user.org_id:
        reminders = db.query(Reminder).filter_by(org_id=current_user.org_id, client_id=selected.id).order_by(Reminder.created_at.desc()).all()
    return render_template("employee/hub_reminders.html", clients=clients, selected_client=selected, reminders=reminders)


@employee_bp.post("/hub/reminders/create")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_reminders_create():
    client_id = int(request.form.get("client_id") or 0)
    subject = (request.form.get("subject") or "").strip()
    body = (request.form.get("body") or "").strip()
    send_at_raw = (request.form.get("send_at") or "").strip()
    send_at = None
    if send_at_raw:
        try:
            send_at = datetime.fromisoformat(send_at_raw)
        except Exception:
            send_at = None
    db = SessionLocal()
    client = db.get(Client, client_id)
    if not client or client.org_id != current_user.org_id:
        flash("Client not found.", "error")
        return redirect(url_for("employee.hub_reminders"))
    row = Reminder(
        org_id=current_user.org_id,
        client_id=client.id,
        subject=subject,
        body=body,
        send_at=send_at,
        created_by_user_id=current_user.id,
    )
    db.add(row)
    db.commit()
    flash("Reminder created.", "success")
    return redirect(url_for("employee.hub_reminders", client_id=client.id))


@employee_bp.post("/hub/reminders/<int:reminder_id>/send")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_reminders_send(reminder_id: int):
    db = SessionLocal()
    reminder = db.get(Reminder, reminder_id)
    if not reminder or reminder.org_id != current_user.org_id:
        flash("Reminder not found.", "error")
        return redirect(url_for("employee.hub_reminders"))
    client = db.get(Client, reminder.client_id)
    if not client or client.org_id != current_user.org_id or not client.email:
        flash("Client email not available.", "error")
        return redirect(url_for("employee.hub_reminders", client_id=reminder.client_id))
    try:
        from app.services.email_service import send_email_html
        subject = reminder.subject or "Reminder"
        body_text = reminder.body or ""
        body_html = "<p>" + (reminder.body or "").replace("\n", "<br>") + "</p>"
        send_email_html(client.email.strip().lower(), subject, body_text, body_html, context="REMINDER", org_id=current_user.org_id, actor_user_id=current_user.id)
        reminder.sent_at = datetime.utcnow()
        reminder.status = "sent"
        db.commit()
        flash("Reminder sent.", "success")
    except Exception as exc:
        flash(f"Reminder send failed: {exc}", "error")
    return redirect(url_for("employee.hub_reminders", client_id=reminder.client_id))


@employee_bp.get("/hub/onboarding")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_onboarding():
    db = SessionLocal()
    items = _load_onboarding_items(db)
    completed = db.query(EmployeeOnboardingProgress).filter_by(employee_id=current_user.id, is_complete=True).all()
    done_ids = {c.item_id for c in completed}
    required_items = [i for i in items if i.is_required]
    progress = int((len([i for i in required_items if i.id in done_ids]) / len(required_items)) * 100) if required_items else 0
    return render_template("employee/hub_onboarding.html", items=items, done_keys=done_ids, progress=progress)


@employee_bp.get("/hub/compliance")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_compliance():
    sections = [
        {
            "title": "Ethical Boundaries",
            "items": [
                "No legal advice or medical determinations. We provide administrative support and education only.",
                "Do not guarantee outcomes, timelines, or percentage ratings.",
                "Never imply VA affiliation or official authority.",
                "Separate verified facts from assumptions in every summary.",
                "If unsure, escalate to the Director before advising the client.",
            ],
        },
        {
            "title": "Fees and Representations",
            "items": [
                "Explain pricing clearly before any work begins.",
                "Confirm whether the fee is flat or percent of backpay.",
                "State that no award means no contingency fee when applicable.",
                "Avoid language like 'we will win' or 'guaranteed approval.'",
                "Document pricing discussion in the client record.",
            ],
        },
        {
            "title": "Evidence Standards (Three Pillars)",
            "items": [
                "Current diagnosis: provider notes, exam, or diagnosis documentation.",
                "In-service event/exposure: STRs, orders, incident reports, buddy statements.",
                "Nexus/continuity: medical opinion or clear timeline linking service to condition.",
                "Track dates, provider names, and document types.",
                "Turn gaps into tasks with clear instructions and deadlines.",
            ],
        },
        {
            "title": "Records Handling",
            "items": [
                "Never alter original records. Preserve the original file.",
                "Keep filenames meaningful and consistent (date_type_source).",
                "Log source and upload date for every document.",
                "If a document is unclear, request a clearer copy and note it.",
                "Do not delete records without approval and audit trail.",
            ],
        },
        {
            "title": "Privacy and Security",
            "items": [
                "Use only approved channels (portal and official email).",
                "Do not share client data outside the system.",
                "Avoid unnecessary PII in messages; redact when possible.",
                "Never store SSNs in notes or messages.",
                "Report suspected breaches immediately to the Director.",
            ],
        },
        {
            "title": "Communication Standards",
            "items": [
                "Use clear, calm, actionable language.",
                "Confirm deadlines and next steps in every message.",
                "Avoid jargon; explain terms in plain English.",
                "Summarize what you reviewed and what is missing.",
                "Keep tone respectful and veteran-first at all times.",
            ],
        },
        {
            "title": "AI Use and Verification",
            "items": [
                "AI output is advisory only and must be verified.",
                "Cross-check AI findings against source documents.",
                "Document what was accepted, edited, or rejected.",
                "Do not paste AI output into client record without review.",
                "Flag hallucinations or unsupported claims immediately.",
            ],
        },
        {
            "title": "Hardship and Urgent Cases",
            "items": [
                "Escalate homelessness, severe hardship, or safety risk same day.",
                "Flag time-sensitive deadlines (appeals, NOD, supplemental).",
                "Notify Director and document the escalation.",
                "Prioritize urgent cases in the task list.",
                "Confirm urgent steps with the client in writing.",
            ],
        },
        {
            "title": "Documentation and Audit Trail",
            "items": [
                "Record uploads, changes, messages, and decisions.",
                "Add notes that explain why actions were taken.",
                "Link each decision to evidence when possible.",
                "Keep logs concise but complete.",
                "Use timestamps and dates in YYYY-MM-DD format.",
            ],
        },
        {
            "title": "Conflicts and Boundaries",
            "items": [
                "Do not accept gifts, favors, or outside payments.",
                "Avoid conflicts of interest with clients or vendors.",
                "Disclose potential conflicts to the Director immediately.",
                "Recuse yourself when conflicts exist.",
                "Keep personal relationships out of case decisions.",
            ],
        },
    ]
    return render_template("employee/hub_compliance.html", sections=sections)


@employee_bp.get("/hub/howto")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_howto():
    guides = [
        {"title": "New Client Setup", "steps": [
            "Create the client record and verify name, email, phone, and address",
            "Confirm service details (branch, dates, discharge type) and dependents",
            "Assign the primary employee and set case status",
            "Enable the portal and send the login invite with guidance",
            "Create initial tasks (DD214, medical records, decision letters)",
            "Add an intake summary note with key conditions and dates",
        ]},
        {"title": "Evidence Workflow", "steps": [
            "Upload documents and normalize filenames (date_type_source.pdf)",
            "Tag each file with the correct category and condition",
            "Run AI scan, then verify extracted text against the document",
            "Identify missing evidence (diagnosis, in-service event, nexus)",
            "Create client tasks with instructions and due dates",
            "Build a checklist and share it in the portal",
        ]},
        {"title": "Messaging Best Practices", "steps": [
            "Acknowledge receipt within 1 business day",
            "Confirm what was received and what is still needed",
            "Use short instructions with clear deadlines",
            "Avoid legal conclusions or guarantees",
            "Summarize the next step at the end of the message",
        ]},
        {"title": "Forms and Letters", "steps": [
            "Open the forms library and confirm the correct VA form",
            "Generate the form or letter and review for accuracy",
            "Save to documents and mark shared when client-facing",
            "Send a portal message with clear instructions and timeline",
        ]},
        {"title": "Portal Support", "steps": [
            "Confirm the portal is enabled and the client can log in",
            "Troubleshoot missing uploads (file type, size, or user error)",
            "Share requested documents and confirm visibility",
            "Use portal messages for all case-related communication",
        ]},
        {"title": "Benefits Finder and Services Locator", "steps": [
            "Use ZIP or city/state to search local resources",
            "Adjust radius and categories to refine results",
            "If no results, use AI seeding and verify before approval",
            "Add verified resources in the Resources Admin panel",
        ]},
    ]
    return render_template("employee/hub_howto.html", guides=guides)


@employee_bp.get("/hub/issues")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_issues():
    issues = [
        {"problem": "Client cannot log in", "cause": "Portal not enabled or password reset required", "fix": "Enable portal and resend invite. Verify email."},
        {"problem": "AI scan returns empty", "cause": "No extracted text yet", "fix": "Wait for extraction or re-upload a clearer document."},
        {"problem": "Missing benefits in finder", "cause": "ZIP/state missing or data incomplete", "fix": "Add ZIP or state and try again."},
        {"problem": "Message not delivered", "cause": "Client email missing or SMTP not configured", "fix": "Add email or configure SMTP in .env."},
        {"problem": "Duplicate documents", "cause": "Uploaded multiple times with different names", "fix": "Archive duplicates and keep the clearest copy."},
    ]
    return render_template("employee/hub_issues.html", issues=issues)


@employee_bp.get("/hub/messages")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_messages():
    return redirect(url_for("messages.inbox"))


@employee_bp.get("/hub/forms")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_forms():
    forms = [
        {"name": "VA Form 21-526EZ", "use": "Initial disability claim", "status": "Manual"},
        {"name": "VA Form 21-4138", "use": "Statement in support of claim", "status": "Supported"},
        {"name": "VA Form 21-4142", "use": "Authorization to release records", "status": "Partial"},
    ]
    return render_template("employee/hub_forms.html", forms=forms)


@employee_bp.get("/hub/settings")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_settings():
    return render_template("employee/hub_settings.html")


@employee_bp.get("/hub/standards")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_standards():
    return render_template("employee/hub_standards.html")


@employee_bp.get("/hub/evidence-guide")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_evidence_guide():
    return render_template("employee/hub_evidence_guide.html")


@employee_bp.get("/hub/ai-guidance")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_ai_guidance():
    return render_template("employee/hub_ai_guidance.html")


@employee_bp.get("/hub/fees-and-agreements")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_fees_agreements():
    return render_template("employee/hub_fees_agreements.html")


@employee_bp.get("/hub/life-events")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_life_events():
    return render_template("employee/hub_life_events.html")


@employee_bp.get("/hub/common-issues")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_common_issues():
    return render_template("employee/hub_common_issues.html")


@employee_bp.get("/hub/escalation")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_escalation():
    return render_template("employee/hub_escalation.html")


@employee_bp.get("/hub/security")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_security():
    return render_template("employee/hub_security.html")


@employee_bp.post("/hub/onboarding/complete")
@csrf.exempt
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def onboarding_complete():
    db = SessionLocal()
    payload = request.get_json(silent=True) or {}
    item_id = int(payload.get("item_id") or 0)
    is_complete = bool(payload.get("is_complete", True))
    item = db.get(OnboardingItem, item_id)
    if not item or not item.is_active:
        return {"error": "Invalid item"}, 400
    row = db.query(EmployeeOnboardingProgress).filter_by(employee_id=current_user.id, item_id=item_id).first()
    if not row:
        row = EmployeeOnboardingProgress(employee_id=current_user.id, item_id=item_id)
        db.add(row)
    row.is_complete = is_complete
    row.completed_at = datetime.utcnow() if is_complete else None
    db.commit()

    items = db.query(OnboardingItem).filter_by(is_active=True).order_by(OnboardingItem.sort_order.asc()).all()
    required_items = [i for i in items if i.is_required]
    completed = db.query(EmployeeOnboardingProgress).filter_by(employee_id=current_user.id, is_complete=True).all()
    done_ids = {c.item_id for c in completed}
    progress = int((len([i for i in required_items if i.id in done_ids]) / len(required_items)) * 100) if required_items else 0
    return {
        "ok": True,
        "progress": progress,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


@employee_bp.post("/hub/onboarding/reset/<int:item_id>")
@login_required
@require_roles(Role.DIRECTOR)
def onboarding_reset(item_id: int):
    db = SessionLocal()
    row = db.query(EmployeeOnboardingProgress).filter_by(employee_id=current_user.id, item_id=item_id).first()
    if row:
        row.is_complete = False
        row.completed_at = None
        db.commit()
    flash("Onboarding item reset.", "success")
    return redirect(url_for("employee.hub_onboarding"))


@employee_bp.post("/notes")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def update_notes():
    db = SessionLocal()
    user = db.get(User, current_user.id)
    if not user or user.org_id != current_user.org_id:
        return "Not found", 404
    user.notes = (request.form.get("notes") or "").strip()
    db.commit()
    audit_log(current_user.org_id, current_user.id, "EMPLOYEE_NOTES_UPDATED", "User", user.id)
    flash("Notes updated.", "success")
    return redirect(url_for("employee.dashboard"))


@employee_bp.post("/guidance")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def guidance():
    question = (request.form.get("question") or "").strip()
    if not question:
        flash("Enter a question for AI guidance.", "error")
        return redirect(url_for("employee.dashboard"))

    api_key = current_app.config.get("OPENAI_API_KEY") or current_app.config.get("OPENAI_KEY")
    if not api_key:
        flash("OPENAI_API_KEY is missing in .env", "error")
        return redirect(url_for("employee.dashboard"))

    guidance_text = ""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        model = current_app.config.get("OPENAI_MODEL", "gpt-4.1-mini")
        prompt = (
            "You are a veteran claims support assistant. Provide practical, safe guidance "
            "for the next steps in a client's claim. Do not guarantee outcomes or provide legal advice.\n\n"
            f"Question: {question}"
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Provide concise, action-oriented guidance with bullet points."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        guidance_text = (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        flash(f"AI guidance failed: {exc}", "error")
        return redirect(url_for("employee.dashboard"))

    db = SessionLocal()
    assigned = db.query(Client).filter(
        Client.org_id == current_user.org_id,
        Client.assigned_user_id == current_user.id,
        Client.is_archived == False,  # noqa: E712
    ).order_by(Client.created_at.desc()).all()
    return render_template("employee/dashboard.html", assigned=assigned, guidance=guidance_text, question=question)
