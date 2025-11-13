from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta, timezone
from jwt import ExpiredSignatureError, InvalidTokenError
import jwt

from app import db
from app.models import User
from app.config import Config

auth_bp = Blueprint("auth", __name__)

JWT_SECRET = Config.JWT_SECRET
JWT_ALG = "HS256"
JWT_EXP_MINUTES = 60 * 12  # 12 ساعة

def create_token(user: User):
    payload = {
        "sub": str(user.id),  # 👈 لازم string
        "username": user.username,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXP_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def decode_token(token: str):
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])

@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json() or {}
    username = data.get("username")
    password = data.get("password")
    role = data.get("role", "RESIDENT")

    if not username or not password:
        return jsonify({"message": "username and password required"}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({"message": "username already exists"}), 409

    user = User(username=username, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    token = create_token(user)

    return jsonify({
        "access_token": token,
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
        }
    }), 201

@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"message": "username and password required"}), 400

    user = User.query.filter_by(username=username).first()
    if not user or not user.check_password(password):
        return jsonify({"message": "invalid credentials"}), 401

    token = create_token(user)

    return jsonify({
        "access_token": token,
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
        }
    })

@auth_bp.route("/me", methods=["GET"])
def me():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"message": "missing token"}), 401

    token = auth_header.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except ExpiredSignatureError:
        return jsonify({"message": "token expired"}), 401
    except InvalidTokenError as e:
        # just for debug, don't log e in prod with full details
        return jsonify({"message": f"invalid token: {str(e)}"}), 401

    user_id = int(payload["sub"])  # 👈 نحولها لرقم
    user = User.query.get(user_id)
    if not user:
        return jsonify({"message": "user not found"}), 404

    return jsonify({
        "id": user.id,
        "username": user.username,
        "role": user.role,
    })