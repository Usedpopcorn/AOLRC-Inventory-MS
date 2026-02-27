import os
from flask import Flask, app
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
# from flask_login import LoginManager
from dotenv import load_dotenv

from .config import Config

db = SQLAlchemy()
migrate = Migrate()
# login_manager = LoginManager()

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

    db.init_app(app)
    migrate.init_app(app, db)
    # login_manager.init_app(app)

    from .routes.main import main_bp
    app.register_blueprint(main_bp)

    from .routes.admin import admin_bp
    app.register_blueprint(admin_bp)

    from .routes.venue_items import venue_items_bp
    app.register_blueprint(venue_items_bp)

    from . import models  # ensures models are registered for migrations

    return app