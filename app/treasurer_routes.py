from datetime import date, datetime
from flask import Blueprint, jsonify, request
from sqlalchemy import func

from app import db
from app.models import User, PersonDetails, Payment, Settlement, MaintenanceInvoice
from .auth.routes import get_current_user_from_request

treasurer_bp = Blueprint("treasurer", __name__)


def _admin_summary_for_treasurer(admin_id: int):
    """
    Helper: compute totals for one admin.
    """
    # total collected
    total_amount = (
        db.session.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.collected_by_admin_id == admin_id)
        .scalar()
        or 0
    )

    # total settled
    settled_amount = (
        db.session.query(func.coalesce(func.sum(Settlement.amount), 0))
        .filter(Settlement.admin_id == admin_id)
        .scalar()
        or 0
    )

    outstanding_amount = float(total_amount) - float(settled_amount)

    payments_count = (
        db.session.query(func.count(Payment.id))
        .filter(Payment.collected_by_admin_id == admin_id)
        .scalar()
        or 0
    )

    return {
        "total_amount": float(total_amount),
        "settled_amount": float(settled_amount),
        "outstanding_amount": float(outstanding_amount),
        "payments_count": int(payments_count),
    }


@treasurer_bp.route("/admins", methods=["GET"])
def treasurer_list_admins():
    """
    List all admins with their financial summary
    (for Treasurer to see who owes what).
    """
    current_user, error = get_current_user_from_request(allowed_roles=["TREASURER"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    admins = User.query.filter_by(role="ADMIN").all()

    results = []
    for admin in admins:
        details = PersonDetails.query.filter_by(user_id=admin.id).first()
        summary = _admin_summary_for_treasurer(admin.id)

        results.append(
            {
                "id": admin.id,
                "username": admin.username,
                "full_name": details.full_name if details else admin.username,
                "summary": summary,
            }
        )

    return jsonify(results)


@treasurer_bp.route("/admins/<int:admin_id>", methods=["GET"])
def treasurer_admin_details(admin_id: int):
    """
    Detailed view for one admin:
    - summary (total, settled, outstanding)
    - recent settlements
    """
    current_user, error = get_current_user_from_request(allowed_roles=["TREASURER"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    admin = User.query.filter_by(id=admin_id, role="ADMIN").first()
    if not admin:
        return jsonify({"message": "admin not found"}), 404

    details = PersonDetails.query.filter_by(user_id=admin.id).first()
    summary = _admin_summary_for_treasurer(admin.id)

    # Recent settlements for this admin
    recent_settlements = (
        db.session.query(Settlement, User)
        .join(User, Settlement.treasurer_id == User.id)
        .filter(Settlement.admin_id == admin.id)
        .order_by(Settlement.created_at.desc(), Settlement.id.desc())
        .limit(10)
        .all()
    )

    recent_list = []
    for sett, treasurer in recent_settlements:
        recent_list.append(
            {
                "id": sett.id,
                "amount": float(sett.amount),
                "created_at": sett.created_at.isoformat(),
                "treasurer_name": treasurer.username,
                "notes": sett.notes,
            }
        )

    return jsonify(
        {
            "admin": {
                "id": admin.id,
                "username": admin.username,
                "full_name": details.full_name if details else admin.username,
            },
            "summary": summary,
            "recent_settlements": recent_list,
        }
    )


@treasurer_bp.route("/settlements", methods=["POST"])
def treasurer_create_settlement():
    """
    Treasurer records that an admin has handed over cash.
    This reduces the admin's outstanding balance.
    """
    current_user, error = get_current_user_from_request(allowed_roles=["TREASURER"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    data = request.get_json() or {}
    admin_id = data.get("admin_id")
    amount = data.get("amount")
    notes = data.get("notes")

    if not admin_id or amount is None:
        return jsonify({"message": "admin_id and amount are required"}), 400

    admin = User.query.filter_by(id=admin_id, role="ADMIN").first()
    if not admin:
        return jsonify({"message": "admin not found"}), 404

    try:
        amount_val = float(amount)
        if amount_val <= 0:
            raise ValueError()
    except Exception:
        return jsonify({"message": "invalid amount"}), 400

    # compute outstanding
    summary = _admin_summary_for_treasurer(admin.id)
    outstanding = summary["outstanding_amount"]

    if amount_val > outstanding + 1e-6:  # صغير tolerance
        return jsonify(
            {
                "message": "amount cannot be greater than admin outstanding balance",
                "outstanding_amount": outstanding,
            }
        ), 400

    settlement = Settlement(
        admin_id=admin.id,
        treasurer_id=current_user.id,
        amount=amount_val,
        created_at=date.today(),
        notes=notes,
    )

    db.session.add(settlement)
    db.session.commit()

    # Recompute summary after settlement
    new_summary = _admin_summary_for_treasurer(admin.id)

    return jsonify(
        {
            "message": "settlement recorded",
            "admin_id": admin.id,
            "summary": new_summary,
        }
    ), 201

@treasurer_bp.route("/summary", methods=["GET"])
def treasurer_summary():
    """
    Overall financial summary for the union:
    - total collected (all time)
    - total settled from admins to union
    - current union balance
    - today's collected
    - this month's collected
    - total invoices (paid / unpaid)
    """
    current_user, error = get_current_user_from_request(allowed_roles=["TREASURER", "SUPERADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    today = date.today()
    first_of_month = date(today.year, today.month, 1)

    # Collected from residents by all admins
    total_collected = (
        db.session.query(func.coalesce(func.sum(Payment.amount), 0))
        .scalar()
        or 0
    )

    # Settled to union by admins
    total_settled = (
        db.session.query(func.coalesce(func.sum(Settlement.amount), 0))
        .scalar()
        or 0
    )

    # Union balance = total_settled (simple model; can be adjusted later for other incomes)
    union_balance = float(total_settled)

    # Today collected
    today_collected = (
        db.session.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.created_at == today)
        .scalar()
        or 0
    )

    # This month collected
    this_month_collected = (
        db.session.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.created_at >= first_of_month)
        .scalar()
        or 0
    )

    # Invoice stats
    total_invoices = db.session.query(func.count(MaintenanceInvoice.id)).scalar() or 0
    paid_invoices = (
        db.session.query(func.count(MaintenanceInvoice.id))
        .filter(MaintenanceInvoice.status == "PAID")
        .scalar()
        or 0
    )
    unpaid_invoices = total_invoices - paid_invoices

    return jsonify(
        {
            "total_collected": float(total_collected),
            "total_settled": float(total_settled),
            "union_balance": float(union_balance),
            "today_collected": float(today_collected),
            "this_month_collected": float(this_month_collected),
            "total_invoices": int(total_invoices),
            "paid_invoices": int(paid_invoices),
            "unpaid_invoices": int(unpaid_invoices),
        }
    )

