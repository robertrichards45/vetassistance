"""Ask AI: a general claims assistant for staff, with optional client context.

Conversation history is kept per staff member (last 30 messages) in
users.preferences_json so it survives page loads and logins.
"""
from __future__ import annotations

import json

from flask import Blueprint, Response, current_app, flash, redirect, render_template, request, stream_with_context, url_for
from flask_login import current_user, login_required

from app.extensions import SessionLocal
from app.models import Client, FormData, User, AIRun
from app.models.user import Role
from app.routes._authz import require_roles
from app.services.case_stages import stage_label

assistant_bp = Blueprint("assistant", __name__, url_prefix="/assistant")

SYSTEM_PROMPT = (
    "You are the in-house assistant for a veteran-run team that helps veterans organize VA disability claims. "
    "Answer the team member's question with practical, accurate guidance: what evidence to gather, which VA form or "
    "review lane fits, how the rating criteria in 38 CFR Part 4 apply, deadlines, and next steps. "
    "Use plain language and short bullet points. Cite the regulation or VA form number when it helps. "
    "Never guarantee an outcome, never give legal advice, and say so briefly when a question needs an accredited "
    "representative or attorney. If you are not sure, say what to verify and where (VA.gov, the eCFR, M21-1)."
)
MAX_HISTORY = 30


def _history(user: User) -> list[dict]:
    try:
        prefs = json.loads(user.preferences_json or "{}")
        hist = prefs.get("assistant_history") or []
        return [m for m in hist if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    except Exception:
        return []


def _save_history(user_id: int, hist: list[dict]) -> None:
    db = SessionLocal()
    u = db.get(User, user_id)
    if not u:
        return
    try:
        prefs = json.loads(u.preferences_json or "{}")
    except Exception:
        prefs = {}
    prefs["assistant_history"] = hist[-MAX_HISTORY:]
    u.preferences_json = json.dumps(prefs)
    db.commit()


def _client_context(client_id: str) -> tuple[Client | None, str]:
    if not (client_id or "").isdigit():
        return None, ""
    db = SessionLocal()
    c = db.get(Client, int(client_id))
    if not c or c.org_id != current_user.org_id:
        return None, ""
    intake = {}
    fd = db.query(FormData).filter_by(org_id=c.org_id, client_id=c.id, form_key="INTAKE_V1").first()
    if fd and fd.data_json:
        try:
            raw = json.loads(fd.data_json)
            intake = {k: raw.get(k) for k in ("issues", "deadlines", "current_rating", "branch", "service_dates") if raw.get(k)}
        except Exception:
            intake = {}
    summary = ""
    run = (db.query(AIRun).filter_by(org_id=c.org_id, client_id=c.id, status="COMPLETED")
           .order_by(AIRun.created_at.desc()).first())
    if run and run.result_json:
        try:
            summary = (json.loads(run.result_json) or {}).get("summary", "")
        except Exception:
            summary = ""
    ctx = {
        "client_first_name": c.first_name,
        "service_branch": c.service_branch,
        "service_entry_date": c.service_entry_date,
        "service_discharge_date": c.service_discharge_date,
        "case_stage": stage_label(c.status),
        "current_va_rating_percent": c.va_rating_percent,
        "retirement_status": c.retirement_status,
        "intake": intake,
        "latest_ai_case_summary": summary,
    }
    return c, json.dumps(ctx, ensure_ascii=True)[:6000]


def _build_messages(question: str, hist: list[dict], ctx: str) -> list[dict]:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    if ctx:
        msgs.append({"role": "system", "content": "The question is about this client (no SSN or contact details included):\n" + ctx})
    for m in hist[-10:]:
        msgs.append({"role": m["role"], "content": (m.get("content") or "")[:4000]})
    msgs.append({"role": "user", "content": question[:4000]})
    return msgs


def _openai():
    key = current_app.config.get("OPENAI_API_KEY")
    if not key:
        return None, None
    from openai import OpenAI
    return OpenAI(api_key=key), current_app.config.get("OPENAI_MODEL") or "gpt-4.1-mini"


@assistant_bp.get("")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def index():
    db = SessionLocal()
    me = db.get(User, current_user.id)
    q = db.query(Client).filter(Client.org_id == current_user.org_id, Client.is_archived == False)  # noqa: E712
    if current_user.role != Role.DIRECTOR:
        q = q.filter(Client.assigned_user_id == current_user.id)
    clients = sorted(q.all(), key=lambda c: (c.last_name or "", c.first_name or ""))
    return render_template("assistant/index.html", history=_history(me), clients=clients,
                           selected_client=request.args.get("client_id", ""),
                           configured=bool(current_app.config.get("OPENAI_API_KEY")))


@assistant_bp.post("/stream")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def stream():
    question = (request.form.get("question") or "").strip()
    if not question:
        return Response("Type a question first.", mimetype="text/plain")
    client, model = _openai()
    if not client:
        return Response("Ask AI isn't set up yet: add OPENAI_API_KEY in Railway's variables.", mimetype="text/plain")
    db = SessionLocal()
    me = db.get(User, current_user.id)
    hist = _history(me)
    c, ctx = _client_context(request.form.get("client_id") or "")
    msgs = _build_messages(question, hist, ctx)
    uid = current_user.id
    label = f" (about {c.display_name()})" if c else ""

    def gen():
        parts = []
        try:
            for ev in client.chat.completions.create(model=model, messages=msgs, stream=True):
                delta = ev.choices[0].delta.content if ev.choices else None
                if delta:
                    parts.append(delta)
                    yield delta
        except Exception as exc:  # surface the reason instead of a blank bubble
            msg = f"\n\nThe AI request failed: {exc}"
            parts.append(msg)
            yield msg
        finally:
            answer = "".join(parts).strip()
            if answer:
                _save_history(uid, hist + [{"role": "user", "content": question + label},
                                           {"role": "assistant", "content": answer}])

    return Response(stream_with_context(gen()), mimetype="text/plain", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@assistant_bp.post("/ask")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def ask():
    """No-JavaScript fallback (and the target of the quick-ask box on Today)."""
    question = (request.form.get("question") or "").strip()
    client_id = request.form.get("client_id") or ""
    if not question:
        flash("Type a question first.", "error")
        return redirect(url_for("assistant.index"))
    client, model = _openai()
    if not client:
        flash("Ask AI isn't set up yet: add OPENAI_API_KEY in Railway's variables.", "error")
        return redirect(url_for("assistant.index"))
    db = SessionLocal()
    me = db.get(User, current_user.id)
    hist = _history(me)
    c, ctx = _client_context(client_id)
    try:
        resp = client.chat.completions.create(model=model, messages=_build_messages(question, hist, ctx))
        answer = (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        flash(f"The AI request failed: {exc}", "error")
        return redirect(url_for("assistant.index"))
    label = f" (about {c.display_name()})" if c else ""
    _save_history(current_user.id, hist + [{"role": "user", "content": question + label},
                                           {"role": "assistant", "content": answer}])
    return redirect(url_for("assistant.index", client_id=client_id or None) + "#end")


@assistant_bp.post("/clear")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def clear():
    _save_history(current_user.id, [])
    flash("Conversation cleared.", "success")
    return redirect(url_for("assistant.index"))
