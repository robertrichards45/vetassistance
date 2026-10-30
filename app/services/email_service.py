from __future__ import annotations

import logging
import os
import smtplib
import ssl
import threading
from email.message import EmailMessage
from typing import Tuple

log = logging.getLogger("vetassistance")

# Email can go out three ways. The first one configured wins:
#   1. Resend   (RESEND_API_KEY)    - HTTPS API, works on every Railway plan
#   2. SendGrid (SENDGRID_API_KEY)  - HTTPS API, works on every Railway plan
#   3. SMTP     (SMTP_HOST/USER/PASS) - Railway only allows outbound SMTP on Pro plans
# The sender address comes from EMAIL_FROM, then SMTP_FROM, then SMTP_USER.


def _sender() -> str:
    return (os.environ.get("EMAIL_FROM") or os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or "").strip()


def _smtp_settings() -> tuple[str, int, str, str, str]:
    host = os.environ.get("SMTP_HOST", "").strip()
    port = int((os.environ.get("SMTP_PORT", "587").strip() or "587"))
    user = os.environ.get("SMTP_USER", "").strip()
    pwd = os.environ.get("SMTP_PASS", "").strip()
    return host, port, user, pwd, _sender()


def email_status() -> dict:
    """What is configured (never returns secrets)."""
    host, port, user, pwd, sender = _smtp_settings()
    if os.environ.get("RESEND_API_KEY", "").strip():
        provider = "Resend"
    elif os.environ.get("SENDGRID_API_KEY", "").strip():
        provider = "SendGrid"
    elif host and user and pwd:
        provider = "SMTP"
    else:
        provider = ""
    return {
        "provider": provider,
        "sender": sender,
        "smtp_host": host,
        "smtp_port": port,
        "smtp_user": user,
        "missing_sender": bool(provider) and not sender,
        "on_railway": bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID")),
    }


def _send_resend(to_email, subject, text, html, sender) -> Tuple[bool, str]:
    import requests
    r = requests.post("https://api.resend.com/emails", timeout=15,
                      headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY'].strip()}"},
                      json={"from": sender, "to": [to_email], "subject": subject, "text": text, **({"html": html} if html else {})})
    if r.status_code < 300:
        return True, "sent"
    return False, f"Resend error {r.status_code}: {r.text[:300]}"


def _send_sendgrid(to_email, subject, text, html, sender) -> Tuple[bool, str]:
    import requests
    content = [{"type": "text/plain", "value": text or " "}]
    if html:
        content.append({"type": "text/html", "value": html})
    r = requests.post("https://api.sendgrid.com/v3/mail/send", timeout=15,
                      headers={"Authorization": f"Bearer {os.environ['SENDGRID_API_KEY'].strip()}"},
                      json={"personalizations": [{"to": [{"email": to_email}]}], "from": {"email": sender},
                            "subject": subject, "content": content})
    if r.status_code < 300:
        return True, "sent"
    return False, f"SendGrid error {r.status_code}: {r.text[:300]}"


def _send_smtp(to_email, subject, text, html, sender) -> Tuple[bool, str]:
    host, port, user, pwd, _ = _smtp_settings()
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(text or " ")
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=12, context=ssl.create_default_context()) as s:
                s.login(user, pwd)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=12) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(user, pwd)
                s.send_message(msg)
        return True, "sent"
    except (TimeoutError, OSError) as e:
        hint = (" Railway only allows outbound SMTP on Pro plans; use RESEND_API_KEY or SENDGRID_API_KEY instead."
                if os.environ.get("RAILWAY_ENVIRONMENT") else "")
        if isinstance(e, smtplib.SMTPAuthenticationError):
            return False, f"SMTP login rejected: {e}. For Gmail, use a 16-character App Password, not your normal password."
        return False, f"SMTP connection failed ({type(e).__name__}: {e}).{hint}"
    except smtplib.SMTPAuthenticationError as e:
        return False, f"SMTP login rejected: {e}. For Gmail, use a 16-character App Password, not your normal password."
    except Exception as e:
        return False, f"SMTP send failed: {e}"


def _deliver(to_email: str, subject: str, text: str, html: str) -> Tuple[bool, str]:
    st = email_status()
    if not st["provider"]:
        return False, "Email is not configured (add RESEND_API_KEY + EMAIL_FROM in Railway variables)"
    if not st["sender"]:
        return False, "Missing sender address (set EMAIL_FROM)"
    try:
        if st["provider"] == "Resend":
            return _send_resend(to_email, subject, text, html, st["sender"])
        if st["provider"] == "SendGrid":
            return _send_sendgrid(to_email, subject, text, html, st["sender"])
        return _send_smtp(to_email, subject, text, html, st["sender"])
    except Exception as e:
        return False, f"{st['provider']} send failed: {e}"


def _send(to_email, subject, text, html, context, org_id, actor_user_id, background) -> Tuple[bool, str]:
    to_email = (to_email or "").strip()
    if not to_email:
        return False, "No recipient"
    if not background:
        ok, msg = _deliver(to_email, subject, text, html)
        _log_email_attempt(to_email, subject, ok, "" if ok else msg, context, org_id, actor_user_id)
        return ok, msg

    def run():
        ok, msg = _deliver(to_email, subject, text, html)
        _log_email_attempt(to_email, subject, ok, "" if ok else msg, context, org_id, actor_user_id, own_session=True)
        if not ok:
            log.warning("email to %s failed: %s", to_email, msg)
    threading.Thread(target=run, name="email-send", daemon=True).start()
    return True, "queued"


def send_email(to_email: str, subject: str, body: str, *, context: str = "", org_id: int | None = None,
               actor_user_id: int | None = None, background: bool = False) -> Tuple[bool, str]:
    return _send(to_email, subject, body, "", context, org_id, actor_user_id, background)


def send_email_html(to_email: str, subject: str, body_text: str, body_html: str, *, context: str = "",
                    org_id: int | None = None, actor_user_id: int | None = None, background: bool = False) -> Tuple[bool, str]:
    return _send(to_email, subject, body_text, body_html, context, org_id, actor_user_id, background)


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


def _log_email_attempt(to_email: str, subject: str, ok: bool, error: str, context: str = "", org_id: int | None = None,
                       actor_user_id: int | None = None, own_session: bool = False) -> None:
    try:
        from app.extensions import SessionLocal
        from app.models.email_log import EmailLog
        db = SessionLocal.session_factory() if own_session else SessionLocal()
        try:
            db.add(EmailLog(org_id=org_id, actor_user_id=actor_user_id, to_email=to_email, subject=subject, ok=ok,
                            error=error or "", context=context or ""))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            if own_session:
                db.close()
    except Exception:
        pass
