from datetime import date
from flask import Blueprint, jsonify, request
from sqlalchemy import or_

from app import db
from app.models import User, PersonDetails, MaintenanceInvoice, Payment
from .auth.routes import get_current_user_from_request

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/residents", methods=["GET"])
def admin_search_residents():
    """
    Search residents by name / building / floor / apartment / username.
    Only ADMIN and SUPERADMIN can use this.
    """
    current_user, error = get_current_user_from_request(
        allowed_roles=["ADMIN", "SUPERADMIN"]
    )
    if error:
        message, status = error
        return jsonify({"message": message}), status

    query = request.args.get("query", "", type=str).strip()

    # Base query: only residents for now
    q = (
        db.session.query(User, PersonDetails)
        .join(PersonDetails, PersonDetails.user_id == User.id)
        .filter(User.role == "RESIDENT")
    )

    if query:
        like = f"%{query}%"
        q = q.filter(
            or_(
                User.username.ilike(like),
                PersonDetails.full_name.ilike(like),
                PersonDetails.building.ilike(like),
                PersonDetails.floor.ilike(like),
                PersonDetails.apartment.ilike(like),
            )
        )

    residents = q.order_by(PersonDetails.building, PersonDetails.floor, PersonDetails.apartment).all()

    results = []
    for user, details in residents:
        unpaid_count = (
            MaintenanceInvoice.query
            .filter_by(user_id=user.id)
            .filter(MaintenanceInvoice.status != "PAID")
            .count()
        )

        results.append({
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "person": {
                "full_name": details.full_name,
                "building": details.building,
                "floor": details.floor,
                "apartment": details.apartment,
            },
            "unpaid_invoices_count": unpaid_count,
        })

    return jsonify(results)


@admin_bp.route("/residents/<int:user_id>/invoices", methods=["GET"])
def admin_resident_invoices(user_id: int):
    """
    Get all invoices for a specific resident (for Admin view).
    """
    current_user, error = get_current_user_from_request(
        allowed_roles=["ADMIN", "SUPERADMIN"]
    )
    if error:
        message, status = error
        return jsonify({"message": message}), status

    resident = User.query.filter_by(id=user_id, role="RESIDENT").first()
    if not resident:
        return jsonify({"message": "resident not found"}), 404

    invoices = (
        MaintenanceInvoice.query
        .filter_by(user_id=resident.id)
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

    return jsonify({
        "resident": {
            "id": resident.id,
            "username": resident.username,
        },
        "invoices": result,
    })


@admin_bp.route("/collect", methods=["POST"])
def admin_collect_payment():
    """
    Admin marks an invoice as PAID and creates a Payment record.
    """
    current_user, error = get_current_user_from_request(
        allowed_roles=["ADMIN"]
    )
    if error:
        message, status = error
        return jsonify({"message": message}), status

    data = request.get_json() or {}
    user_id = data.get("user_id")
    invoice_id = data.get("invoice_id")
    amount = data.get("amount")
    method = data.get("method", "CASH")
    notes = data.get("notes")

    if not all([user_id, invoice_id, amount]):
        return jsonify({"message": "user_id, invoice_id and amount are required"}), 400

    try:
        amount_val = float(amount)
        if amount_val <= 0:
            raise ValueError()
    except Exception:
        return jsonify({"message": "invalid amount"}), 400

    invoice = (
        MaintenanceInvoice.query
        .filter_by(id=invoice_id, user_id=user_id)
        .first()
    )
    if not invoice:
        return jsonify({"message": "invoice not found for this user"}), 404

    if invoice.status == "PAID":
        return jsonify({"message": "invoice already paid"}), 400

    # Update invoice
    invoice.status = "PAID"
    invoice.paid_date = date.today()

    # Create payment record
    payment = Payment(
        user_id=user_id,
        invoice_id=invoice_id,
        amount=amount_val,
        method=method,
        notes=notes,
        collected_by_admin_id=current_user.id,
    )

    db.session.add(payment)
    db.session.commit()

    return jsonify({
        "message": "payment recorded and invoice marked as PAID",
        "invoice": {
            "id": invoice.id,
            "year": invoice.year,
            "month": invoice.month,
            "amount": float(invoice.amount),
            "status": invoice.status,
            "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
            "paid_date": invoice.paid_date.isoformat() if invoice.paid_date else None,
            "notes": invoice.notes,
        },
        "payment": {
            "id": payment.id,
            "amount": float(payment.amount),
            "method": payment.method,
            "notes": payment.notes,
            "created_at": payment.created_at.isoformat(),
        }
    })
