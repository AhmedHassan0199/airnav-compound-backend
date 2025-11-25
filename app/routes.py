from flask import Blueprint, jsonify

main_bp = Blueprint("main", __name__)

@main_bp.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "airnav-compound-backend"})

@main_bp.route("/api/create-superadmin", methods=["GET"])
def create_superadmin():
    from werkzeug.security import generate_password_hash
    from app.models import User, db

    if User.query.filter_by(role="SUPERADMIN").first():
        return {"message": "SUPERADMIN already exists"}

    u = User(
        username="superadmin",
        role="SUPERADMIN",
        password_hash=generate_password_hash("Passw0rd!")
    )
    db.session.add(u)
    db.session.commit()
    return {"message": "SUPERADMIN created"}
