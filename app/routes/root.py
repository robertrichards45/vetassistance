from flask import Blueprint, redirect, url_for
from flask_login import current_user

root_bp = Blueprint('root', __name__)

@root_bp.get('/client-portal')
def client_portal_redirect():
    """Stable client portal entrypoint.

    - Anonymous -> login
    - Client role -> portal dashboard
    - Staff -> clients list
    """
    if not current_user.is_authenticated:
        return redirect(url_for('portal.portal_home'))
    if getattr(current_user, 'role', None) and current_user.role.value == 'CLIENT':
        return redirect(url_for('portal.dashboard'))
    return redirect(url_for('clients.list_clients'))
