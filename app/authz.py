from functools import wraps

from flask import flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required, logout_user

from app.models import normalize_role


def wants_json_response():
    if request.path.startswith("/dashboard/restocking_rows"):
        return True
    accept_header = (request.headers.get("Accept") or "").lower()
    if "application/json" not in accept_header:
        return False
    accepts_json = request.accept_mimetypes["application/json"]
    accepts_html = request.accept_mimetypes["text/html"]
    if accepts_json == 0:
        return False
    return accepts_json > accepts_html


def roles_required(*roles):
    normalized_roles = {normalize_role(role) for role in roles}

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped(*args, **kwargs):
            if not getattr(current_user, "is_active", False):
                logout_user()
                if wants_json_response():
                    return jsonify({"error": "authentication required", "code": "inactive"}), 401

                flash("Your account is inactive. Contact an admin.", "error")
                return redirect(url_for("auth.login", next=request.url))

            if current_user.has_role(*normalized_roles):
                return view_func(*args, **kwargs)

            if wants_json_response():
                return jsonify({"error": "forbidden", "code": "forbidden"}), 403

            flash("You do not have permission to perform that action.", "error")
            return redirect(url_for("main.dashboard"))

        return wrapped

    return decorator
