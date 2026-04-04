from functools import wraps

from flask import flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from app.models import normalize_role


def _wants_json_response():
    if request.path.startswith("/dashboard/restocking_rows"):
        return True
    return request.accept_mimetypes.best == "application/json"


def roles_required(*roles):
    normalized_roles = {normalize_role(role) for role in roles}

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.has_role(*normalized_roles):
                return view_func(*args, **kwargs)

            if _wants_json_response():
                return jsonify({"error": "forbidden"}), 403

            flash("You do not have permission to perform that action.", "error")
            return redirect(url_for("main.dashboard"))

        return wrapped

    return decorator
