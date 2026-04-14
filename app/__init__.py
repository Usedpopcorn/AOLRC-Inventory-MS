import os
import click
from urllib.parse import urlparse

from flask import Flask, flash, jsonify, redirect, request, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv

from .config import Config, DEFAULT_SECRET_KEY, is_development_environment

db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in to continue."
login_manager.login_message_category = "error"


@login_manager.user_loader
def load_user(user_id):
    from .models import User

    if not user_id:
        return None
    try:
        parsed_user_id = int(user_id)
    except (TypeError, ValueError):
        return None
    return User.query.get(parsed_user_id)


@login_manager.unauthorized_handler
def handle_unauthorized():
    from .authz import wants_json_response

    if wants_json_response():
        return jsonify({"error": "authentication required", "code": "unauthenticated"}), 401
    return redirect(url_for("auth.login", next=request.url))

def create_app():
    load_dotenv(override=True)  # loads variables from .env into the environment

    app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static"
)
    app.config.from_object(Config)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", app.config["SECRET_KEY"])
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", app.config["SQLALCHEMY_DATABASE_URI"])
    if (
        app.config["SECRET_KEY"] == DEFAULT_SECRET_KEY
        and not app.debug
        and not is_development_environment()
    ):
        raise RuntimeError(
            "SECRET_KEY is using the default development value. "
            "Set a unique SECRET_KEY before running outside development."
        )

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)

    from .routes.main import main_bp
    app.register_blueprint(main_bp)

    from .routes.auth import auth_bp
    app.register_blueprint(auth_bp)

    from .routes.admin import admin_bp
    app.register_blueprint(admin_bp)

    from .routes.venue_items import venue_items_bp
    app.register_blueprint(venue_items_bp)

    from .routes.venue_settings import venue_settings_bp
    app.register_blueprint(venue_settings_bp)

    from .routes.supplies import supplies_bp
    app.register_blueprint(supplies_bp)
    
    from . import models  # ensures models are registered for migrations

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        flash("Your form session expired. Please try again.", "error")
        referrer = request.referrer or ""
        host = request.host_url or ""
        if referrer:
            referrer_host = urlparse(referrer).netloc
            current_host = urlparse(host).netloc
            if referrer_host == current_host:
                return redirect(referrer)
        return redirect(url_for("auth.login"))

    @app.cli.command("create-admin")
    @click.option("--email", prompt=True, help="Admin email")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True, help="Admin password")
    def create_admin(email, password):
        from .models import User, normalize_role

        normalized_email = (email or "").strip().lower()
        if not normalized_email:
            raise click.ClickException("Email is required.")
        if not password:
            raise click.ClickException("Password is required.")

        existing = User.query.filter_by(email=normalized_email).first()
        if existing:
            existing.password_hash = generate_password_hash(password)
            existing.role = normalize_role("admin")
            existing.active = True
            db.session.commit()
            click.echo(f"Updated existing user to admin: {normalized_email}")
            return

        user = User(
            email=normalized_email,
            password_hash=generate_password_hash(password),
            role=normalize_role("admin"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        click.echo(f"Created admin user: {normalized_email}")

    return app
