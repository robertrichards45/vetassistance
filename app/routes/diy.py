from __future__ import annotations

from datetime import datetime
import os
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user, login_user

from app.extensions import SessionLocal, csrf
from app.models import Organization, User, DIYAccount, DIYSubscription, DIYChecklistItem, DIYClaimUpdate, DIYDocument, DIYDocumentScan, Client
from app.models.user import Role
from app.services.diy_access import has_diy_access, is_within_refund_window
from app.services.va_forms import get_va_forms
from app.services.doc_text import extract_text
from app.services.ai_engine import answer_doc_question
from werkzeug.utils import secure_filename
import stripe


diy_bp = Blueprint("diy", __name__, url_prefix="/diy")


def _stripe():
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    return stripe


def _get_default_org(db):
    default_name = current_app.config.get("DEFAULT_TENANT", "Veteran Benefits Assistance")
    org = db.query(Organization).filter(Organization.name == default_name).first()
    if not org:
        org = db.query(Organization).first()
    return org


def _get_diy_account(db):
    return db.query(DIYAccount).filter(DIYAccount.user_id == current_user.id).first()


def _require_diy_login():
    if not current_user.is_authenticated or current_user.role != Role.DIY:
        return False
    return True

def _get_diy_plan(db, acct: DIYAccount | None) -> str:
    if not acct:
        return "basic"
    plan = "basic"
    sub = db.query(DIYSubscription).filter_by(diy_id=acct.id).order_by(DIYSubscription.created_at.desc()).first()
    if sub and sub.plan:
        plan = sub.plan
    return plan

def _build_tools_context(db, acct: DIYAccount, scan_id: int = 0) -> dict:
    checklist = db.query(DIYChecklistItem).filter_by(diy_id=acct.id).order_by(DIYChecklistItem.created_at.desc()).all()
    updates = db.query(DIYClaimUpdate).filter_by(diy_id=acct.id).order_by(DIYClaimUpdate.created_at.desc()).all()
    docs = db.query(DIYDocument).filter_by(diy_id=acct.id).order_by(DIYDocument.created_at.desc()).all()
    scan = db.query(DIYDocumentScan).filter_by(id=scan_id, diy_id=acct.id).first() if scan_id else None
    scan_issues = []
    if scan and scan.issues_json:
        try:
            parsed = json.loads(scan.issues_json)
            if isinstance(parsed, list):
                scan_issues = parsed
        except Exception:
            scan_issues = []
    plan = _get_diy_plan(db, acct)
    forms = get_va_forms()
    return {
        "checklist": checklist,
        "updates": updates,
        "documents": docs,
        "scan": scan,
        "scan_issues": scan_issues,
        "plan": plan,
        "forms": forms,
    }


def _scan_document(doc: DIYDocument) -> tuple[int, list[dict], str]:
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


@diy_bp.get("")
def landing():
    return render_template("public/diy.html", meta_title="DIY VA Claim Tools", meta_description="Do-it-yourself VA claim tools: rating chart, calculator, checklist, and document scan.")


def _render_register(error: str | None = None, info: str | None = None, email: str = "", full_name: str = ""):
    return render_template(
        "public/diy_register.html",
        error=error,
        info=info,
        form_email=email,
        form_full_name=full_name,
        meta_title="DIY Registration",
        meta_description="Create a DIY account to access VA rating tools, checklists, and document scans.",
    )


@diy_bp.get("/register")
def register():
    return _render_register()


@diy_bp.post("/register")
@csrf.exempt
def register_post():
    email = (request.form.get("email") or "").strip().lower()
    full_name = (request.form.get("full_name") or "").strip()
    password = request.form.get("password") or ""
    confirm = request.form.get("confirm_password") or ""
    if not email or not password or not full_name:
        return _render_register("Please complete all fields.", email=email, full_name=full_name)
    if password != confirm:
        return _render_register("Passwords do not match.", email=email, full_name=full_name)
    if len(password) < 8:
        return _render_register("Password must be at least 8 characters.", email=email, full_name=full_name)

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            return _render_register("Account already exists. Please log in.", email=email, full_name=full_name)
        org = _get_default_org(db)
        if not org:
            return _render_register("Organization not found. Contact support.", email=email, full_name=full_name)
        user = User(org_id=org.id, email=email, full_name=full_name, role=Role.DIY)
        user.set_password(password)
        db.add(user)
        db.commit()
        acct = DIYAccount(org_id=org.id, user_id=user.id, full_name=full_name, status="active")
        db.add(acct)
        db.commit()
        login_user(user)
        user.last_login_at = datetime.utcnow()
        db.commit()
        return redirect(url_for("diy.subscribe"))
    finally:
        db.close()


@diy_bp.get("/login")
def login():
    return render_template("public/diy_login.html", meta_title="DIY Login", meta_description="Log in to your DIY VA claim tools.")


@diy_bp.post("/login")
@csrf.exempt
def login_post():
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    if not email or not password:
        flash("Please enter your email and password.", "error")
        return redirect(url_for("diy.login"))
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
    finally:
        db.close()
    if not user or not user.check_password(password) or user.role != Role.DIY:
        flash("Invalid credentials.", "error")
        return redirect(url_for("diy.login"))
    login_user(user)
    db = SessionLocal()
    try:
        u = db.get(User, user.id)
        if u:
            u.last_login_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()
    return redirect(url_for("diy.tools"))


@diy_bp.get("/subscribe")
@login_required
def subscribe():
    if current_user.role != Role.DIY:
        return redirect(url_for("public.home"))
    return render_template("public/diy_subscribe.html")


@diy_bp.post("/checkout")
@login_required
def checkout():
    if current_user.role != Role.DIY:
        return redirect(url_for("public.home"))
    plan = (request.form.get("plan") or "basic").strip().lower()
    price_id = os.environ.get("STRIPE_DIY_BASIC_PRICE_ID", "").strip()
    if plan == "pro":
        price_id = os.environ.get("STRIPE_DIY_PRO_PRICE_ID", "").strip()
    s = _stripe()
    if not s.api_key or not price_id:
        flash("Stripe not configured.", "error")
        return redirect(url_for("diy.subscribe"))
    db = SessionLocal()
    try:
        acct = _get_diy_account(db)
        if acct:
            row = db.query(DIYSubscription).filter_by(diy_id=acct.id).order_by(DIYSubscription.created_at.desc()).first()
            if not row:
                row = DIYSubscription(org_id=acct.org_id, diy_id=acct.id)
                db.add(row)
                db.commit()
            row.plan = plan
            row.status = "pending"
            db.commit()
    finally:
        db.close()
    success_url = request.url_root.rstrip("/") + url_for("diy.tools") + "?subscribed=1"
    cancel_url = request.url_root.rstrip("/") + url_for("diy.subscribe") + "?canceled=1"
    session = s.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        customer_email=current_user.email,
        metadata={"source": "diy", "plan": plan, "user_id": str(current_user.id)},
    )
    return redirect(session.url, code=303)


@diy_bp.get("/tools")
@login_required
def tools():
    if current_user.role != Role.DIY:
        return redirect(url_for("public.home"))
    db = SessionLocal()
    try:
        acct = _get_diy_account(db)
        if not acct:
            flash("DIY account not found.", "error")
            return redirect(url_for("diy.subscribe"))
        if not has_diy_access(current_user.id):
            return render_template("public/diy_paywall.html")
        scan_id = int(request.args.get("scan_id") or 0)
        context = _build_tools_context(db, acct, scan_id)
        return render_template("public/diy_tools.html", **context)
    finally:
        db.close()


@diy_bp.post("/checklist/add")
@login_required
def checklist_add():
    if current_user.role != Role.DIY:
        return redirect(url_for("public.home"))
    label = (request.form.get("label") or "").strip()
    category = (request.form.get("category") or "Evidence").strip()
    if not label:
        flash("Enter an item.", "error")
        return redirect(url_for("diy.tools"))
    db = SessionLocal()
    try:
        acct = _get_diy_account(db)
        if not acct:
            return redirect(url_for("diy.tools"))
        db.add(DIYChecklistItem(org_id=acct.org_id, diy_id=acct.id, label=label, category=category))
        db.commit()
        return redirect(url_for("diy.tools"))
    finally:
        db.close()


@diy_bp.post("/checklist/<int:item_id>/toggle")
@login_required
def checklist_toggle(item_id: int):
    if current_user.role != Role.DIY:
        return redirect(url_for("public.home"))
    db = SessionLocal()
    try:
        acct = _get_diy_account(db)
        item = db.get(DIYChecklistItem, item_id)
        if not acct or not item or item.diy_id != acct.id:
            return redirect(url_for("diy.tools"))
        item.is_done = not item.is_done
        db.commit()
        return redirect(url_for("diy.tools"))
    finally:
        db.close()


@diy_bp.post("/updates/add")
@login_required
def updates_add():
    if current_user.role != Role.DIY:
        return redirect(url_for("public.home"))
    note = (request.form.get("note") or "").strip()
    status_label = (request.form.get("status_label") or "Update").strip()
    if not note:
        flash("Enter a short update.", "error")
        return redirect(url_for("diy.tools"))
    db = SessionLocal()
    try:
        acct = _get_diy_account(db)
        if not acct:
            return redirect(url_for("diy.tools"))
        db.add(DIYClaimUpdate(org_id=acct.org_id, diy_id=acct.id, status_label=status_label, note=note))
        db.commit()
        return redirect(url_for("diy.tools"))
    finally:
        db.close()


@diy_bp.post("/documents/upload")
@login_required
def documents_upload():
    if current_user.role != Role.DIY:
        return redirect(url_for("public.home"))
    db = SessionLocal()
    try:
        acct = _get_diy_account(db)
        if not acct:
            return redirect(url_for("diy.tools"))
        files = request.files.getlist("files") or ([] if "file" not in request.files else [request.files.get("file")])
        files = [f for f in files if f and (f.filename or "").strip()]
        if not files:
            flash("Choose file(s) to upload.", "error")
            return redirect(url_for("diy.tools"))
        root = current_app.config.get("STORAGE_ROOT") or os.path.join(current_app.root_path, "..", "storage")
        base_dir = os.path.join(root, "diy", str(acct.id))
        os.makedirs(base_dir, exist_ok=True)
        for f in files:
            name = secure_filename(f.filename)
            path = os.path.join(base_dir, name)
            f.save(path)
            size = os.path.getsize(path)
            db.add(DIYDocument(
                org_id=acct.org_id,
                diy_id=acct.id,
                filename=name,
                mime_type=f.mimetype or "",
                storage_path=path,
                size_bytes=size,
            ))
        db.commit()
        flash("Uploaded successfully.", "success")
        return redirect(url_for("diy.tools"))
    finally:
        db.close()


@diy_bp.post("/documents/<int:doc_id>/scan")
@login_required
def documents_scan(doc_id: int):
    if current_user.role != Role.DIY:
        return redirect(url_for("public.home"))
    db = SessionLocal()
    try:
        acct = _get_diy_account(db)
        doc = db.get(DIYDocument, doc_id)
        if not acct or not doc or doc.diy_id != acct.id:
            return redirect(url_for("diy.tools"))
        score, issues, summary = _scan_document(doc)
        scan = DIYDocumentScan(
            org_id=acct.org_id,
            diy_id=acct.id,
            diy_document_id=doc.id,
            summary=summary,
            score=score,
            issues_json=json.dumps(issues, ensure_ascii=True),
        )
        db.add(scan)
        db.commit()
        return redirect(url_for("diy.tools", scan_id=scan.id))
    finally:
        db.close()


@diy_bp.post("/documents/ask")
@login_required
def documents_ask():
    if current_user.role != Role.DIY:
        return redirect(url_for("public.home"))
    db = SessionLocal()
    try:
        acct = _get_diy_account(db)
        if not acct:
            return redirect(url_for("diy.tools"))
        if not has_diy_access(current_user.id):
            return render_template("public/diy_paywall.html")
        if _get_diy_plan(db, acct) != "pro":
            flash("AI document Q&A is available on Pro.", "error")
            return redirect(url_for("diy.subscribe"))

        question = (request.form.get("question") or "").strip()
        doc_id_raw = (request.form.get("qa_doc_id") or "").strip()
        try:
            doc_id = int(doc_id_raw)
        except Exception:
            doc_id = 0

        if not question:
            context = _build_tools_context(db, acct)
            context.update({
                "qa_error": "Enter a question.",
                "qa_question": "",
                "qa_doc_id": doc_id_raw,
                "qa_answer": "",
            })
            return render_template("public/diy_tools.html", **context)

        doc = db.query(DIYDocument).filter_by(id=doc_id, diy_id=acct.id).first() if doc_id else None
        if not doc:
            context = _build_tools_context(db, acct)
            context.update({
                "qa_error": "Select a document first.",
                "qa_question": question,
                "qa_doc_id": doc_id_raw,
                "qa_answer": "",
            })
            return render_template("public/diy_tools.html", **context)

        doc_text = ""
        try:
            if doc.storage_path and os.path.exists(doc.storage_path):
                doc_text = extract_text(doc.storage_path)[:20000]
        except Exception:
            doc_text = ""
        if not (doc_text or "").strip():
            context = _build_tools_context(db, acct)
            context.update({
                "qa_error": "No readable text found in this document. Run OCR or upload a clearer file.",
                "qa_question": question,
                "qa_doc_id": str(doc.id),
                "qa_answer": "",
            })
            return render_template("public/diy_tools.html", **context)

        extra_context = json.dumps(
            {
                "diy_user": {
                    "email": current_user.email or "",
                    "full_name": acct.full_name or current_user.full_name or "",
                },
                "document": {
                    "id": doc.id,
                    "filename": doc.filename,
                },
            },
            ensure_ascii=True,
        )
        answer = answer_doc_question(doc_text, question, "", extra_context)
        context = _build_tools_context(db, acct)
        context.update({
            "qa_error": "",
            "qa_question": question,
            "qa_doc_id": str(doc.id),
            "qa_answer": answer,
        })
        return render_template("public/diy_tools.html", **context)
    finally:
        db.close()


@diy_bp.get("/billing")
@login_required
def billing():
    if current_user.role != Role.DIY:
        return redirect(url_for("public.home"))
    db = SessionLocal()
    try:
        acct = _get_diy_account(db)
        sub = db.query(DIYSubscription).filter_by(diy_id=acct.id).order_by(DIYSubscription.created_at.desc()).first() if acct else None
        return render_template("public/diy_billing.html", subscription=sub)
    finally:
        db.close()


@diy_bp.post("/portal")
@login_required
def portal():
    if current_user.role != Role.DIY:
        return redirect(url_for("public.home"))
    s = _stripe()
    if not s.api_key:
        flash("Stripe not configured.", "error")
        return redirect(url_for("diy.billing"))
    db = SessionLocal()
    try:
        acct = _get_diy_account(db)
        sub = db.query(DIYSubscription).filter_by(diy_id=acct.id).order_by(DIYSubscription.created_at.desc()).first() if acct else None
        if not sub or not sub.stripe_customer_id:
            flash("Stripe customer not found.", "error")
            return redirect(url_for("diy.billing"))
        portal = s.billing_portal.Session.create(
            customer=sub.stripe_customer_id,
            return_url=request.url_root.rstrip("/") + url_for("diy.billing"),
        )
        return redirect(portal.url, code=303)
    finally:
        db.close()


@diy_bp.post("/cancel/refund")
@login_required
def cancel_refund():
    if current_user.role != Role.DIY:
        return redirect(url_for("public.home"))
    s = _stripe()
    if not s.api_key:
        flash("Stripe not configured.", "error")
        return redirect(url_for("diy.billing"))
    db = SessionLocal()
    try:
        acct = _get_diy_account(db)
        if not acct:
            return redirect(url_for("diy.billing"))
        sub = db.query(DIYSubscription).filter_by(diy_id=acct.id).order_by(DIYSubscription.created_at.desc()).first()
        if not sub or not sub.stripe_subscription_id:
            flash("Subscription not found.", "error")
            return redirect(url_for("diy.billing"))
        if not is_within_refund_window(sub.started_at):
            flash("Refund window has expired.", "error")
            return redirect(url_for("diy.billing"))
        doc_count = db.query(DIYDocument).filter_by(diy_id=acct.id).count()
        refund_pct = 100 if doc_count == 0 else 50
        stripe_sub = s.Subscription.retrieve(sub.stripe_subscription_id)
        latest_invoice = stripe_sub.get("latest_invoice")
        if latest_invoice:
            inv = s.Invoice.retrieve(latest_invoice)
            payment_intent = inv.get("payment_intent")
            amount_paid = int(inv.get("amount_paid") or 0)
            refund_amount = int(amount_paid * (refund_pct / 100))
            if payment_intent and refund_amount > 0:
                s.Refund.create(payment_intent=payment_intent, amount=refund_amount)
                sub.refund_status = "refunded"
                sub.refund_amount_cents = refund_amount
                sub.refund_at = datetime.utcnow()
        s.Subscription.delete(sub.stripe_subscription_id)
        sub.status = "canceled"
        sub.canceled_at = datetime.utcnow()
        db.commit()
        # Delete DIY documents and related scans on refund
        docs = db.query(DIYDocument).filter_by(diy_id=acct.id).all()
        for d in docs:
            try:
                if d.storage_path and os.path.exists(d.storage_path):
                    os.remove(d.storage_path)
            except Exception:
                pass
            db.delete(d)
        db.query(DIYDocumentScan).filter_by(diy_id=acct.id).delete(synchronize_session=False)
        db.commit()
        flash(f"Canceled with {refund_pct}% refund and documents deleted.", "success")
        return redirect(url_for("diy.landing"))
    finally:
        db.close()


@diy_bp.post("/cancel/end")
@login_required
def cancel_end():
    if current_user.role != Role.DIY:
        return redirect(url_for("public.home"))
    s = _stripe()
    if not s.api_key:
        flash("Stripe not configured.", "error")
        return redirect(url_for("diy.billing"))
    db = SessionLocal()
    try:
        acct = _get_diy_account(db)
        sub = db.query(DIYSubscription).filter_by(diy_id=acct.id).order_by(DIYSubscription.created_at.desc()).first() if acct else None
        if not sub or not sub.stripe_subscription_id:
            flash("Subscription not found.", "error")
            return redirect(url_for("diy.billing"))
        s.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=True)
        sub.status = "canceling"
        db.commit()
        flash("Cancellation scheduled for period end. Access remains until the end of your billing cycle.", "success")
        return redirect(url_for("diy.billing"))
    finally:
        db.close()


@diy_bp.post("/become-client")
@login_required
def become_client():
    if current_user.role != Role.DIY:
        return redirect(url_for("public.home"))
    db = SessionLocal()
    try:
        acct = _get_diy_account(db)
        if not acct:
            return redirect(url_for("diy.tools"))
        existing = db.query(Client).filter(Client.portal_user_id == current_user.id).first()
        if existing:
            flash("Client profile already exists.", "info")
            return redirect(url_for("portal.dashboard"))
        user = db.get(User, current_user.id)
        name = acct.full_name or current_user.full_name or ""
        parts = [p for p in name.split(" ") if p]
        first = parts[0] if parts else ""
        last = " ".join(parts[1:]) if len(parts) > 1 else ""
        client = Client(
            org_id=acct.org_id,
            email=current_user.email,
            first_name=first,
            last_name=last,
            portal_user_id=current_user.id,
            portal_enabled=True,
            account_type="verified",
            email_verified=True,
            email_verified_at=datetime.utcnow(),
        )
        db.add(client)
        if user:
            user.role = Role.CLIENT
        db.commit()
        flash("Converted to client account.", "success")
        return redirect(url_for("portal.dashboard"))
    finally:
        db.close()
