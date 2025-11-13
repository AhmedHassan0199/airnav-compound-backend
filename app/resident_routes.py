from flask import Blueprint, jsonify
from app.models import PersonDetails, MaintenanceInvoice
from .auth.routes import get_current_user_from_request  # reuse helper

resident_bp = Blueprint("resident", __name__)

@resident_bp.route("/profile", methods=["GET"])
def resident_profile():
    user, error = get_current_user_from_request(allowed_roles=["RESIDENT"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    details = PersonDetails.query.filter_by(user_id=user.id).first()

    return jsonify({
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
        },
        "person": {
            "full_name": details.full_name if details else None,
            "building": details.building if details else None,
            "floor": details.floor if details else None,
            "apartment": details.apartment if details else None,
        }
    })


@resident_bp.route("/invoices", methods=["GET"])
def resident_invoices():
    user, error = get_current_user_from_request(allowed_roles=["RESIDENT"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    invoices = (
        MaintenanceInvoice.query
        .filter_by(user_id=user.id)
        .order_by(MaintenanceInvoice.year.desc(), MaintenanceInvoice.month.desc())
        .all()
    )

    result = []
    for inv in invoices:
        result.append({
            "id": inv.id,
            "year": inv.year,
            "month": inv.month,
            "amount": float(inv.amount),
            "status": inv.status,
            "due_date": inv.due_date.isoformat() if inv.due_date else None,
            "paid_date": inv.paid_date.isoformat() if inv.paid_date else None,
            "notes": inv.notes,
        })

    return jsonify(result)
