"""Free public tools: decision letter explainer, Intent to File reminders, C&P exam prep sheet."""
from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from app.extensions import limiter

tools_bp = Blueprint("tools", __name__, url_prefix="/tools")


@tools_bp.get("/decision-letter")
def decision_letter():
    return render_template("tools/decision_letter.html", result=None,
                           meta_title="VA Decision Letter Explained (Free Tool)",
                           meta_description="Upload or paste your VA decision letter and get a plain-English summary of what was granted, denied and why, plus your options and deadline. Nothing is saved.")


@tools_bp.post("/decision-letter")
@limiter.limit("5 per hour; 15 per day")
def decision_letter_post():
    from app.services import decision_letter as dl
    from app.services.claim_builder import _ai_json
    if not current_app.config.get("OPENAI_API_KEY"):
        flash("The explainer is temporarily unavailable. Please try again later.", "error")
        return redirect(url_for("tools.decision_letter"))
    text = (request.form.get("letter_text") or "").strip()
    f = request.files.get("letter_file")
    if f and f.filename:
        data = f.read(12 * 1024 * 1024 + 1)
        if len(data) > 12 * 1024 * 1024:
            flash("That file is larger than 12 MB. Try pasting the text instead.", "error")
            return redirect(url_for("tools.decision_letter"))
        try:
            text = dl.extract_text(f.filename, data)
        except Exception:
            flash("We couldn't read that file. If it's a scan or photo, paste the letter's text instead.", "error")
            return redirect(url_for("tools.decision_letter"))
    text = dl.clean_text(text)
    if not dl.looks_like_decision(text):
        flash("That doesn't look like a VA decision letter, or the file is a scan we can't read. Paste the letter's text and try again.", "error")
        return redirect(url_for("tools.decision_letter"))
    try:
        result = dl.explain(text, _ai_json)
    except Exception:
        current_app.logger.exception("decision letter explain failed")
        flash("We couldn't explain that letter right now. Please try again in a few minutes.", "error")
        return redirect(url_for("tools.decision_letter"))
    return render_template("tools/decision_letter.html", result=result,
                           meta_title="Your VA Decision Letter, Explained", meta_description="")


@tools_bp.get("/intent-to-file")
def intent_to_file():
    return render_template("tools/intent_to_file.html", meta_title="Intent to File: Protect Your VA Back Pay (Free Reminders)",
                           meta_description="An Intent to File holds your VA back-pay start date for one year. Learn how it works and get free email reminders before it expires.")


@tools_bp.post("/intent-to-file")
@limiter.limit("5 per hour")
def intent_to_file_post():
    from datetime import date, datetime
    from app.services import itf_reminders
    from app.services.email_service import send_email
    email = (request.form.get("email") or "").strip()
    try:
        filed_on = datetime.strptime(request.form.get("filed_on") or "", "%Y-%m-%d").date()
    except ValueError:
        filed_on = None
    if "@" not in email or "." not in email.split("@")[-1] or not filed_on:
        flash("Enter your email and the date you submitted your Intent to File.", "error")
        return redirect(url_for("tools.intent_to_file"))
    if filed_on > date.today() or itf_reminders.deadline(filed_on) <= date.today():
        flash("That date is in the future or more than a year ago. An Intent to File only lasts one year.", "error")
        return redirect(url_for("tools.intent_to_file"))
    base = request.url_root.rstrip("/")
    itf_reminders.create(email, filed_on, base, lambda to, subj, body: send_email(to, subj, body, context="ITF_REMINDER", background=True))
    flash(f"Reminders set. We'll email you before {itf_reminders.deadline(filed_on):%B %d, %Y}. Check your inbox for a confirmation.", "success")
    return redirect(url_for("tools.intent_to_file"))


@tools_bp.get("/intent-to-file/stop/<token>")
def intent_to_file_stop(token: str):
    from app.services import itf_reminders
    ok = itf_reminders.stop(token[:64])
    flash("Reminders stopped. You won't get any more emails about this." if ok else "That link didn't work. Contact us and we'll stop them.", "success" if ok else "error")
    return redirect(url_for("tools.intent_to_file"))


@tools_bp.get("/cp-exam-prep")
def cp_exam_prep():
    from app.services.cp_prep import COMMON_BRING, COMMON_TIPS, SHEETS
    key = request.args.get("condition", "")
    sheet = SHEETS.get(key)
    return render_template("tools/cp_exam_prep.html", sheets=SHEETS, key=key if sheet else "", sheet=sheet,
                           bring=COMMON_BRING, tips=COMMON_TIPS,
                           meta_title=(f"C&P Exam Prep Sheet: {sheet['title']}" if sheet else "Free C&P Exam Prep Sheet"),
                           meta_description="A free printable checklist for your VA C&P exam: what the examiner measures, what to describe, and what to bring.")
