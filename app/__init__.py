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

    # ---------- CORS SETUP ----------
    CORS(
        app,
        origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "https://airnav-compound-frontend.vercel.app",  # ADD YOUR VERCEL URL,
            "http://95.179.181.72:3000",
            "http://airnav-compound.work.gd"
        ],
        supports_credentials=True,
    )
    # ---------------------------------

    db.init_app(app)
    migrate.init_app(app, db)

    # 👇 THIS LINE IS CRITICAL – it registers all models with SQLAlchemy
    from . import models  # noqa: F401

    # Blueprints
    from .routes import main_bp
    from .auth.routes import auth_bp
    from .resident_routes import resident_bp 
    from .admin_routes import admin_bp 
    from .treasurer_routes import treasurer_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(resident_bp, url_prefix="/api/resident")
    app.register_blueprint(admin_bp, url_prefix="/api/admin") 
    app.register_blueprint(treasurer_bp, url_prefix="/api/treasurer")

    return app