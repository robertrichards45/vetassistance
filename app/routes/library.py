"""Condition library: a reference page per diagnostic code, the official section text, AI guides, and write-ups."""
from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime
from html.parser import HTMLParser

from flask import (Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request,
                   stream_with_context, url_for)
from flask_login import current_user, login_required

from app.extensions import SessionLocal
from app.models import Client, ConditionGuide, Letter
from app.models.user import Role
from app.routes._authz import require_roles
from app.services import condition_library as lib
from app.services.audit_service import log as audit_log
from app.services.case_stages import stage_label

library_bp = Blueprint("library", __name__, url_prefix="/library")
STAFF = (Role.DIRECTOR, Role.EMPLOYEE)


def _clients():
    q = SessionLocal().query(Client).filter(Client.org_id == current_user.org_id, Client.is_archived == False)  # noqa: E712
    if current_user.role != Role.DIRECTOR:
        q = q.filter(Client.assigned_user_id == current_user.id)
    return sorted(q.all(), key=lambda c: (c.last_name or "", c.first_name or ""))


@library_bp.get("")
@login_required
@require_roles(*STAFF)
def index():
    data = lib.library()
    systems = {}
    for c in data["conditions"]:
        systems.setdefault(c.get("system") or "Other", []).append(c)
    ordered = sorted(systems.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    db = SessionLocal()
    ready = {g.diagnostic_code for g in db.query(ConditionGuide).filter_by(status="READY").all()}
    sections = sorted({c["cfr_section"] for c in data["conditions"]}, key=lambda s: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)])
    return render_template("library/index.html", systems=ordered, total=len(data["conditions"]), ready=ready,
                           sections=sections, effective_date=data.get("effective_date"))


@library_bp.get("/<dc>")
@login_required
@require_roles(*STAFF)
def condition(dc: str):
    c = lib.condition(dc)
    if not c:
        abort(404)
    g = SessionLocal().query(ConditionGuide).filter_by(diagnostic_code=dc).first()
    guide = None
    if g and g.status == "READY" and g.content_json:
        try:
            guide = json.loads(g.content_json)
        except Exception:
            guide = None
    formulas = [lib.library()["_formulas"][n] for n in c.get("formula_names", []) if n in lib.library()["_formulas"]]
    return render_template("library/condition.html", c=c, formulas=formulas, guide=guide, guide_row=g,
                           rules=lib.rules_for(c.get("system", "")), general_rules=lib.GENERAL_RULES,
                           related=lib.related(c), clients=_clients(), kinds=lib.WRITEUP_KINDS,
                           ai_ok=bool(current_app.config.get("OPENAI_API_KEY")),
                           selected_client=request.args.get("client_id", ""))


@library_bp.post("/<dc>/guide")
@login_required
@require_roles(*STAFF)
def make_guide(dc: str):
    from app.services.queue import enqueue
    if not lib.condition(dc):
        abort(404)
    db = SessionLocal()
    g = db.query(ConditionGuide).filter_by(diagnostic_code=dc).first()
    if g and g.status in ("QUEUED", "RUNNING") and (datetime.utcnow() - g.updated_at).total_seconds() < 600:
        return redirect(url_for("library.condition", dc=dc))
    if g and g.status == "READY" and current_user.role != Role.DIRECTOR:
        flash("Only a director can regenerate a guide.", "error")
        return redirect(url_for("library.condition", dc=dc))
    if not g:
        g = ConditionGuide(diagnostic_code=dc)
        db.add(g)
    g.status, g.error, g.updated_at = "QUEUED", "", datetime.utcnow()
    db.commit()
    enqueue(lib.generate_guide, dc, queue="ai")
    return redirect(url_for("library.condition", dc=dc) + "#guide")


@library_bp.get("/<dc>/guide/status")
@login_required
@require_roles(*STAFF)
def guide_status(dc: str):
    g = SessionLocal().query(ConditionGuide).filter_by(diagnostic_code=dc).first()
    return jsonify({"status": g.status if g else None, "error": (g.error or "")[:300] if g else ""})


def _client_ctx(client_id: str) -> tuple[Client | None, str]:
    if not (client_id or "").isdigit():
        return None, ""
    c = SessionLocal().get(Client, int(client_id))
    if not c or c.org_id != current_user.org_id:
        return None, ""
    return c, json.dumps({"first_name": c.first_name, "branch": c.service_branch, "service": f"{c.service_entry_date} to {c.service_discharge_date}",
                          "current_combined_rating": c.va_rating_percent, "case_stage": stage_label(c.status)})


@library_bp.post("/<dc>/writeup")
@login_required
@require_roles(*STAFF)
def writeup(dc: str):
    c = lib.condition(dc)
    if not c:
        abort(404)
    key = current_app.config.get("OPENAI_API_KEY")
    if not key:
        return Response("AI isn't set up yet: add OPENAI_API_KEY in Railway's Variables.", mimetype="text/plain")
    client_row, ctx = _client_ctx(request.form.get("client_id") or "")
    msgs = lib.writeup_messages(c, request.form.get("kind") or "analysis", (request.form.get("target") or "").strip(),
                                (request.form.get("facts") or "").strip()[:8000], ctx)
    from openai import OpenAI
    ai = OpenAI(api_key=key, timeout=180, max_retries=1)
    model = current_app.config.get("OPENAI_MODEL") or "gpt-4.1-mini"

    def gen():
        try:
            for ev in ai.chat.completions.create(model=model, messages=msgs, stream=True):
                delta = ev.choices[0].delta.content if ev.choices else None
                if delta:
                    yield delta
        except Exception as exc:
            yield f"\n\nThe AI request failed: {exc}"

    return Response(stream_with_context(gen()), mimetype="text/plain", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@library_bp.post("/<dc>/save")
@login_required
@require_roles(*STAFF)
def save_writeup(dc: str):
    c = lib.condition(dc)
    client_row, _ = _client_ctx(request.form.get("client_id") or "")
    body = (request.form.get("body") or "").strip()
    if not c or not client_row or not body:
        return jsonify({"ok": False, "error": "Choose a client and write something first."}), 400
    kind = lib.WRITEUP_KINDS.get(request.form.get("kind") or "", ("Write-up", ""))[0]
    db = SessionLocal()
    letter = Letter(org_id=client_row.org_id, client_id=client_row.id, created_by_user_id=current_user.id,
                    title=f"{kind}: {c['condition']} (DC {dc})"[:250], body=body[:60000], is_visible_to_client=False)
    db.add(letter)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "WRITEUP_SAVED", "Client", client_row.id, detail=f"DC {dc}")
    return jsonify({"ok": True, "url": url_for("letters.staff_letters", client_id=client_row.id)})


# ---------------- Official section text ----------------

class _Clean(HTMLParser):
    """Keep only safe structural tags from the eCFR page; drop scripts, styles, attributes, and links."""
    KEEP = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tr", "td", "th", "em", "i", "b", "strong",
            "sup", "sub", "br", "ul", "ol", "li", "span"}
    DROP = {"script", "style", "head", "nav", "form", "button", "svg", "noscript", "iframe"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.DROP:
            self.skip += 1
        elif not self.skip and tag in self.KEEP:
            a = dict(attrs)
            extra = ""
            if tag in ("td", "th") and a.get("colspan", "").isdigit():
                extra = f' colspan="{a["colspan"]}"'
            if tag in ("td", "th") and a.get("align") == "right":
                extra += ' class="r"'
            self.out.append(f"<{tag}{extra}>")

    def handle_endtag(self, tag):
        if tag in self.DROP:
            self.skip = max(0, self.skip - 1)
        elif not self.skip and tag in self.KEEP and tag != "br":
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data))


def _section_html(sec: str) -> str | None:
    if not re.fullmatch(r"4\.\d+[a-z]?", sec):
        return None
    path = os.path.join(current_app.root_path, "..", "cfr_data", "ecfr_cache", f"section_{sec}.html")
    if not os.path.exists(path):
        return None
    raw = open(path, encoding="utf-8", errors="ignore").read()
    # Keep only the regulation itself: from the section heading to the page footer.
    start = raw.find("<h3>", raw.find('aria-label="breadcrumb"'))
    end = raw.find("<footer", start)
    if start > 0:
        raw = raw[start:end if end > start else None]
        raw = re.sub(r"<hr\s*/?>\s*<div class=\"container\">\s*$", "", raw)
    p = _Clean()
    p.feed(raw)
    out = "".join(p.out)
    # Anchor each diagnostic code row so condition pages can deep-link into the section.
    return re.sub(r"<td>(\d{4})(\s)", lambda m: f'<td id="dc-{m.group(1)}"><b>{m.group(1)}</b>{m.group(2)}', out)


@library_bp.get("/section/<sec>")
@login_required
@require_roles(*STAFF)
def section(sec: str):
    body = _section_html(sec)
    if body is None:
        abort(404)
    codes = [c for c in lib.library()["conditions"] if c["cfr_section"] == sec]
    return render_template("library/section.html", sec=sec, body=body, codes=codes,
                           system=codes[0].get("system") if codes else "")
