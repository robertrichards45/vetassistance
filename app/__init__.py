import os
from types import SimpleNamespace
from datetime import timezone
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from flask import Flask, url_for, request
from flask_login import current_user
from hashlib import sha256
from sqlalchemy import inspect, text
from werkzeug.routing import BuildError
from werkzeug.middleware.proxy_fix import ProxyFix
import logging
from app.config import Config
from app.extensions import login_manager, csrf, limiter, init_db, SessionLocal
from app.models import User, Base, PageView
from app.models.user import Role


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    from app.services.ai_compat import install as _ai_compat
    _ai_compat()

    # Railway terminates HTTPS at its edge proxy. Trust one hop of X-Forwarded-*
    # headers so request.scheme is https (canonical/OG URLs, secure redirects)
    # and request.remote_addr is the visitor's IP (rate limiting per visitor).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    log = logging.getLogger("vetassistance")
    if not app.debug:
        if app.config.get("SECRET_KEY") in ("", "dev-secret-key-change-me"):
            log.error("SECURITY: SECRET_KEY is not set in the environment. Set a long random SECRET_KEY in Railway.")
        if not os.environ.get("SSN_ENCRYPTION_KEY", "").strip():
            log.error("SECURITY: SSN_ENCRYPTION_KEY is not set. SSNs entered by clients cannot be saved until it is.")

    engine = init_db(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    csrf.init_app(app)
    if limiter is not None:
        limiter.init_app(app)

    def _ensure_column(table: str, column: str, ddl: str) -> None:
        try:
            cols = {c.get("name") for c in inspect(engine).get_columns(table)}
        except Exception:
            return
        if column in cols:
            return
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        except Exception:
            return

    _ensure_column("users", "notes", "TEXT DEFAULT ''")
    _ensure_column("users", "preferences_json", "TEXT DEFAULT '{}'")
    _ensure_column("resources", "county", "TEXT DEFAULT ''")
    _ensure_column("resources", "verification_status", "TEXT DEFAULT 'AI-Discovered / Pending Review'")
    _ensure_column("documents", "is_client_visible", "BOOLEAN DEFAULT 0")
    _ensure_column("client_tasks", "instructions", "TEXT DEFAULT ''")
    _ensure_column("client_tasks", "due_date", "TEXT")
    _ensure_column("clients", "status", "TEXT DEFAULT 'Intake Received'")
    _ensure_column("clients", "claim_number", "TEXT DEFAULT ''")
    _ensure_column("clients", "full_legal_name", "TEXT DEFAULT ''")
    _ensure_column("clients", "ssn_full", "TEXT DEFAULT ''")
    _ensure_column("clients", "ssn_encrypted", "TEXT DEFAULT ''")
    _ensure_column("clients", "ssn_last4", "TEXT DEFAULT ''")
    _ensure_column("clients", "dob", "TEXT DEFAULT ''")
    _ensure_column("clients", "mailing_address1", "TEXT DEFAULT ''")
    _ensure_column("clients", "mailing_address2", "TEXT DEFAULT ''")
    _ensure_column("clients", "mailing_city", "TEXT DEFAULT ''")
    _ensure_column("clients", "mailing_state", "TEXT DEFAULT ''")
    _ensure_column("clients", "mailing_zip", "TEXT DEFAULT ''")
    _ensure_column("clients", "service_entry_date", "TEXT DEFAULT ''")
    _ensure_column("clients", "service_discharge_date", "TEXT DEFAULT ''")
    _ensure_column("clients", "service_branch", "TEXT DEFAULT ''")
    _ensure_column("clients", "referral_source", "TEXT DEFAULT ''")
    _ensure_column("clients", "va_rating_percent", "INTEGER DEFAULT 0")
    _ensure_column("clients", "retirement_status", "TEXT DEFAULT ''")
    _ensure_column("client_payments", "email_sent", "BOOLEAN DEFAULT 0")
    _ensure_column("client_payments", "invoice_id", "TEXT DEFAULT ''")
    _ensure_column("client_payments", "invoice_url", "TEXT DEFAULT ''")
    _ensure_column("public_comments", "is_rejected", "BOOLEAN DEFAULT 0")
    _ensure_column("users", "last_login_at", "TEXT")
    _ensure_column("va_call_logs", "call_type", "TEXT DEFAULT ''")
    _ensure_column("va_call_logs", "topic", "TEXT DEFAULT ''")
    _ensure_column("rendered_artifacts", "pdf_path", "TEXT DEFAULT ''")
    _ensure_column("client_notes", "tags", "TEXT DEFAULT ''")
    _ensure_column("client_tasks", "kind", "TEXT DEFAULT 'task'")
    _ensure_column("alerts", "link", "TEXT DEFAULT ''")

    try:
        Base.metadata.create_all(bind=engine)
    except Exception:
        pass

    @app.before_request
    def require_initial_password_change():
        # A temporary password is a one-time login credential, not a full session.
        # Keep static assets and the change-password/logout routes accessible.
        if (current_user.is_authenticated and getattr(current_user, "must_reset_password", False)
                and request.endpoint not in ("auth.change_password", "auth.change_password_post",
                                             "auth.logout", "static")
                and not (request.path or "").startswith("/static/")):
            from flask import redirect
            return redirect(url_for("auth.change_password"))

    @app.before_request
    def require_verified_client_email():
        # A self-registered client must prove mailbox control before case access.
        # The pending dashboard only exposes verification instructions.
        if (current_user.is_authenticated and current_user.role == Role.CLIENT
                and request.endpoint not in ("portal.dashboard", "portal.verify_email",
                                             "portal.resend_verification", "auth.logout", "static")
                and not (request.path or "").startswith("/static/")):
            from app.models import Client
            db = SessionLocal()
            client = db.query(Client).filter_by(org_id=current_user.org_id,
                                                portal_user_id=current_user.id).first()
            if client and client.email_verified is False:
                from flask import redirect
                return redirect(url_for("portal.dashboard"))

    @app.before_request
    def track_page_view():
        try:
            if request.method != "GET":
                return
            path = request.path or ""
            if path.startswith("/static/") or path.startswith("/api/") or path.startswith("/favicon"):
                return
            # Do not count internal staff activity (build/test sessions)
            if current_user.is_authenticated and current_user.role in (Role.DIRECTOR, Role.EMPLOYEE):
                return
            ip = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if not ip:
                ip = request.remote_addr or ""
            if not ip:
                return
            salt = app.config.get("SECRET_KEY", "")
            ip_hash = sha256((ip + salt).encode("utf-8")).hexdigest()
            ua = (request.headers.get("User-Agent") or "")[:300]
            org_id = current_user.org_id if current_user.is_authenticated else None
            user_id = current_user.id if current_user.is_authenticated else None
            db = SessionLocal()
            try:
                db.add(PageView(
                    org_id=org_id,
                    user_id=user_id,
                    path=path[:255],
                    method=request.method,
                    ip_hash=ip_hash,
                    user_agent=ua,
                ))
                db.commit()
            finally:
                db.close()
        except Exception:
            return

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(self)")
        if not app.debug and request.is_secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        # Logged-in pages can show personal data; never let shared caches keep them.
        if current_user.is_authenticated:
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @login_manager.user_loader
    def load_user(user_id):
        db = SessionLocal()
        try:
            return db.get(User, int(user_id))
        except Exception:
            return None
        finally:
            db.close()

    @app.teardown_appcontext
    def remove_session(exception=None):
        SessionLocal.remove()

    # Blueprint registration
    from app.routes.root import root_bp
    app.register_blueprint(root_bp)

    from app.routes.public import public_bp
    app.register_blueprint(public_bp)

    from app.routes.onboarding_public import onboard_bp
    app.register_blueprint(onboard_bp)

    from app.routes.auth import auth_bp
    app.register_blueprint(auth_bp)

    from app.routes.clients import clients_bp
    app.register_blueprint(clients_bp)

    from app.routes.employee import employee_bp
    app.register_blueprint(employee_bp)

    from app.routes.intakes import intakes_bp
    app.register_blueprint(intakes_bp)

    from app.routes.email_logs import email_logs_bp
    app.register_blueprint(email_logs_bp)

    from app.routes.billing import billing_bp
    app.register_blueprint(billing_bp)

    from app.routes.evidence_tags import tags_bp
    app.register_blueprint(tags_bp)

    from app.routes.metrics import metrics_bp
    app.register_blueprint(metrics_bp)

    from app.routes.resources_locator import resources_bp
    app.register_blueprint(resources_bp)

    from app.routes.pricing import pricing_bp
    app.register_blueprint(pricing_bp)

    from app.routes.ai import ai_bp
    app.register_blueprint(ai_bp)

    from app.routes.director import director_bp
    app.register_blueprint(director_bp)

    from app.routes.site_admin import site_admin_bp
    app.register_blueprint(site_admin_bp)

    from app.routes.invites import invites_bp
    app.register_blueprint(invites_bp)

    from app.routes.messages import messages_bp
    app.register_blueprint(messages_bp)

    from app.routes.messages_api import messages_api_bp
    app.register_blueprint(messages_api_bp)

    from app.routes.alerts import alerts_bp
    app.register_blueprint(alerts_bp)

    from app.routes.employee_api import employee_api_bp
    app.register_blueprint(employee_api_bp)

    from app.routes.documents import documents_bp
    app.register_blueprint(documents_bp)

    from app.routes.letters import letters_bp
    app.register_blueprint(letters_bp)

    from app.routes.forms import forms_bp
    app.register_blueprint(forms_bp)

    from app.routes.tasks import tasks_bp
    app.register_blueprint(tasks_bp)

    from app.routes.nexus import nexus_bp
    app.register_blueprint(nexus_bp)

    from app.routes.artifacts import artifacts_bp
    app.register_blueprint(artifacts_bp)

    from app.routes.templates import templates_bp
    app.register_blueprint(templates_bp)

    from app.routes.agreements import agreements_bp
    app.register_blueprint(agreements_bp)

    from app.routes.portal import portal_bp
    app.register_blueprint(portal_bp)

    from app.routes.portal_extras import portalx_bp
    app.register_blueprint(portalx_bp)

    from app.routes.exports import exports_bp
    app.register_blueprint(exports_bp)

    from app.routes.cfr import cfr_bp
    app.register_blueprint(cfr_bp)

    from app.routes.cue import cue_bp
    app.register_blueprint(cue_bp)

    from app.routes.diy import diy_bp
    app.register_blueprint(diy_bp)

    from app.routes.search import search_bp
    app.register_blueprint(search_bp)

    from app.routes.notifications import notifications_bp
    app.register_blueprint(notifications_bp)

    from app.routes.assistant import assistant_bp
    app.register_blueprint(assistant_bp)

    from app.routes.library import library_bp
    app.register_blueprint(library_bp)
    from app.routes.claim_builder import claims_bp
    app.register_blueprint(claims_bp)
    from app.routes.visits import visits_bp, page_token
    app.register_blueprint(visits_bp)
    app.jinja_env.globals["visit_token"] = page_token

    def safe_url_for(endpoint: str, **values) -> str:
        aliases = {
            "public_home": "public.home",
            "public_contact": "public.contact",
            "become_client": "onboard.become_client",
            "client_portal": "root.client_portal_redirect",
            "employee_dashboard": "employee.dashboard",
            "clients_list": "clients.list_clients",
            "director_dashboard": "director.dashboard",
            "director_employees": "director.users",
            "director_pricing_list": "pricing.list_pricing",
            "director_site_content": "site_admin.edit",
            "logout": "auth.logout",
            "account_change_password": "auth.change_password",
        }
        target = aliases.get(endpoint, endpoint)
        try:
            return url_for(target, **values)
        except BuildError:
            return "#"

    def _notif_count() -> int:
        try:
            if not current_user.is_authenticated:
                return 0
            from app.models import Alert
            return SessionLocal().query(Alert).filter(Alert.user_id == current_user.id, Alert.read_at.is_(None),
                                                      Alert.dismissed_at.is_(None)).count()
        except Exception:
            return 0

    @app.context_processor
    def inject_email_status():
        from app.services.email_service import email_status
        return {"EMAIL_STATUS": email_status()}

    @app.context_processor
    def inject_globals():
        from flask_login import current_user as _current_user
        site = SimpleNamespace(
            theme_navbar_color=os.environ.get("THEME_NAVBAR_COLOR"),
            theme_navbar_bg_secondary=os.environ.get("THEME_NAVBAR_BG_SECONDARY"),
            theme_navbar_text=os.environ.get("THEME_NAVBAR_TEXT"),
            theme_accent=os.environ.get("THEME_ACCENT"),
            theme_radius=os.environ.get("THEME_RADIUS"),
            theme_page_bg=os.environ.get("THEME_PAGE_BG"),
            theme_card_bg=os.environ.get("THEME_CARD_BG"),
            theme_heading_text=os.environ.get("THEME_HEADING_TEXT"),
            theme_muted_text=os.environ.get("THEME_MUTED_TEXT"),
            home_hero_kicker=os.environ.get("HOME_HERO_KICKER"),
            home_hero_title=os.environ.get("HOME_HERO_TITLE"),
            home_hero_subtitle=os.environ.get("HOME_HERO_SUBTITLE"),
            home_primary_cta_text=os.environ.get("HOME_PRIMARY_CTA_TEXT"),
            home_primary_cta_url=os.environ.get("HOME_PRIMARY_CTA_URL"),
            home_disclaimer_box=os.environ.get("HOME_DISCLAIMER_BOX"),
            become_client_title=os.environ.get("BECOME_CLIENT_TITLE"),
        )
        brand_name = os.environ.get("BRAND_NAME") or app.config.get("SITE_NAME", "") or "Veteran Benefits Assistance"
        unread_messages = 0
        if getattr(_current_user, "is_authenticated", False):
            try:
                from datetime import datetime
                from sqlalchemy import func, or_
                from app.models import Client, MessageThread, Message
                from app.models.user import Role
                if _current_user.role in [Role.DIRECTOR, Role.EMPLOYEE]:
                    db = SessionLocal()
                    # One query instead of one-per-client on every staff page load.
                    q = (
                        db.query(func.count(Message.id))
                        .join(MessageThread, Message.thread_id == MessageThread.id)
                        .join(Client, MessageThread.client_id == Client.id)
                        .filter(
                            Client.org_id == _current_user.org_id,
                            Client.is_archived.is_(False),
                            Message.org_id == _current_user.org_id,
                            Message.sender_role == "CLIENT",
                            or_(MessageThread.last_staff_seen_at.is_(None),
                                Message.created_at > MessageThread.last_staff_seen_at),
                        )
                    )
                    if _current_user.role != Role.DIRECTOR:
                        q = q.filter(Client.assigned_user_id == _current_user.id)
                    unread_messages = int(q.scalar() or 0)
            except Exception:
                unread_messages = 0
            # Note: SessionLocal is a scoped session shared with the view that is
            # rendering; it is cleaned up in teardown_appcontext, never closed here.
        return {
            "SITE": site,
            "COMPANY_NAME": brand_name,
            "BRAND_NAME": brand_name,
            "CONTACT_EMAIL": os.environ.get("BRAND_EMAIL") or app.config.get("SUPPORT_EMAIL", ""),
            "CONTACT_PHONE": os.environ.get("SUPPORT_PHONE") or app.config.get("SUPPORT_PHONE", ""),
            "COMPANY_WEBSITE": os.environ.get("BRAND_DOMAIN") or "",
            "HAS_SKIN": os.path.exists(os.path.join(app.root_path, "static", "css", "skin.css")),
            "safe_url_for": safe_url_for,
            "UNREAD_MESSAGES": unread_messages,
            "NOTIF_COUNT": _notif_count(),
        }

    from app.services.bot_guard import form_token as _bot_token
    app.jinja_env.globals["bot_token"] = _bot_token
    from datetime import datetime as _dt
    app.jinja_env.globals["now_utc"] = _dt.utcnow
    app.jinja_env.globals["SEARCH_VERIFY"] = {"google": os.environ.get("GOOGLE_SITE_VERIFICATION", "").strip(),
                                              "bing": os.environ.get("BING_SITE_VERIFICATION", "").strip()}
    from app.services.case_stages import STAGES, stage_key, stage_index, stage_label
    app.jinja_env.globals.update(CASE_STAGES=STAGES, stage_key=stage_key, stage_index=stage_index, stage_label=stage_label)

    @app.template_filter("short_date")
    def short_date(dt, fmt: str = "%b %d"):
        """Date in Eastern time without the trailing zone label (for badges and lists)."""
        out = format_et(dt, fmt)
        return out[:-3] if out.endswith(" ET") else out

    @app.template_filter("format_et")
    def format_et(dt, fmt: str = "%Y-%m-%d %H:%M"):
        if not dt:
            return ""
        try:
            if getattr(dt, "tzinfo", None) is None:
                dt = dt.replace(tzinfo=timezone.utc)
            tz = ZoneInfo("America/New_York") if ZoneInfo else timezone.utc
            return dt.astimezone(tz).strftime(fmt) + " ET"
        except Exception:
            try:
                return dt.strftime(fmt) + " ET"
            except Exception:
                return ""


    def _add_alias(rule: str, endpoint: str, target_endpoint: str, methods=None) -> None:
        if target_endpoint in app.view_functions:
            app.add_url_rule(rule, endpoint=endpoint, view_func=app.view_functions[target_endpoint], methods=methods)

    _add_alias("/", "public_home", "public.home", methods=["GET"])
    _add_alias("/contact", "public_contact", "public.contact", methods=["GET"])
    _add_alias("/become-client", "become_client", "onboard.become_client", methods=["GET"])
    _add_alias("/client-portal", "client_portal", "root.client_portal_redirect", methods=["GET"])

    from app.services.error_alerts import install as _error_alerts
    _error_alerts(app)
    from app.services.uptime_watch import start as _uptime_watch
    _uptime_watch(app)
    return app
