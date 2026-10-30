"""Notifications: an in-app alert (the bell) plus a branded email when possible.

Every function here is best-effort and never raises; a failed email or alert
must never break the action that triggered it.
"""
from __future__ import annotations

import html
import logging
import os
import uuid
from datetime import datetime

from flask import current_app, request, url_for

from app.extensions import SessionLocal
from app.models import Alert, User, Client
from app.models.user import Role

log = logging.getLogger("vetassistance")


def _abs(link: str) -> str:
    if not link or link.startswith("http"):
        return link
    try:
        base = (os.environ.get("PUBLIC_BASE_URL") or request.url_root).rstrip("/")
    except Exception:
        base = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    return base + link


def _email_html(title: str, body: str, link: str, cta: str) -> str:
    brand = current_app.config.get("SITE_NAME", "Veteran Benefits Assistance")
    button = (f'<p style="margin:24px 0"><a href="{html.escape(link)}" style="background:#14325A;color:#fff;'
              f'padding:12px 20px;border-radius:6px;text-decoration:none;font-weight:700;display:inline-block">{html.escape(cta)}</a></p>'
              if link else "")
    return f"""<!doctype html><html><body style="margin:0;background:#F4F6FA;font-family:Segoe UI,Arial,sans-serif;color:#102033">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:28px 12px">
<table role="presentation" width="100%" style="max-width:560px;background:#fff;border:1px solid #DCE3EC;border-radius:10px" cellpadding="0" cellspacing="0">
<tr><td style="padding:18px 24px;border-bottom:4px solid #F2C14E;background:#14325A;border-radius:10px 10px 0 0;color:#fff;font-weight:700;font-size:16px">{html.escape(brand)}</td></tr>
<tr><td style="padding:24px">
<h1 style="font-size:20px;margin:0 0 10px">{html.escape(title)}</h1>
<p style="margin:0;line-height:1.6;color:#4A5D73">{html.escape(body).replace(chr(10), '<br>')}</p>
{button}
<p style="margin:24px 0 0;font-size:12px;color:#7A8A9C">For your privacy, details stay in your secure account. If you're in crisis, call 988 and press 1.</p>
</td></tr></table></td></tr></table></body></html>"""


def notify_user(user_id: int | None, title: str, body: str = "", link: str = "", *,
                kind: str = "system", email: str | None = None, cta: str = "Open", org_id: int | None = None,
                actor_user_id: int | None = None) -> None:
    """Create an in-app notification for a user and optionally email them."""
    if user_id:
        db = SessionLocal()
        try:
            db.add(Alert(user_id=user_id, type=kind[:40], source_key=f"{kind}:{uuid.uuid4().hex}",
                         title=title[:255], body=(body or "")[:2000], link=link or "", created_at=datetime.utcnow()))
            db.commit()
        except Exception as exc:  # pragma: no cover
            db.rollback()
            log.warning("notify: alert failed: %s", exc)
    if email:
        try:
            from app.services.email_service import send_email_html
            send_email_html(email.strip().lower(), title, f"{body}\n\n{_abs(link)}".strip(),
                            _email_html(title, body, _abs(link), cta), context=f"NOTIFY_{kind.upper()}",
                            org_id=org_id, actor_user_id=actor_user_id, background=True)
        except Exception as exc:  # pragma: no cover
            log.warning("notify: email failed: %s", exc)


def notify_client(client: Client, title: str, body: str = "", link: str = "", *, kind: str = "update",
                  cta: str = "Open your portal", actor_user_id: int | None = None) -> None:
    if not client:
        return
    link = link or _safe_url("portal.dashboard")
    email = client.email if (client.email and client.portal_enabled) else None
    notify_user(client.portal_user_id, title, body, link, kind=kind, email=email, cta=cta,
                org_id=client.org_id, actor_user_id=actor_user_id)


def notify_team_for_client(client: Client, title: str, body: str = "", link: str = "", *, kind: str = "client",
                           cta: str = "Open", actor_user_id: int | None = None, email: bool = True) -> None:
    """Notify the client's assigned rep, or every director when no rep is assigned."""
    if not client:
        return
    db = SessionLocal()
    try:
        users = []
        if client.assigned_user_id:
            rep = db.get(User, client.assigned_user_id)
            if rep and rep.is_active:
                users.append(rep)
        if not users:
            users = db.query(User).filter(User.org_id == client.org_id, User.role == Role.DIRECTOR,
                                          User.is_active.is_(True)).all()
        targets = [(u.id, u.email) for u in users if u.id != actor_user_id]
    finally:
        pass
    link = link or _safe_url("clients.view_client", client_id=client.id)
    for uid, mail in targets:
        notify_user(uid, title, body, link, kind=kind, email=mail if email else None, cta=cta,
                    org_id=client.org_id, actor_user_id=actor_user_id)


def _safe_url(endpoint: str, **values) -> str:
    try:
        return url_for(endpoint, **values)
    except Exception:
        return ""
