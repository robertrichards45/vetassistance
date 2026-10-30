from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models import User, AuditLog, Template, OnboardingItem, EmployeeOnboardingProgress, PageView, FAQItem, DIYAccount, DIYSubscription, Client, PublicComment
from app.services.audit_service import log as audit_log
from app.services.article_store import list_articles, load_article, save_article, delete_article
import os
from app.services.template_engine import DEFAULT_TEMPLATES
import subprocess
from datetime import datetime, timedelta
from sqlalchemy import func, distinct, or_

director_bp = Blueprint("director", __name__, url_prefix="/director")


@director_bp.get("")
@login_required
@require_roles(Role.DIRECTOR)
def dashboard():
    db = SessionLocal()
    users = (
        db.query(User)
        .filter(User.org_id == current_user.org_id, User.role != Role.CLIENT)
        .order_by(User.created_at.desc())
        .all()
    )
    audits = db.query(AuditLog).filter(AuditLog.org_id == current_user.org_id).order_by(AuditLog.created_at.desc()).limit(25).all()

    from datetime import datetime, timedelta
    from app.models import ClientTask, Message, Document
    from app.models.claim_update import ClaimUpdate
    from app.services.case_stages import stage_key
    now = datetime.utcnow()
    since = now - timedelta(days=30)
    org = current_user.org_id
    clients = db.query(Client).filter(Client.org_id == org, Client.is_archived == False).all()  # noqa: E712
    open_clients = [c for c in clients if stage_key(c.status) != "closed"]
    overdue_rows = (db.query(ClientTask.client_id).filter(ClientTask.org_id == org, ClientTask.is_completed == False,  # noqa: E712
                    ClientTask.due_date.isnot(None), ClientTask.due_date < now).all())
    overdue_by_client = {}
    for (cid,) in overdue_rows:
        overdue_by_client[cid] = overdue_by_client.get(cid, 0) + 1
    staff = [u for u in users if u.role in (Role.DIRECTOR, Role.EMPLOYEE)]
    workload = []
    for u in staff:
        mine = [c for c in open_clients if c.assigned_user_id == u.id]
        workload.append({"user": u, "open": len(mine), "overdue": sum(overdue_by_client.get(c.id, 0) for c in mine)})
    workload.sort(key=lambda r: r["open"], reverse=True)
    unassigned = sum(1 for c in open_clients if not c.assigned_user_id)
    stats = {
        "open": len(open_clients),
        "new_30": sum(1 for c in clients if c.created_at and c.created_at >= since),
        "filed_30": db.query(ClaimUpdate).filter(ClaimUpdate.org_id == org, ClaimUpdate.status_key == "filed", ClaimUpdate.created_at >= since).count(),
        "docs_30": db.query(Document).filter(Document.org_id == org, Document.created_at >= since).count(),
        "msgs_30": db.query(Message).filter(Message.org_id == org, Message.created_at >= since).count(),
        "overdue": len(overdue_rows),
        "unassigned": unassigned,
    }
    people = {u.id: u for u in db.query(User).filter(User.org_id == org).all()}
    return render_template("director/dashboard.html", users=users, audits=audits, stats=stats, workload=workload, people=people)

@director_bp.post("/backup/run")
@login_required
@require_roles(Role.DIRECTOR)
def run_backup():
    script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "backup_storage.cmd"))
    if not os.path.exists(script_path):
        flash("Backup script not found.", "error")
        return redirect(url_for("director.dashboard"))
    try:
        result = subprocess.run([script_path], check=False, shell=True)
        # Robocopy uses bitmask exit codes; 0-7 are considered success.
        if result.returncode <= 7:
            audit_log(current_user.org_id, current_user.id, "BACKUP_RUN", "System", "storage")
            flash("Backup completed.", "success")
        else:
            flash(f"Backup failed: exit code {result.returncode}", "error")
    except Exception as e:
        flash(f"Backup failed: {e}", "error")
    return redirect(url_for("director.dashboard"))


@director_bp.get("/users")
@login_required
@require_roles(Role.DIRECTOR)
def users():
    from app.services.bot_guard import gibberish_name
    from app.models import Document, Message
    db = SessionLocal()
    tab = request.args.get("tab") or "team"
    rows = db.query(User).filter_by(org_id=current_user.org_id).order_by(User.created_at.desc()).all()
    team = [u for u in rows if u.role in (Role.DIRECTOR, Role.EMPLOYEE)]
    logins = [u for u in rows if u.role == Role.CLIENT]
    clients_by_user = {c.portal_user_id: c for c in db.query(Client).filter(Client.org_id == current_user.org_id,
                                                                           Client.portal_user_id.isnot(None)).all()}
    active_clients = {cid for (cid,) in db.query(Document.client_id).distinct().all()} | \
                     {cid for (cid,) in db.query(Message.client_id).filter(Message.sender_role == "CLIENT").distinct().all()}
    info = {}
    for u in logins:
        c = clients_by_user.get(u.id)
        engaged = bool(c and c.id in active_clients)
        one_visit = not u.last_login_at or not u.created_at or abs((u.last_login_at - u.created_at).total_seconds()) < 86400
        spam = u.is_active and one_visit and not engaged and (
            gibberish_name(u.full_name or "") or gibberish_name((u.email or "").split("@")[0]))
        info[u.id] = {"client": c, "engaged": engaged, "spam": spam}
    open_by_rep = {}
    for c in db.query(Client).filter(Client.org_id == current_user.org_id, Client.is_archived == False).all():  # noqa: E712
        if c.assigned_user_id:
            open_by_rep[c.assigned_user_id] = open_by_rep.get(c.assigned_user_id, 0) + 1
    only_spam = request.args.get("spam") == "1"
    shown = [u for u in logins if info[u.id]["spam"]] if only_spam else logins
    return render_template("director/users.html", tab=tab, team=team, logins=shown, all_logins=logins, info=info,
                           spam_count=sum(1 for u in logins if info[u.id]["spam"]), only_spam=only_spam,
                           open_by_rep=open_by_rep, new_password=request.args.get("new"))


@director_bp.post("/users/bulk-disable")
@login_required
@require_roles(Role.DIRECTOR)
def bulk_disable_users():
    """Disable client logins in bulk (e.g. bot sign-ups); optionally archive their empty client records."""
    ids = [int(i) for i in request.form.getlist("user_ids") if i.isdigit()]
    db = SessionLocal()
    rows = db.query(User).filter(User.org_id == current_user.org_id, User.id.in_(ids or [0]), User.role == Role.CLIENT).all()
    for u in rows:
        u.is_active = False
        if request.form.get("archive_clients") == "1":
            for c in db.query(Client).filter(Client.portal_user_id == u.id).all():
                c.is_archived = True
    db.commit()
    audit_log(current_user.org_id, current_user.id, "USERS_DISABLED_BULK", "User", "bulk", detail=f"count={len(rows)}")
    if not rows:
        flash("Select at least one login.", "error")
        return redirect(url_for("director.users", tab="clients"))
    flash(f"Disabled {len(rows)} login{'s' if len(rows) != 1 else ''}." + (" Their client records were archived." if request.form.get("archive_clients") == "1" else ""), "success")
    return redirect(url_for("director.users", tab="clients"))


@director_bp.get("/traffic")
@login_required
@require_roles(Role.DIRECTOR)
def traffic():
    db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=30)
        bot_patterns = [
            "bot", "spider", "crawler", "slurp", "bingpreview",
            "facebookexternalhit", "preview", "python-requests", "aiohttp",
            "httpclient", "wget", "curl", "uptime", "pingdom", "healthcheck",
        ]
        staff_ids = (
            db.query(User.id)
            .filter(User.org_id == current_user.org_id, User.role.in_([Role.DIRECTOR, Role.EMPLOYEE]))
            .all()
        )
        staff_ids = [sid for (sid,) in staff_ids]

        def apply_real_filters(q):
            q = q.filter(PageView.created_at >= since)
            q = q.filter(or_(PageView.org_id == current_user.org_id, PageView.org_id == None))
            # Exclude all logged-in traffic (staff, clients, DIY)
            q = q.filter(PageView.user_id == None)
            ua = func.lower(PageView.user_agent)
            for p in bot_patterns:
                q = q.filter(~ua.like(f"%{p}%"))
            return q

        total = apply_real_filters(db.query(func.count(PageView.id))).scalar() or 0
        unique = apply_real_filters(db.query(func.count(distinct(PageView.ip_hash)))).scalar() or 0
        top_paths = (
            apply_real_filters(db.query(PageView.path, func.count(PageView.id)))
            .group_by(PageView.path)
            .order_by(func.count(PageView.id).desc())
            .limit(20)
            .all()
        )
        daily = (
            apply_real_filters(db.query(func.date(PageView.created_at), func.count(distinct(PageView.ip_hash))))
            .group_by(func.date(PageView.created_at))
            .order_by(func.date(PageView.created_at))
            .all()
        )
    finally:
        db.close()
    return render_template(
        "director/traffic.html",
        total=total,
        unique=unique,
        top_paths=top_paths,
        daily=daily,
    )

@director_bp.post("/users/create")
@login_required
@require_roles(Role.DIRECTOR)
def create_user():
    db = SessionLocal()
    email = (request.form.get("email") or "").strip().lower()
    full_name = request.form.get("full_name") or ""
    role = request.form.get("role") or "EMPLOYEE"
    if role not in ("EMPLOYEE", "DIRECTOR"):
        role = "EMPLOYEE"
    import secrets as _secrets
    password = (request.form.get("password") or "").strip() or _secrets.token_urlsafe(9)
    if not email or "@" not in email:
        flash("Enter a valid email.", "error")
        return redirect(url_for("director.users"))
    if db.query(User).filter(User.email == email).first():
        flash("A user with that email already exists.", "error")
        return redirect(url_for("director.users"))
    u = User(org_id=current_user.org_id, email=email, full_name=full_name, role=Role(role), is_active=True, must_reset_password=True)
    u.set_password(password)
    db.add(u); db.commit()
    audit_log(current_user.org_id, current_user.id, "USER_CREATED", "User", u.id, detail=f"role={role}, email={email}")
    flash(f"Added {email}. Temporary password: {password} (they'll choose a new one at first sign-in). Share it privately.", "success")
    return redirect(url_for("director.users"))

@director_bp.post("/users/<int:user_id>/toggle")
@login_required
@require_roles(Role.DIRECTOR)
def toggle_user(user_id: int):
    db = SessionLocal()
    u = db.get(User, user_id)
    if not u or u.org_id != current_user.org_id:
        flash("User not found.", "error")
        return redirect(url_for("director.dashboard"))
    u.is_active = not u.is_active
    db.commit()
    audit_log(current_user.org_id, current_user.id, "USER_TOGGLED", "User", u.id, detail=f"is_active={u.is_active}")
    if u.id == current_user.id and not u.is_active:
        u.is_active = True
        db.commit()
        flash("You can't disable your own account.", "error")
        return redirect(url_for("director.users"))
    flash(f"{u.email} is now {'active' if u.is_active else 'disabled'}.", "success")
    return redirect(request.referrer or url_for("director.users"))

@director_bp.post("/users/<int:user_id>/force-reset")
@login_required
@require_roles(Role.DIRECTOR)
def force_reset(user_id: int):
    db = SessionLocal()
    u = db.get(User, user_id)
    if not u or u.org_id != current_user.org_id:
        flash("User not found.", "error")
        return redirect(url_for("director.dashboard"))
    u.must_reset_password = True
    db.commit()
    audit_log(current_user.org_id, current_user.id, "USER_FORCE_RESET", "User", u.id)
    flash(f"{u.email} will be asked to set a new password at next sign-in.", "success")
    return redirect(request.referrer or url_for("director.users"))

@director_bp.post("/templates/seed-defaults")
@login_required
@require_roles(Role.DIRECTOR)
def seed_templates():
    db = SessionLocal()
    if db.query(Template).filter(Template.org_id == current_user.org_id).count():
        flash("Templates already exist.", "info")
        return redirect(url_for("director.templates"))
    for t in DEFAULT_TEMPLATES:
        db.add(Template(org_id=current_user.org_id, name=t["name"], category=t["category"], body=t["body"], is_active=True))
    db.commit()
    audit_log(current_user.org_id, current_user.id, "TEMPLATES_SEEDED", "Template", "bulk")
    flash("Default templates seeded.", "success")
    return redirect(url_for("director.templates"))


@director_bp.get("/templates")
@login_required
@require_roles(Role.DIRECTOR)
def templates():
    db = SessionLocal()
    items = (
        db.query(Template)
        .filter(Template.org_id == current_user.org_id)
        .order_by(Template.category.asc(), Template.name.asc())
        .all()
    )
    return render_template("director/templates.html", templates=items)


@director_bp.post("/templates/create")
@login_required
@require_roles(Role.DIRECTOR)
def templates_create():
    db = SessionLocal()
    name = (request.form.get("name") or "").strip()
    category = (request.form.get("category") or "General").strip()
    body = (request.form.get("body") or "").strip()
    is_active = request.form.get("is_active") == "1"
    if not name or not body:
        flash("Template name and body are required.", "error")
        return redirect(url_for("director.templates"))
    item = Template(
        org_id=current_user.org_id,
        name=name,
        category=category or "General",
        body=body,
        is_active=is_active,
    )
    db.add(item)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "TEMPLATE_CREATED", "Template", item.id)
    flash("Template created.", "success")
    return redirect(url_for("director.templates"))


@director_bp.post("/templates/<int:template_id>/update")
@login_required
@require_roles(Role.DIRECTOR)
def templates_update(template_id: int):
    db = SessionLocal()
    item = db.get(Template, template_id)
    if not item or item.org_id != current_user.org_id:
        flash("Template not found.", "error")
        return redirect(url_for("director.templates"))
    item.name = (request.form.get("name") or item.name).strip()
    item.category = (request.form.get("category") or item.category).strip() or "General"
    item.body = (request.form.get("body") or item.body).strip()
    item.is_active = request.form.get("is_active") == "1"
    if not item.name or not item.body:
        flash("Template name and body are required.", "error")
        return redirect(url_for("director.templates"))
    db.commit()
    audit_log(current_user.org_id, current_user.id, "TEMPLATE_UPDATED", "Template", item.id)
    flash("Template updated.", "success")
    return redirect(url_for("director.templates"))


@director_bp.post("/templates/<int:template_id>/delete")
@login_required
@require_roles(Role.DIRECTOR)
def templates_delete(template_id: int):
    db = SessionLocal()
    item = db.get(Template, template_id)
    if not item or item.org_id != current_user.org_id:
        flash("Template not found.", "error")
        return redirect(url_for("director.templates"))
    db.delete(item)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "TEMPLATE_DELETED", "Template", template_id)
    flash("Template deleted.", "success")
    return redirect(url_for("director.templates"))


@director_bp.get("/faq")
@login_required
@require_roles(Role.DIRECTOR)
def faq():
    db = SessionLocal()
    items = db.query(FAQItem).filter(FAQItem.org_id == current_user.org_id).order_by(FAQItem.category.asc(), FAQItem.sort_order.asc(), FAQItem.id.asc()).all()
    return render_template("director/faq.html", items=items)


@director_bp.get("/comments")
@login_required
@require_roles(Role.DIRECTOR)
def comments():
    db = SessionLocal()
    items = db.query(PublicComment).order_by(PublicComment.created_at.desc()).all()
    return render_template("director/comments.html", items=items)


@director_bp.post("/comments/<int:comment_id>/approve")
@login_required
@require_roles(Role.DIRECTOR)
def comments_approve(comment_id: int):
    db = SessionLocal()
    item = db.get(PublicComment, comment_id)
    if not item:
        flash("Comment not found.", "error")
        return redirect(url_for("director.comments"))
    item.is_approved = True
    item.is_rejected = False
    db.commit()
    flash("Comment approved.", "success")
    return redirect(url_for("director.comments"))


@director_bp.get("/articles")
@login_required
@require_roles(Role.DIRECTOR)
def articles():
    items = list_articles()
    return render_template("director/articles.html", articles=items)


@director_bp.get("/articles/<slug>")
@login_required
@require_roles(Role.DIRECTOR)
def articles_edit(slug: str):
    article = load_article(slug)
    if not article:
        flash("Article not found.", "error")
        return redirect(url_for("director.articles"))
    return render_template("director/article_edit.html", article=article)


@director_bp.get("/articles/new")
@login_required
@require_roles(Role.DIRECTOR)
def articles_new():
    article = {"slug": "", "title": "", "summary": "", "body": ""}
    return render_template("director/article_edit.html", article=article)


@director_bp.post("/articles/save")
@login_required
@require_roles(Role.DIRECTOR)
def articles_save():
    slug = request.form.get("slug", "")
    title = request.form.get("title", "")
    summary = request.form.get("summary", "")
    body = request.form.get("body", "")
    if not title.strip() or not summary.strip() or not body.strip():
        flash("Title, summary, and body are required.", "error")
        return redirect(url_for("director.articles_new"))
    safe_slug = save_article(slug or title, title, summary, body)
    flash("Article saved.", "success")
    return redirect(url_for("director.articles_edit", slug=safe_slug))


@director_bp.post("/articles/<slug>/delete")
@login_required
@require_roles(Role.DIRECTOR)
def articles_delete(slug: str):
    delete_article(slug)
    flash("Article deleted.", "success")
    return redirect(url_for("director.articles"))

@director_bp.post("/comments/<int:comment_id>/reject")
@login_required
@require_roles(Role.DIRECTOR)
def comments_reject(comment_id: int):
    db = SessionLocal()
    item = db.get(PublicComment, comment_id)
    if not item:
        flash("Comment not found.", "error")
        return redirect(url_for("director.comments"))
    item.is_approved = False
    item.is_rejected = True
    db.commit()
    flash("Comment rejected.", "success")
    return redirect(url_for("director.comments"))


@director_bp.post("/comments/bulk-approve")
@login_required
@require_roles(Role.DIRECTOR)
def comments_bulk_approve():
    ids = request.form.getlist("comment_ids")
    if not ids:
        flash("Select at least one comment to approve.", "error")
        return redirect(url_for("director.comments"))
    db = SessionLocal()
    try:
        items = db.query(PublicComment).filter(PublicComment.id.in_(ids)).all()
        for item in items:
            item.is_approved = True
            item.is_rejected = False
        db.commit()
    finally:
        db.close()
    flash("Selected comments approved.", "success")
    return redirect(url_for("director.comments"))


@director_bp.post("/comments/bulk-reject")
@login_required
@require_roles(Role.DIRECTOR)
def comments_bulk_reject():
    ids = request.form.getlist("comment_ids")
    if not ids:
        flash("Select at least one comment to reject.", "error")
        return redirect(url_for("director.comments"))
    db = SessionLocal()
    try:
        items = db.query(PublicComment).filter(PublicComment.id.in_(ids)).all()
        for item in items:
            item.is_approved = False
            item.is_rejected = True
        db.commit()
    finally:
        db.close()
    flash("Selected comments rejected.", "success")
    return redirect(url_for("director.comments"))


@director_bp.post("/faq/create")
@login_required
@require_roles(Role.DIRECTOR)
def faq_create():
    db = SessionLocal()
    question = (request.form.get("question") or "").strip()
    answer = (request.form.get("answer") or "").strip()
    category = (request.form.get("category") or "General").strip()
    sort_order = int(request.form.get("sort_order") or 0)
    is_active = request.form.get("is_active") == "1"
    if not question or not answer:
        flash("Question and answer are required.", "error")
        return redirect(url_for("director.faq"))
    row = FAQItem(
        org_id=current_user.org_id,
        question=question,
        answer=answer,
        category=category or "General",
        sort_order=sort_order,
        is_active=is_active,
    )
    db.add(row)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "FAQ_CREATED", "FAQItem", row.id)
    flash("FAQ item created.", "success")
    return redirect(url_for("director.faq"))


@director_bp.post("/faq/<int:item_id>/update")
@login_required
@require_roles(Role.DIRECTOR)
def faq_update(item_id: int):
    db = SessionLocal()
    row = db.get(FAQItem, item_id)
    if not row or row.org_id != current_user.org_id:
        flash("FAQ item not found.", "error")
        return redirect(url_for("director.faq"))
    row.question = (request.form.get("question") or row.question).strip()
    row.answer = (request.form.get("answer") or row.answer).strip()
    row.category = (request.form.get("category") or row.category).strip() or "General"
    row.sort_order = int(request.form.get("sort_order") or row.sort_order or 0)
    row.is_active = request.form.get("is_active") == "1"
    db.commit()
    audit_log(current_user.org_id, current_user.id, "FAQ_UPDATED", "FAQItem", row.id)
    flash("FAQ item updated.", "success")
    return redirect(url_for("director.faq"))


@director_bp.post("/faq/<int:item_id>/delete")
@login_required
@require_roles(Role.DIRECTOR)
def faq_delete(item_id: int):
    db = SessionLocal()
    row = db.get(FAQItem, item_id)
    if not row or row.org_id != current_user.org_id:
        flash("FAQ item not found.", "error")
        return redirect(url_for("director.faq"))
    db.delete(row)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "FAQ_DELETED", "FAQItem", item_id)
    flash("FAQ item deleted.", "success")
    return redirect(url_for("director.faq"))


@director_bp.get("/diy-users")
@login_required
@require_roles(Role.DIRECTOR)
def diy_users():
    db = SessionLocal()
    rows = db.query(DIYAccount).filter_by(org_id=current_user.org_id).order_by(DIYAccount.created_at.desc()).all()
    subs = {}
    for r in rows:
        sub = db.query(DIYSubscription).filter_by(diy_id=r.id).order_by(DIYSubscription.created_at.desc()).first()
        subs[r.id] = sub
    total = len(rows)
    purchased = sum(1 for r in rows if subs.get(r.id) and subs.get(r.id).status in ("active", "trialing", "canceling"))
    converted_ids = set([c.portal_user_id for c in db.query(Client.portal_user_id).filter(Client.portal_user_id.isnot(None)).all()])
    converted = sum(1 for r in rows if r.user_id in converted_ids)
    return render_template("director/diy_users.html", rows=rows, subs=subs, total=total, purchased=purchased, converted=converted, converted_ids=converted_ids)


@director_bp.get("/onboarding-items")
@login_required
@require_roles(Role.DIRECTOR)
def onboarding_items():
    db = SessionLocal()
    items = db.query(OnboardingItem).order_by(OnboardingItem.sort_order.asc(), OnboardingItem.id.asc()).all()
    return render_template("director/onboarding_items.html", items=items)


@director_bp.post("/onboarding-items/create")
@login_required
@require_roles(Role.DIRECTOR)
def onboarding_items_create():
    db = SessionLocal()
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    task_url = (request.form.get("task_url") or "").strip()
    sort_order = int(request.form.get("sort_order") or 0)
    is_active = (request.form.get("is_active") == "1")
    is_required = (request.form.get("is_required") == "1")
    if not title or not task_url:
        flash("Title and task URL are required.", "error")
        return redirect(url_for("director.onboarding_items"))
    item = OnboardingItem(
        title=title,
        description=description,
        task_url=task_url,
        sort_order=sort_order,
        is_active=is_active,
        is_required=is_required,
    )
    db.add(item)
    db.commit()
    flash("Onboarding item created.", "success")
    return redirect(url_for("director.onboarding_items"))


@director_bp.post("/onboarding-items/<int:item_id>/update")
@login_required
@require_roles(Role.DIRECTOR)
def onboarding_items_update(item_id: int):
    db = SessionLocal()
    item = db.get(OnboardingItem, item_id)
    if not item:
        flash("Item not found.", "error")
        return redirect(url_for("director.onboarding_items"))
    item.title = (request.form.get("title") or "").strip()
    item.description = (request.form.get("description") or "").strip()
    item.task_url = (request.form.get("task_url") or "").strip()
    item.sort_order = int(request.form.get("sort_order") or 0)
    item.is_active = (request.form.get("is_active") == "1")
    item.is_required = (request.form.get("is_required") == "1")
    db.commit()
    flash("Onboarding item updated.", "success")
    return redirect(url_for("director.onboarding_items"))


@director_bp.post("/onboarding-items/<int:item_id>/delete")
@login_required
@require_roles(Role.DIRECTOR)
def onboarding_items_delete(item_id: int):
    db = SessionLocal()
    item = db.get(OnboardingItem, item_id)
    if not item:
        flash("Item not found.", "error")
        return redirect(url_for("director.onboarding_items"))
    has_progress = db.query(EmployeeOnboardingProgress).filter_by(item_id=item_id).count() > 0
    if has_progress:
        flash("Cannot delete item that has completions. Deactivate it instead.", "error")
        return redirect(url_for("director.onboarding_items"))
    db.delete(item)
    db.commit()
    flash("Onboarding item deleted.", "success")
    return redirect(url_for("director.onboarding_items"))
