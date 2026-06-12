from __future__ import annotations

import datetime
import hashlib
import os
from werkzeug.utils import secure_filename

from flask import Blueprint, render_template, request, current_app, jsonify, flash, redirect, url_for, send_from_directory, Response
from flask_login import current_user
import json

from app.extensions import SessionLocal, csrf
from app.models import Organization, PricingItem, FAQItem, DIYAccount, DIYSubscription, PublicComment
from app.models.user import Role
from app.services.diy_access import has_diy_access
from app.services.locator import zip_to_place
from app.services.article_store import list_articles, load_article

public_bp = Blueprint("public", __name__)

def _get_pricing_items():
    db = SessionLocal()
    default_name = current_app.config.get("DEFAULT_TENANT", "Veteran Benefits Assistance")
    org = db.query(Organization).filter(Organization.name == default_name).first()
    if not org:
        org = db.query(Organization).first()
    if not org:
        return []
    return (
        db.query(PricingItem)
        .filter(PricingItem.org_id == org.id, PricingItem.active == True)  # noqa: E712
        .order_by(PricingItem.sort_order.asc(), PricingItem.created_at.desc())
        .all()
    )

def _ai_tool_allowed() -> tuple[bool, str]:
    # Allow staff and clients.
    if current_user.is_authenticated:
        if current_user.role in (Role.DIRECTOR, Role.EMPLOYEE, Role.CLIENT):
            return True, ""
        if current_user.role == Role.DIY:
            db = SessionLocal()
            try:
                acct = db.query(DIYAccount).filter_by(user_id=current_user.id).first()
                if not acct:
                    return False, "DIY account not found."
                sub = db.query(DIYSubscription).filter_by(diy_id=acct.id).order_by(DIYSubscription.created_at.desc()).first()
                if not sub or sub.status not in ("active", "trialing", "canceling"):
                    return False, "AI tools require an active Pro subscription."
                if (sub.plan or "").lower() != "pro":
                    return False, "AI tools require a Pro subscription."
                return True, ""
            finally:
                db.close()
    return False, "AI tools require a Pro subscription and login."

@public_bp.get("/")
def home():
    pricing_items = _get_pricing_items()
    diy_access = False
    comments = []
    if current_user.is_authenticated:
        if current_user.role == Role.DIY:
            diy_access = has_diy_access(current_user.id)
        else:
            diy_access = True
    db = SessionLocal()
    try:
        comments = (
            db.query(PublicComment)
            .filter_by(is_approved=True)
            .order_by(PublicComment.created_at.desc())
            .limit(12)
            .all()
        )
    finally:
        db.close()
    return render_template(
        "public/home.html",
        pricing_items=pricing_items,
        diy_access=diy_access,
        comments=comments,
        meta_title=f"{current_app.config.get('SITE_NAME','Veteran Benefits Assistance')} | VA Claims Guidance",
        meta_description="VA claims guidance with evidence checklists, rating criteria, and a secure portal. Build a clear plan and avoid common gaps.",
    )

@public_bp.get("/pricing")
def pricing():
    pricing_items = _get_pricing_items()
    return render_template("public/pricing.html", pricing_items=pricing_items, meta_title="Pricing | VA Claims Support", meta_description="Transparent pricing for VA claims organization, guidance, and support services.")



@public_bp.get("/health")
def health_check():
    return jsonify({"status": "ok"})

@public_bp.get("/privacy")
def privacy():
    return render_template("public/privacy.html", meta_title="Privacy Policy", meta_description="Privacy and data handling for veteran support services.")

@public_bp.get("/security")
def security():
    return render_template("public/security.html", meta_title="Security", meta_description="Security practices for protecting client data and documents.")

@public_bp.get("/faq")
def faq():
    db = SessionLocal()
    rows = db.query(FAQItem).filter_by(is_active=True).order_by(FAQItem.category.asc(), FAQItem.sort_order.asc(), FAQItem.id.asc()).all()
    if not rows:
        defaults = [
            {"category": "Getting Started", "question": "How do I start?", "answer": "Create a DIY account or become a client, then complete the intake and checklist."},
            {"category": "Getting Started", "question": "Do you guarantee outcomes?", "answer": "No. We provide organization and education, not legal guarantees."},
            {"category": "Tools", "question": "What does the rating chart show?", "answer": "Condition-by-condition CFR criteria with percentage breakdowns."},
            {"category": "Tools", "question": "How does the rating calculator work?", "answer": "It uses VA math to combine ratings and rounds to the nearest 10."},
            {"category": "Evidence", "question": "What evidence do I need?", "answer": "Diagnosis, in-service event/exposure, and nexus or continuity evidence."},
            {"category": "Evidence", "question": "Can I upload documents?", "answer": "Yes. Pro DIY includes document upload and scan. Client support includes uploads."},
            {"category": "Billing", "question": "Can I cancel?", "answer": "Yes. Cancel anytime. Refunds apply if canceled within 72 hours."},
            {"category": "Billing", "question": "What is the refund policy?", "answer": "Cancel within 72 hours: 100% refund with no uploads, 50% refund with uploads. After 72 hours: no refund."},
            {"category": "Privacy", "question": "How is my data protected?", "answer": "We use encrypted storage, audit logs, and role-based access controls."},
            {"category": "Support", "question": "Can I switch from DIY to full client support?", "answer": "Yes. You can upgrade anytime from your DIY dashboard."},
        ]
        for idx, row in enumerate(defaults):
            db.add(FAQItem(
                org_id=(db.query(Organization).first().id if db.query(Organization).first() else 1),
                question=row["question"],
                answer=row["answer"],
                category=row["category"],
                sort_order=idx,
                is_active=True,
            ))
        db.commit()
        rows = db.query(FAQItem).filter_by(is_active=True).order_by(FAQItem.category.asc(), FAQItem.sort_order.asc(), FAQItem.id.asc()).all()
    grouped = []
    buckets = {}
    for r in rows:
        cat = (r.category or "General").strip()
        buckets.setdefault(cat, []).append(r)
    for cat in sorted(buckets.keys()):
        grouped.append({"category": cat, "items": buckets[cat]})
    return render_template("public/faq.html", grouped=grouped, meta_title="FAQ", meta_description="Common questions about VA claims guidance, DIY tools, and privacy.")

@public_bp.get("/professional")
def professional():
    return render_template("public/professional.html", meta_title="Professional Services", meta_description="Professional VA claims organization and guidance services.")

@public_bp.route("/contact", methods=["GET", "POST"])
def contact():
    meta_title = "Contact"
    meta_description = "Contact our team with questions about VA claims guidance and support."
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        message = (request.form.get("message") or "").strip()
        if not name or not email or not message:
            flash("Please complete all fields.", "error")
            return render_template("public/contact.html", meta_title=meta_title, meta_description=meta_description)
        try:
            from app.services.email_service import send_email_html
            to_email = os.environ.get("BRAND_EMAIL") or current_app.config.get("SUPPORT_EMAIL", "")
            subject = f"Website contact from {name}"
            body_text = f"Name: {name}\nEmail: {email}\n\nMessage:\n{message}\n"
            body_html = (
                f"<p><strong>Name:</strong> {name}</p>"
                f"<p><strong>Email:</strong> {email}</p>"
                f"<p><strong>Message:</strong><br>{message}</p>"
            )
            ok, err = send_email_html(to_email, subject, body_text, body_html, context="CONTACT_FORM")
            if not ok:
                flash(f"Email failed: {err}", "error")
                return render_template("public/contact.html", meta_title=meta_title, meta_description=meta_description)
        except Exception as exc:
            flash(f"Email failed: {exc}", "error")
            return render_template("public/contact.html", meta_title=meta_title, meta_description=meta_description)
        flash("Message sent. We will respond shortly.", "success")
        return redirect(url_for("public.contact"))
    return render_template("public/contact.html", meta_title=meta_title, meta_description=meta_description)

@public_bp.get("/favicon.ico")
def favicon():
    return send_from_directory(current_app.static_folder, "favicon.ico")

@public_bp.get("/apple-touch-icon.png")
def apple_touch_icon():
    return send_from_directory(current_app.static_folder, "apple-touch-icon.png")

@public_bp.get("/apple-touch-icon-precomposed.png")
def apple_touch_icon_precomposed():
    return send_from_directory(current_app.static_folder, "apple-touch-icon-precomposed.png")

@public_bp.get("/google21e1f40e5238076b.html")
def google_site_verification():
    return "google-site-verification: google21e1f40e5238076b.html"

@public_bp.post("/comments")
@csrf.exempt
def public_comment_post():
    name = (request.form.get("name") or "").strip()
    branch = (request.form.get("branch") or "").strip()
    comment = (request.form.get("comment") or "").strip()
    if not name or not comment:
        flash("Please add your name and a comment.", "error")
        return redirect(url_for("public.home") + "#comments")
    if len(comment) > 1000:
        flash("Comment is too long (max 1000 characters).", "error")
        return redirect(url_for("public.home") + "#comments")
    db = SessionLocal()
    try:
        db.add(PublicComment(name=name[:120], branch=branch[:80], comment=comment, is_approved=False))
        db.commit()
    finally:
        db.close()
    flash("Thanks for your comment! It will appear after approval.", "success")
    return redirect(url_for("public.home") + "#comments")

@public_bp.get("/robots.txt")
def robots_txt():
    base_url = request.url_root.rstrip("/")
    content = "\n".join([
        "User-agent: *",
        "Disallow: /portal/",
        "Disallow: /director/",
        "Disallow: /employee/",
        f"Sitemap: {base_url}/sitemap.xml",
    ])
    return Response(content, mimetype="text/plain")

@public_bp.get("/sitemap.xml")
def sitemap_xml():
    base_url = request.url_root.rstrip("/")
    today = datetime.date.today().isoformat()
    routes = [
        url_for("public.home"),
        url_for("public.pricing"),
        url_for("public.benefits_hub"),
        url_for("public.crsc"),
        url_for("public.guides"),
        url_for("public.article_list"),
        url_for("public.guide_detail", slug="va-evidence-checklist"),
        url_for("public.guide_detail", slug="va-rating-criteria"),
        url_for("public.guide_detail", slug="denied-claim-next-steps"),
        url_for("public.guide_detail", slug="google-search-visibility"),
        url_for("public.intelligent_benefits"),
        url_for("public.state_benefits"),
        url_for("public.faq"),
        url_for("public.privacy"),
        url_for("public.security"),
        url_for("public.contact"),
        url_for("public.professional"),
        url_for("cfr.ratings_chart"),
        url_for("diy.landing"),
        url_for("diy.register"),
        url_for("diy.login"),
    ]
    urls = []
    for r in routes:
        loc = f"{base_url}{r}"
        urls.append(
            "<url>"
            f"<loc>{loc}</loc>"
            f"<lastmod>{today}</lastmod>"
            "<changefreq>weekly</changefreq>"
            "<priority>0.7</priority>"
            "</url>"
        )
    for a in list_articles():
        loc = f"{base_url}{url_for('public.article_detail', slug=a['slug'])}"
        lastmod = (a.get("updated_at") or datetime.datetime.utcnow()).date().isoformat()
        urls.append(
            "<url>"
            f"<loc>{loc}</loc>"
            f"<lastmod>{lastmod}</lastmod>"
            "<changefreq>weekly</changefreq>"
            "<priority>0.7</priority>"
            "</url>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(urls) +
        "</urlset>"
    )
    return Response(xml, mimetype="application/xml")


@public_bp.get("/blog")
def article_list():
    items = list_articles()
    items.sort(key=lambda x: x.get("updated_at") or datetime.datetime.min, reverse=True)
    return render_template(
        "public/article_list.html",
        articles=items,
        meta_title="VA Articles & Guides",
        meta_description="Veteran-focused VA disability education articles and claim preparation guidance.",
    )


@public_bp.get("/blog/<slug>")
def article_detail(slug: str):
    article = load_article(slug)
    if not article:
        return redirect(url_for("public.article_list"))
    related = [a for a in list_articles() if a.get("slug") != slug]
    related.sort(key=lambda x: x.get("updated_at") or datetime.datetime.min, reverse=True)
    related = related[:3]
    return render_template(
        "public/article_detail.html",
        article=article,
        related=related,
        meta_title=article["title"],
        meta_description=article["summary"],
    )

@public_bp.get("/benefits-hub")
def benefits_hub():
    return render_template("public/benefits_hub.html", meta_title="Benefits Hub", meta_description="Explore veteran benefits tools and resources.")

@public_bp.get("/crsc")
def crsc():
    return render_template("public/crsc.html", meta_title="CRSC & Combat Pay", meta_description="Combat-Related Special Compensation (CRSC) guidance for retirees, eligibility, evidence, and packet preparation.")

@public_bp.get("/guides")
def guides():
    return render_template("public/guides.html", meta_title="VA Claims Guides", meta_description="Practical guides for evidence checklists, rating criteria, and next steps after denial.")

@public_bp.get("/guides/<slug>")
def guide_detail(slug: str):
    guides = {
        "va-evidence-checklist": {
            "title": "VA Evidence Checklist",
            "kicker": "Evidence Guide",
            "summary": "A clear checklist for diagnosis, service event, and nexus evidence.",
            "sections": [
                {"title": "1) Diagnosis evidence", "items": ["Current diagnosis notes", "Specialist findings or DBQ", "Medication lists or treatment plan"]},
                {"title": "2) In-service event", "items": ["Service treatment records", "Line-of-duty or incident reports", "Deployment records or awards"]},
                {"title": "3) Nexus evidence", "items": ["Medical opinion linking condition to service", "Continuity of symptoms notes", "Lay statements or buddy statements"]},
            ],
        },
        "va-rating-criteria": {
            "title": "Understanding VA Rating Criteria",
            "kicker": "Ratings Guide",
            "summary": "How to interpret CFR criteria and percentage levels.",
            "sections": [
                {"title": "Read the criteria like a checklist", "items": ["Focus on symptoms, frequency, and functional impact", "Match your evidence to the exact criteria language"]},
                {"title": "Use the rating chart", "items": ["Compare percentage levels side by side", "Look for objective tests or measurements"]},
                {"title": "Document what matters", "items": ["Severity over time", "Flare-ups and missed work", "Treatments tried and outcomes"]},
            ],
        },
        "denied-claim-next-steps": {
            "title": "Denied Claim: What Next?",
            "kicker": "Decision Guide",
            "summary": "Choose the right path after a denial or low rating.",
            "sections": [
                {"title": "Supplemental claim", "items": ["Add new and relevant evidence", "Best when evidence was missing or incomplete"]},
                {"title": "Higher-level review", "items": ["No new evidence allowed", "Best for clear errors in decision"]},
                {"title": "Appeal to Board", "items": ["Longer timeline", "Best for complex disputes"]},
            ],
        },
        "google-search-visibility": {
            "title": "How Our Website Appears in Google Search Results",
            "kicker": "Search Visibility",
            "summary": "How educational content helps veterans find answers in Google.",
            "sections": [
                {"title": "How veterans find us on Google", "items": [
                    "Veterans search for answers, not business names.",
                    "We target real VA questions such as back pay timelines and denial next steps.",
                    "Each page is written to answer one specific question clearly.",
                ]},
                {"title": "Why educational pages matter more than ads", "items": [
                    "Google ranks pages based on usefulness and trust.",
                    "Clear explanations and step-by-step guidance build credibility.",
                    "Educational content earns visibility without paid ads.",
                ]},
                {"title": "How content is structured for search visibility", "items": [
                    "A clear page title that matches veteran search phrases.",
                    "Direct answers at the top of each page.",
                    "Supporting sections that explain details and common mistakes.",
                    "A short section showing how we help with claim preparation.",
                ]},
                {"title": "Topics we publish", "items": [
                    "VA compensation and pay: rates, dependency increases, back pay timelines.",
                    "VA claims and appeals: denials, supplemental claims, and decision errors.",
                    "C&P exam prep: expectations, mistakes to avoid, and rating impact.",
                ]},
                {"title": "Why Google trusts this website", "items": [
                    "Veteran-focused, experience-based educational content.",
                    "Clear explanations without misleading promises.",
                    "Transparent service disclosures and consistent updates.",
                ]},
                {"title": "How this benefits veterans", "items": [
                    "Faster access to accurate answers.",
                    "Better decisions before filing.",
                    "Fewer common claim mistakes.",
                ]},
                {"title": "How veterans can take the next step", "items": [
                    "Request assistance with claim preparation.",
                    "Ask questions about a specific situation.",
                    "Learn more about services without obligation.",
                ]},
                {"title": "Ongoing website updates", "items": [
                    "Reflect current VA compensation rates.",
                    "Address new VA policies and procedures.",
                    "Add answers to trending veteran questions.",
                ]},
            ],
        },
    }
    guide = guides.get(slug)
    if not guide:
        return redirect(url_for("public.guides"))
    return render_template("public/guide.html", guide=guide, meta_title=guide["title"], meta_description=guide["summary"])

@public_bp.get("/intelligent-benefits")
def intelligent_benefits():
    ok, msg = _ai_tool_allowed()
    return render_template("public/intelligent_benefits.html", ai_allowed=ok, ai_message=msg, meta_title="Intelligent Benefits Finder", meta_description="Find likely benefits based on service era, rating, and location.")

@public_bp.get("/services-locator")
def services_locator():
    return redirect(url_for("public.intelligent_benefits") + "#services-locator")

@public_bp.get("/state-benefits")
def state_benefits():
    ok, msg = _ai_tool_allowed()
    return render_template("public/state_benefits.html", ai_allowed=ok, ai_message=msg, meta_title="State Benefits", meta_description="State-level veteran benefits and tax relief by location.")

@public_bp.get("/api/state-benefits")
def state_benefits_api():
    ok, msg = _ai_tool_allowed()
    if not ok:
        return jsonify({"error": msg}), 403
    state = (request.args.get("state") or "").strip().upper()
    if not state:
        return jsonify({"error": "Select a state."}), 400

    api_key = current_app.config.get("OPENAI_API_KEY")
    if not api_key:
        return jsonify({"error": "OPENAI_API_KEY is not configured."}), 400

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        model = current_app.config.get("OPENAI_MODEL", "gpt-4.1-mini")
        prompt = (
            "Return JSON only. Provide veteran state benefits for the given US state.\n"
            "Schema:\n"
            "{\n"
            "  \"state\": \"GA\",\n"
            "  \"categories\": [\n"
            "    {\"name\": \"Tax Relief\", \"programs\": [{\"title\": \"...\", \"benefit\": \"...\", \"eligibility\": \"...\", \"link\": \"...\"}]},\n"
            "    ...\n"
            "  ]\n"
            "}\n"
            "Include categories like: Tax Relief, Property Tax, Vehicle/Registration, Education/Tuition, "
            "Hunting/Fishing, Employment, Housing, Family/Survivor, Business/Entrepreneurship, Other.\n"
            "Be concise. If you are unsure about a link, use an empty string. No legal advice.\n"
            f"State: {state}\n"
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Return JSON only. No markdown."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        content = resp.choices[0].message.content or ""
        data = json.loads(content)
        return jsonify(data)
    except Exception as exc:
        return jsonify({"error": f"AI lookup failed: {exc}"}), 500

@public_bp.post("/api/intelligent-benefits")
@csrf.exempt
def intelligent_benefits_api():
    ok, msg = _ai_tool_allowed()
    if not ok:
        return jsonify({"error": msg}), 403
    payload = request.get_json(silent=True) or {}
    state = (payload.get("state") or "").strip().upper()
    city = (payload.get("city") or "").strip()
    if not state:
        z = (payload.get("zip") or "").strip()
        place = zip_to_place(z) if z else None
        state = (place.get("state") or "").strip().upper() if place else ""
    if not state and city:
        state = (payload.get("state") or "").strip().upper()
    if not state:
        return jsonify({"error": "State is required (or provide a ZIP)."}), 400

    api_key = current_app.config.get("OPENAI_API_KEY")
    if not api_key:
        return jsonify({"error": "OPENAI_API_KEY is not configured."}), 400

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        model = current_app.config.get("OPENAI_MODEL", "gpt-4.1-mini")
        prompt = (
            "Return JSON only. Evaluate benefits based on the veteran profile.\n"
            "Schema:\n"
            "{\n"
            "  \"state\": \"GA\",\n"
            "  \"likely\": [{\"title\": \"...\", \"summary\": \"...\", \"confidence\": 0-100, \"notes\": \"...\", \"verification\": \"VA-Verified|State-Verified|Nonprofit-Verified|AI-Discovered / Pending Review\", \"plain_english\": \"...\", \"what_it_means\": \"...\", \"what_it_does_not_affect\": \"...\"}],\n"
            "  \"conditional\": [{\"title\": \"...\", \"summary\": \"...\", \"confidence\": 0-100, \"notes\": \"...\", \"verification\": \"...\", \"plain_english\": \"...\", \"what_it_means\": \"...\", \"what_it_does_not_affect\": \"...\"}],\n"
            "  \"missed_hidden\": [{\"title\": \"...\", \"summary\": \"...\", \"confidence\": 0-100, \"notes\": \"...\", \"verification\": \"...\", \"plain_english\": \"...\", \"what_it_means\": \"...\", \"what_it_does_not_affect\": \"...\"}],\n"
            "  \"stacking\": [{\"title\": \"...\", \"status\": \"Allowed|Conflict\", \"details\": \"...\"}],\n"
            "  \"life_event_triggers\": [{\"event\": \"Marriage\", \"benefits\": [\"...\"]}],\n"
            "  \"timeline\": {\"now\": [\"...\"], \"one_year\": [\"...\"], \"five_years\": [\"...\"], \"retirement\": [\"...\"]},\n"
            "  \"disclaimer\": \"Short disclaimer text.\"\n"
            "}\n"
            "Consider federal, state, county, and nonprofit benefits. Include tax exemptions, fee waivers, "
            "dependent benefits, education, housing, employment, healthcare, mental health, transportation, "
            "and local assistance. Do not guarantee eligibility. Be concise and plain-English.\n"
            f"Profile: {json.dumps(payload, ensure_ascii=True)}\n"
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Return JSON only. No markdown."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        content = resp.choices[0].message.content or ""
        data = json.loads(content)
        if not data.get("state"):
            data["state"] = state
        return jsonify(data)
    except Exception as exc:
        return jsonify({"error": f"AI lookup failed: {exc}"}), 500

@public_bp.route("/veteran-upload", methods=["GET","POST"])
def veteran_upload():
    if request.method == "POST":
        f = request.files.get("file")
        email = request.form.get("email","").strip()
        full_name = request.form.get("full_name","").strip()
        phone = request.form.get("phone","").strip()
        if not f:
            return render_template("public/veteran_upload.html", error="No file provided")
        upload_dir = os.path.join(current_app.instance_path, "intakes")
        os.makedirs(upload_dir, exist_ok=True)
        fname = f"{datetime.datetime.utcnow().timestamp()}_{secure_filename(f.filename)}"
        path = os.path.join(upload_dir, fname)
        hasher = hashlib.sha256()
        with open(path, "wb") as out:
            while True:
                chunk = f.stream.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
                out.write(chunk)
        file_hash = hasher.hexdigest()
        from app.models.intake import IntakeRecord, IntakeMeta
        from app.extensions import SessionLocal
        db = SessionLocal()
        intake = IntakeRecord(email=email, filename=fname, file_hash=file_hash)
        db.add(intake); db.commit()
        meta = IntakeMeta(intake_id=intake.id, full_name=full_name, phone=phone)
        db.add(meta); db.commit()
        try:
            from app.services.email_service import send_email_html
            subject = "We received your documents"
            body_text = f"Thank you. We received your document upload. Intake ID: {intake.id}."
            body_html = f"<p>Thank you.</p><p>We received your document upload.</p><p><strong>Intake ID:</strong> {intake.id}</p>"
            send_email_html(email, subject, body_text, body_html, context="UPLOAD_RECEIPT")
        except Exception:
            pass
        return render_template("public/veteran_upload.html", success=True)
    return render_template("public/veteran_upload.html")


