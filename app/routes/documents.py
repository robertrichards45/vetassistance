from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, jsonify
import mimetypes
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models import Client, Document, User
from app.services.evidence_classifier import classify
from app.services.storage import save_upload
from app.services.queue import get_queue
from app.services.jobs import extract_document_text
from app.services.doc_text import extract_text
from app.services.audit_service import log as audit_log
import os
import json

DOCUMENT_CATEGORIES = [
    "Decision Letter",
    "C&P Exam",
    "DBQ",
    "Treatment Records",
    "Service Records (STR)",
    "Buddy Statement",
    "Employer Statement",
    "Imaging/Labs",
    "Other",
    "Uncategorized",
]


def _document_text(doc: Document, limit: int = 12000) -> str:
    if not doc.extracted_text_path or not os.path.exists(doc.extracted_text_path):
        return ""
    try:
        with open(doc.extracted_text_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(limit)
    except Exception:
        return ""


def _ai_categories(docs: list[Document]) -> dict[int, str]:
    from flask import current_app
    from openai import OpenAI

    api_key = current_app.config.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    model = current_app.config.get("OPENAI_MODEL", "gpt-4.1-mini")
    payload = [
        {
            "id": doc.id,
            "filename": doc.filename,
            "mime_type": doc.mime_type or "unknown",
            "text": _document_text(doc, limit=4000),
        }
        for doc in docs
    ]
    allowed = ", ".join(DOCUMENT_CATEGORIES)
    prompt = f"""Categorize each VA claim document into exactly one allowed category.
Allowed categories: {allowed}
Return a JSON object whose keys are the document IDs and whose values are category names.
Include every supplied document ID and no other keys.

Documents:
{json.dumps(payload, ensure_ascii=False)}
"""
    response = OpenAI(api_key=api_key).chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "You classify VA claim documents. Return exactly one allowed category."},
            {"role": "user", "content": prompt},
        ],
    )
    raw = json.loads(response.choices[0].message.content or "{}")
    results = {}
    for doc in docs:
        category = str(raw.get(str(doc.id), raw.get(doc.id, "Other"))).strip()
        results[doc.id] = category if category in DOCUMENT_CATEGORIES else "Other"
    return results

documents_bp = Blueprint("documents", __name__, url_prefix="/documents")

@documents_bp.get("/client/<int:client_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def client_documents(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    q = (request.args.get("q") or "").strip()
    docs_q = db.query(Document).filter_by(org_id=current_user.org_id, client_id=c.id)
    if q:
        docs_q = docs_q.filter(Document.filename.ilike(f"%{q}%"))
    docs = docs_q.order_by(Document.created_at.desc()).all()
    return render_template("documents/client_documents.html", client=c, docs=docs, q=q)

@documents_bp.get("/prefs/doc-table/<int:client_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def get_doc_table_prefs(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return jsonify({"error": "Not found"}), 404
    u = db.get(User, current_user.id)
    try:
        prefs = json.loads(u.preferences_json or "{}")
    except Exception:
        prefs = {}
    doc_prefs = (prefs.get("doc_table") or {}).get(str(client_id), {})
    return jsonify({"prefs": doc_prefs})

@documents_bp.post("/prefs/doc-table/<int:client_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def set_doc_table_prefs(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return jsonify({"error": "Not found"}), 404
    payload = request.get_json(silent=True) or {}
    allowed = {k: payload.get(k, "") for k in ["sort", "name", "category", "status", "share"]}
    u = db.get(User, current_user.id)
    try:
        prefs = json.loads(u.preferences_json or "{}")
    except Exception:
        prefs = {}
    doc_prefs = prefs.get("doc_table") or {}
    doc_prefs[str(client_id)] = allowed
    prefs["doc_table"] = doc_prefs
    u.preferences_json = json.dumps(prefs)
    db.commit()
    return jsonify({"ok": True})

def _delete_doc_files(doc: Document) -> None:
    try:
        if doc.storage_path and os.path.exists(doc.storage_path):
            os.remove(doc.storage_path)
    except Exception:
        pass
    try:
        if doc.extracted_text_path and os.path.exists(doc.extracted_text_path):
            os.remove(doc.extracted_text_path)
    except Exception:
        pass


@documents_bp.post("/client/<int:client_id>/reclassify")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def reclassify_client_docs(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    docs = db.query(Document).filter_by(org_id=current_user.org_id, client_id=c.id).all()
    updated = 0
    queued = 0
    extracted = 0
    for d in docs:
        text = ""
        if d.extracted_text_path and os.path.exists(d.extracted_text_path):
            try:
                with open(d.extracted_text_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read(12000)
            except Exception:
                text = ""
        if not text and d.storage_path and d.storage_path.lower().endswith(".pdf") and os.path.exists(d.storage_path):
            try:
                text = extract_text(d.storage_path)
                if text.strip():
                    base = d.storage_path + ".txt"
                    with open(base, "w", encoding="utf-8") as f:
                        f.write(text)
                    d.extracted_text_path = base
                    extracted += 1
            except Exception:
                text = ""
        new_cat = classify(d.filename, d.mime_type or "", text)
        if new_cat and (new_cat != d.category):
            d.category = new_cat
            updated += 1
        if not text:
            try:
                get_queue("docs").enqueue(extract_document_text, d.id)
                queued += 1
            except Exception:
                pass
    if updated:
        db.commit()
    audit_log(current_user.org_id, current_user.id, "DOCS_RECLASSIFIED", "Document", "bulk", detail=f"client_id={c.id}, updated={updated}")
    if queued or extracted:
        flash(f"Reclassified documents. Updated {updated} file(s). Extracted {extracted} now, queued {queued}.", "success")
    else:
        flash(f"Reclassified documents. Updated {updated} file(s).", "success")
    return redirect(url_for("documents.client_documents", client_id=c.id))


@documents_bp.post("/client/<int:client_id>/categorize-ai")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def categorize_client_docs_ai(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    if not current_app().config.get("OPENAI_API_KEY"):
        flash("AI categorization is unavailable because OPENAI_API_KEY is not configured.", "error")
        return redirect(url_for("documents.client_documents", client_id=c.id))

    docs = db.query(Document).filter_by(org_id=current_user.org_id, client_id=c.id).all()
    updated = 0
    failed = 0
    batch_size = 8
    for start in range(0, len(docs), batch_size):
        batch = docs[start:start + batch_size]
        try:
            categories = _ai_categories(batch)
            for d in batch:
                category = categories[d.id]
                if d.category != category:
                    d.category = category
                    updated += 1
        except Exception:
            failed += len(batch)
    db.commit()
    audit_log(
        current_user.org_id,
        current_user.id,
        "DOCS_AI_CATEGORIZED",
        "Document",
        "bulk",
        detail=f"client_id={c.id}, total={len(docs)}, updated={updated}, failed={failed}",
    )
    if failed:
        flash(f"AI categorized {len(docs) - failed} document(s); {failed} could not be categorized.", "warning")
    else:
        flash(f"AI categorized all {len(docs)} document(s).", "success")
    return redirect(url_for("documents.client_documents", client_id=c.id))


@documents_bp.post("/client/<int:client_id>/share-all")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def share_all_documents(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    docs = db.query(Document).filter_by(org_id=current_user.org_id, client_id=c.id).all()
    changed = 0
    for d in docs:
        if not d.is_client_visible:
            d.is_client_visible = True
            changed += 1
    db.commit()
    audit_log(
        current_user.org_id,
        current_user.id,
        "DOCS_SHARED_ALL",
        "Document",
        "bulk",
        detail=f"client_id={c.id}, total={len(docs)}, changed={changed}",
    )
    flash(f"Shared all {len(docs)} document(s) with the client.", "success")
    return redirect(url_for("documents.client_documents", client_id=c.id))

@documents_bp.post("/client/<int:client_id>/upload")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def upload_staff(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    files = request.files.getlist("files") or ([] if "file" not in request.files else [request.files.get("file")])
    files = [f for f in files if f and (f.filename or "").strip()]
    if not files:
        flash("Choose file(s) to upload.", "error")
        return redirect(url_for("documents.client_documents", client_id=c.id))
    count = 0
    for f in files:
        full_path, saved_name = save_upload(current_app().config["STORAGE_ROOT"], current_user.org_id, c.id, f)
        doc = Document(org_id=current_user.org_id, client_id=c.id, filename=saved_name, mime_type=f.mimetype or "", storage_path=full_path, uploaded_by_user_id=current_user.id, category=classify(saved_name, f.mimetype or ''))
        db.add(doc); db.commit()
        try:
            get_queue("docs").enqueue(extract_document_text, doc.id)
        except Exception:
            pass
        audit_log(current_user.org_id, current_user.id, "STAFF_UPLOAD", "Document", doc.id, detail=saved_name)
        count += 1
    flash(f"Uploaded {count} file(s).", "success")
    return redirect(url_for("documents.client_documents", client_id=c.id))

def current_app():
    from flask import current_app as ca
    return ca

@documents_bp.get("/download/<int:doc_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def download(doc_id: int):
    db = SessionLocal()
    d = db.get(Document, doc_id)
    if not d or d.org_id != current_user.org_id:
        return "Not found", 404
    if not os.path.exists(d.storage_path):
        return "Missing file", 404
    return send_file(d.storage_path, as_attachment=True, download_name=d.filename)


@documents_bp.get("/view/<int:doc_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def view_document(doc_id: int):
    db = SessionLocal()
    d = db.get(Document, doc_id)
    if not d or d.org_id != current_user.org_id:
        return "Not found", 404
    if not os.path.exists(d.storage_path):
        return "Missing file", 404
    mime = d.mime_type or (mimetypes.guess_type(d.storage_path)[0] or "application/octet-stream")
    return send_file(d.storage_path, as_attachment=False, download_name=d.filename, mimetype=mime)


@documents_bp.post("/update/<int:doc_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def update_category(doc_id: int):
    db = SessionLocal()
    d = db.get(Document, doc_id)
    if not d or d.org_id != current_user.org_id:
        return "Not found", 404
    cat = (request.form.get("category") or "").strip()
    if cat:
        d.category = cat
        db.commit()
        audit_log(current_user.org_id, current_user.id, "DOC_CATEGORY_UPDATED", "Document", d.id, detail=cat)
    return redirect(url_for("documents.client_documents", client_id=d.client_id))

@documents_bp.post("/delete/<int:doc_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def delete_document(doc_id: int):
    db = SessionLocal()
    d = db.get(Document, doc_id)
    if not d or d.org_id != current_user.org_id:
        return "Not found", 404
    client_id = d.client_id
    _delete_doc_files(d)
    db.delete(d)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "DOC_DELETED", "Document", doc_id, detail=f"client_id={client_id}")
    flash("Document deleted.", "success")
    return redirect(url_for("documents.client_documents", client_id=client_id))

@documents_bp.post("/client/<int:client_id>/delete-bulk")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def delete_documents_bulk(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    ids = request.form.getlist("doc_ids")
    ids = [int(i) for i in ids if i.isdigit()]
    if not ids:
        flash("Select at least one document to delete.", "error")
        return redirect(url_for("documents.client_documents", client_id=c.id))
    docs = db.query(Document).filter(Document.org_id == current_user.org_id, Document.client_id == c.id, Document.id.in_(ids)).all()
    for d in docs:
        _delete_doc_files(d)
        db.delete(d)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "DOCS_DELETED_BULK", "Document", "bulk", detail=f"client_id={c.id}, count={len(docs)}")
    flash(f"Deleted {len(docs)} document(s).", "success")
    return redirect(url_for("documents.client_documents", client_id=c.id))


@documents_bp.post("/client/<int:client_id>/preview")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def preview_documents(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    ids = request.form.getlist("doc_ids")
    ids = [int(i) for i in ids if i.isdigit()]
    if not ids:
        flash("Select at least one document to preview.", "error")
        return redirect(url_for("documents.client_documents", client_id=c.id))
    docs = db.query(Document).filter(Document.org_id == current_user.org_id, Document.client_id == c.id, Document.id.in_(ids)).all()
    docs = sorted(docs, key=lambda d: d.created_at or 0, reverse=True)
    return render_template("documents/preview_documents.html", client=c, docs=docs)


@documents_bp.post("/share/<int:doc_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def toggle_share(doc_id: int):
    db = SessionLocal()
    d = db.get(Document, doc_id)
    if not d or d.org_id != current_user.org_id:
        return "Not found", 404
    d.is_client_visible = not bool(d.is_client_visible)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "DOC_SHARE_TOGGLED", "Document", d.id, detail=str(d.is_client_visible))
    return redirect(url_for("documents.client_documents", client_id=d.client_id))
