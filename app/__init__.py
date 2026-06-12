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
from app.config import Config
from app.extensions import login_manager, csrf, limiter, init_db, SessionLocal
from app.models import User, Base, PageView
from app.models.user import Role


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

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

    try:
        Base.metadata.create_all(bind=engine)
    except Exception:
        pass

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
        brand_name = os.environ.get("BRAND_NAME") or app.config.get("SITE_NAME", "")
        unread_messages = 0
        if getattr(_current_user, "is_authenticated", False):
            try:
                from app.models import Client, MessageThread, Message
                from app.models.user import Role
                db = SessionLocal()
                if _current_user.role in [Role.DIRECTOR, Role.EMPLOYEE]:
                    if _current_user.role == Role.DIRECTOR:
                        clients = db.query(Client).filter_by(org_id=_current_user.org_id, is_archived=False).all()
                    else:
                        clients = db.query(Client).filter_by(org_id=_current_user.org_id, assigned_user_id=_current_user.id, is_archived=False).all()
                    from datetime import datetime
                    for c in clients:
                        thread = db.query(MessageThread).filter_by(org_id=_current_user.org_id, client_id=c.id).first()
                        if not thread:
                            continue
                        since = thread.last_staff_seen_at or datetime.min
                        unread_messages += db.query(Message).filter_by(org_id=_current_user.org_id, thread_id=thread.id, sender_role="CLIENT").filter(Message.created_at > since).count()
            except Exception:
                unread_messages = 0
        return {
            "SITE": site,
            "COMPANY_NAME": brand_name,
            "CONTACT_EMAIL": os.environ.get("BRAND_EMAIL") or app.config.get("SUPPORT_EMAIL", ""),
            "CONTACT_PHONE": os.environ.get("SUPPORT_PHONE") or app.config.get("SUPPORT_PHONE", ""),
            "COMPANY_WEBSITE": os.environ.get("BRAND_DOMAIN") or "",
            "HAS_SKIN": os.path.exists(os.path.join(app.root_path, "static", "css", "skin.css")),
            "safe_url_for": safe_url_for,
            "UNREAD_MESSAGES": unread_messages,
        }

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

    return app
