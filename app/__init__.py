from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
# from flask_login import LoginManager
from dotenv import load_dotenv

from .config import Config

db = SQLAlchemy()
migrate = Migrate()
# login_manager = LoginManager()

def create_app():
    load_dotenv()  # loads variables from .env into the environment

    app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static"
)
    app.config.from_object(Config)

    db.init_app(app)
    migrate.init_app(app, db)
    # login_manager.init_app(app)

    from .routes.main import main_bp
    app.register_blueprint(main_bp)

    from . import models  # ensures models are registered for migrations

    return app