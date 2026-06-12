import os
from flask import Flask
from app.config import Config

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Blueprint registration
    from app.routes.root import root_bp
    app.register_blueprint(root_bp)

    from app.routes.auth import auth_bp
    app.register_blueprint(auth_bp)

    from app.routes.clients import clients_bp
    app.register_blueprint(clients_bp)

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

    return app
