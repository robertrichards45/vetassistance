from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Tuple


def _smtp_settings() -> tuple[str, int, str, str, str]:
    host = os.environ.get("SMTP_HOST", "").strip()
    port = int((os.environ.get("SMTP_PORT", "587").strip() or "587"))
    user = os.environ.get("SMTP_USER", "").strip()
    pwd = os.environ.get("SMTP_PASS", "").strip()
    sender = (os.environ.get("SMTP_FROM", "") or user).strip()
    return host, port, user, pwd, sender


def send_email(to_email: str, subject: str, body: str, *, context: str = "", org_id: int | None = None, actor_user_id: int | None = None) -> Tuple[bool, str]:
    host, port, user, pwd, sender = _smtp_settings()
    if not host or not user or not pwd or not sender:
        _log_email_attempt(to_email, subject, False, "SMTP not configured", context, org_id, actor_user_id)
        return False, "SMTP not configured"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.starttls()
            s.login(user, pwd)
            s.send_message(msg)
        _log_email_attempt(to_email, subject, True, "", context, org_id, actor_user_id)
        return True, "sent"
    except Exception as e:
        _log_email_attempt(to_email, subject, False, str(e), context, org_id, actor_user_id)
        return False, f"SMTP send failed: {e}"


def send_email_html(to_email: str, subject: str, body_text: str, body_html: str, *, context: str = "", org_id: int | None = None, actor_user_id: int | None = None) -> Tuple[bool, str]:
    host, port, user, pwd, sender = _smtp_settings()
    if not host or not user or not pwd or not sender:
        _log_email_attempt(to_email, subject, False, "SMTP not configured", context, org_id, actor_user_id)
        return False, "SMTP not configured"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text)
    msg.add_alternative(body_html, subtype="html")

    try:
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.starttls()
            s.login(user, pwd)
            s.send_message(msg)
        _log_email_attempt(to_email, subject, True, "", context, org_id, actor_user_id)
        return True, "sent"
    except Exception as e:
        _log_email_attempt(to_email, subject, False, str(e), context, org_id, actor_user_id)
        return False, f"SMTP send failed: {e}"


def build_portal_invite(site_name: str, support_email: str, support_phone: str, login_url: str, user_email: str, temp_password: str) -> tuple[str, str, str]:
    subject = f"{site_name} – Your Client Portal Login"
    text_body = f"""Welcome to {site_name}!

Your client portal is ready.

Login link: {login_url}
Email: {user_email}
Temporary Password: {temp_password}

For security, you will be prompted to reset your password after you sign in.

Need help?
Email: {support_email}
Phone: {support_phone}
"""
    html_body = f"""<!doctype html>
<html><body style='font-family:Arial,sans-serif;'>
  <h2>{site_name}</h2>
  <p>Your client portal is ready.</p>
  <ul>
    <li><strong>Login link:</strong> <a href='{login_url}'>{login_url}</a></li>
    <li><strong>Email:</strong> {user_email}</li>
    <li><strong>Temporary Password:</strong> {temp_password}</li>
  </ul>
  <p style='font-size:12px;color:#555'>Need help? {support_email} | {support_phone}</p>
</body></html>"""
    return subject, text_body, html_body


def _log_email_attempt(to_email: str, subject: str, ok: bool, error: str, context: str = "", org_id: int | None = None, actor_user_id: int | None = None) -> None:
    try:
        from app.extensions import SessionLocal
        from app.models.email_log import EmailLog
        db = SessionLocal()
        row = EmailLog(org_id=org_id, actor_user_id=actor_user_id, to_email=to_email, subject=subject, ok=ok, error=error or "", context=context or "")
        db.add(row)
        db.commit()
    except Exception:
        pass
