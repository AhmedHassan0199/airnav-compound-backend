from flask import Blueprint, request, jsonify
from app import db
from app.models import NotificationSubscription
from .auth.routes import get_current_user_from_request

notifications_bp = Blueprint("notifications", __name__)

@notifications_bp.route("/register", methods=["POST"])
def register_notification():
    current_user, error = get_current_user_from_request()
    if error:
        msg, status = error
        return jsonify({"message": msg}), status

    data = request.get_json() or {}
    token = data.get("token")

    if not token:
        return jsonify({"message": "token is required"}), 400

    user_agent = request.headers.get("User-Agent", "")

    # Upsert logic: if token exists, update owner; otherwise create new
    sub = NotificationSubscription.query.filter_by(token=token).first()
    if sub:
        sub.user_id = current_user.id
        sub.user_agent = user_agent
    else:
        sub = NotificationSubscription(
            user_id=current_user.id,
            token=token,
            user_agent=user_agent,
        )
        db.session.add(sub)

    db.session.commit()

    return jsonify({"message": "notification token registered"}), 200
