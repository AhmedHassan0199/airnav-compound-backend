from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS
from .config import Config

db = SQLAlchemy()
migrate = Migrate()

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    CORS(app, origins=["http://localhost:3000"], supports_credentials=True)

    db.init_app(app)
    migrate.init_app(app, db)

    # 👇 THIS LINE IS CRITICAL – it registers all models with SQLAlchemy
    from . import models  # noqa: F401

    # Blueprints
    from .routes import main_bp
    from .auth.routes import auth_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/api/auth")

    return app