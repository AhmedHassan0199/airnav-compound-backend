from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta, timezone
from jwt import ExpiredSignatureError, InvalidTokenError
import jwt

from app import db
from app.models import User, PersonDetails
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
    """
    Login endpoint with two modes:

    1) Staff (ADMIN / TREASURER / SUPERADMIN / ONLINE_ADMIN):
       - Send: { "username": "...", "password": "..." }

    2) Residents:
       - Send: { "building": "...", "floor": "...", "apartment": "...", "password": "..." }
       - We look up a RESIDENT user whose PersonDetails matches that unit.
    """
    data = request.get_json() or {}

    username = (data.get("username") or "").strip()
    password = data.get("password")

    building = (data.get("building") or "").strip()
    floor = (data.get("floor") or "").strip()
    apartment = (data.get("apartment") or "").strip()

    if not password:
        return jsonify({"message": "password is required"}), 400

    user = None

    # Mode 1: username + password (for staff and backward compatibility)
    if username:
        user = User.query.filter_by(username=username).first()

    # Mode 2: building/floor/apartment + password (for RESIDENT accounts)
    elif building and floor and apartment:
        details = (
            db.session.query(PersonDetails)
            .join(User, PersonDetails.user_id == User.id)
            .filter(
                User.role == "RESIDENT",
                PersonDetails.building == building,
                PersonDetails.floor == floor,
                PersonDetails.apartment == apartment,
            )
            .first()
        )

        if details:
            user = details.user

    else:
        return jsonify(
            {
                "message": (
                    "Either (username + password) or "
                    "(building + floor + apartment + password) is required"
                )
            }
        ), 400

    if not user or not user.check_password(password):
        return jsonify({"message": "invalid credentials"}), 401

    token = create_token(user)

    return jsonify(
        {
            "access_token": token,
            "user": {
                "id": user.id,
                "username": user.username,
                "role": user.role,
            },
        }
    )

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

def get_current_user_from_request(allowed_roles=None):
    """
    Read Authorization header, decode JWT, return User object.
    If allowed_roles is provided, ensure user.role is in that list.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None, ("missing token", 401)

    token = auth_header.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except ExpiredSignatureError:
        return None, ("token expired", 401)
    except InvalidTokenError as e:
        return None, (f"invalid token: {str(e)}", 401)

    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError, TypeError):
        return None, ("invalid token payload", 401)

    user = User.query.get(user_id)
    if not user:
        return None, ("user not found", 404)

    if allowed_roles and user.role not in allowed_roles:
        return None, ("forbidden", 403)

    return user, None