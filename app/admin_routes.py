from datetime import date
from flask import Blueprint, jsonify, request
from sqlalchemy import or_, func
from decimal import Decimal

from app import db
from app.models import User, PersonDetails, MaintenanceInvoice, Payment, Settlement
from .auth.routes import get_current_user_from_request

admin_bp = Blueprint("admin", __name__)

def create_initial_invoices_for_resident(user: User, monthly_amount: Decimal = Decimal("200.00")):
    """
    Create invoices for the new resident from current month until end of year.
    Example: if today is 2025-11-xx -> create invoices for Nov & Dec of 2025.
    """
    today = date.today()
    current_year = today.year
    start_month = today.month

    for month in range(start_month, 13):  # 13 so it includes 12
        invoice = MaintenanceInvoice(
            user_id=user.id,
            year=current_year,
            month=month,
            amount=monthly_amount,
            status="UNPAID",
            due_date=date.today()
        )
        db.session.add(invoice)


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

    if invoice.status == "PENDING_CONFIRMATION":
        return jsonify({
            "message": "لا يمكن تحصيل هذا الايصال نقداً لأنه يحتوي على عملية دفع إلكتروني قيد المراجعة."
        }), 400

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

@admin_bp.route("/invoices", methods=["POST"])
def admin_create_invoice():
    """
    Admin creates a new maintenance invoice for a resident.
    """
    current_user, error = get_current_user_from_request(allowed_roles=["ADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    data = request.get_json() or {}
    user_id = data.get("user_id")
    year = data.get("year")
    month = data.get("month")
    amount = data.get("amount")
    due_date_str = data.get("due_date")  # optional: "YYYY-MM-DD"
    notes = data.get("notes")

    if not all([user_id, year, month, amount]):
        return jsonify({"message": "user_id, year, month and amount are required"}), 400

    try:
        year = int(year)
        month = int(month)
        if month < 1 or month > 12:
            raise ValueError()
    except Exception:
        return jsonify({"message": "invalid year or month"}), 400

    try:
        amount_val = float(amount)
        if amount_val <= 0:
            raise ValueError()
    except Exception:
        return jsonify({"message": "invalid amount"}), 400

    resident = User.query.filter_by(id=user_id, role="RESIDENT").first()
    if not resident:
        return jsonify({"message": "resident not found"}), 404

    # تأكد إنه مفيش فاتورة لنفس الشهر و السنة
    existing = (
        MaintenanceInvoice.query
        .filter_by(user_id=user_id, year=year, month=month)
        .first()
    )
    if existing:
        return jsonify({
            "message": "يوجد بالفعل فاتورة لهذا الشهر والسنة. يمكنك حذف الفاتورة القديمة أولاً ثم إنشاء واحدة جديدة."
        }), 400

    # Parse due_date لو موجود
    due_date = None
    if due_date_str:
        try:
            due_date = date.fromisoformat(due_date_str)
        except Exception:
            return jsonify({"message": "invalid due_date format, expected YYYY-MM-DD"}), 400

    invoice = MaintenanceInvoice(
        user_id=user_id,
        year=year,
        month=month,
        amount=amount_val,
        status="PENDING",
        due_date=due_date,
        notes=notes,
    )

    db.session.add(invoice)
    db.session.commit()

    return jsonify({
        "message": "invoice created",
        "invoice": {
            "id": invoice.id,
            "year": invoice.year,
            "month": invoice.month,
            "amount": float(invoice.amount),
            "status": invoice.status,
            "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
            "paid_date": invoice.paid_date.isoformat() if invoice.paid_date else None,
            "notes": invoice.notes,
        }
    }), 201

@admin_bp.route("/invoices/<int:invoice_id>", methods=["DELETE"])
def admin_delete_invoice(invoice_id: int):
    """
    Admin deletes an invoice (only if not PAID and has no payments).
    """
    current_user, error = get_current_user_from_request(allowed_roles=["ADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    invoice = MaintenanceInvoice.query.filter_by(id=invoice_id).first()
    if not invoice:
        return jsonify({"message": "invoice not found"}), 404

    # من باب الأمان: منمسحش فاتورة مدفوعة أو عليها Payments أو قيد تأكيد دفع إلكتروني
    if invoice.status == "PAID":
        return jsonify({"message": "cannot delete a PAID invoice"}), 400

    if invoice.status == "PENDING_CONFIRMATION":
        return jsonify({
            "message": "cannot delete invoice while an online payment is pending confirmation"
        }), 400


    payments_count = Payment.query.filter_by(invoice_id=invoice.id).count()
    if payments_count > 0:
        return jsonify({"message": "cannot delete invoice that has payments recorded"}), 400

    db.session.delete(invoice)
    db.session.commit()

    return jsonify({"message": "invoice deleted"}), 200

@admin_bp.route("/me/summary", methods=["GET"])
def admin_me_summary():
    """
    Summary for the current admin:
    - total collected amount
    - number of payments
    - today's collected amount and count
    - settled amount
    - outstanding amount
    - recent payments
    """
    current_user, error = get_current_user_from_request(allowed_roles=["ADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    # Total collected
    total_amount = (
        db.session.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.collected_by_admin_id == current_user.id)
        .scalar()
        or 0
    )

    payments_count = (
        db.session.query(func.count(Payment.id))
        .filter(Payment.collected_by_admin_id == current_user.id)
        .scalar()
        or 0
    )

    # Today
    today = date.today()
    today_amount = (
        db.session.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(
            Payment.collected_by_admin_id == current_user.id,
            Payment.created_at == today,
        )
        .scalar()
        or 0
    )

    today_count = (
        db.session.query(func.count(Payment.id))
        .filter(
            Payment.collected_by_admin_id == current_user.id,
            Payment.created_at == today,
        )
        .scalar()
        or 0
    )

    # Total settled (what admin already handed over to treasurer)
    settled_amount = (
        db.session.query(func.coalesce(func.sum(Settlement.amount), 0))
        .filter(Settlement.admin_id == current_user.id)
        .scalar()
        or 0
    )

    # Outstanding = collected - settled
    outstanding_amount = float(total_amount) - float(settled_amount)

    # Recent payments
    recent = (
        db.session.query(Payment, MaintenanceInvoice, PersonDetails)
        .join(MaintenanceInvoice, Payment.invoice_id == MaintenanceInvoice.id)
        .join(PersonDetails, PersonDetails.user_id == Payment.user_id)
        .filter(Payment.collected_by_admin_id == current_user.id)
        .order_by(Payment.created_at.desc())
        .limit(10)
        .all()
    )

    recent_list = []
    for pay, inv, details in recent:
        recent_list.append(
            {
                "id": pay.id,
                "amount": float(pay.amount),
                "created_at": pay.created_at.isoformat(),
                "resident_name": details.full_name,
                "building": details.building,
                "floor": details.floor,
                "apartment": details.apartment,
                "year": inv.year,
                "month": inv.month,
            }
        )

    return jsonify(
        {
            "total_amount": float(total_amount),
            "payments_count": int(payments_count),
            "today_amount": float(today_amount),
            "today_count": int(today_count),
            "settled_amount": float(settled_amount),
            "outstanding_amount": float(outstanding_amount),
            "recent_payments": recent_list,
        }
    )

VALID_ROLES = {"RESIDENT", "ADMIN", "TREASURER", "SUPERADMIN"}

@admin_bp.route("/users", methods=["POST"])
def superadmin_create_user():
    """
    SuperAdmin creates a new user (Admin / Treasurer / Resident / SuperAdmin).
    """
    current_user, error = get_current_user_from_request(allowed_roles=["SUPERADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    data = request.get_json() or {}

    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    role = data.get("role", "").strip().upper()

    full_name = data.get("full_name", "").strip() or None
    building = data.get("building", "").strip() or None
    floor = data.get("floor", "").strip() or None
    apartment = data.get("apartment", "").strip() or None
    phone = data.get("phone", "").strip() or None

    if not username or not password or not role:
        return jsonify({"message": "username, password and role are required"}), 400

    if role not in VALID_ROLES:
        return jsonify({"message": "invalid role"}), 400

    # Check username uniqueness
    existing = User.query.filter_by(username=username).first()
    if existing:
        return jsonify({"message": "username already exists"}), 400

    # Create user
    user = User(username=username, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()  # get user.id

    # Optional person details
    # For TREASURER / SUPERADMIN building/floor/apartment can be dummy or null
    if any([full_name, building, floor, apartment]):
        details = PersonDetails(
            user_id=user.id,
            full_name=full_name or username,
            building=building or "",
            floor=floor or "",
            apartment=apartment or "",
            phone=phone or "",
        )
        db.session.add(details)

    # ✅ Only for residents: create invoices
    if role == "RESIDENT":
        create_initial_invoices_for_resident(user)
    
    db.session.commit()

    return jsonify(
        {
            "message": "user created",
            "user": {
                "id": user.id,
                "username": user.username,
                "role": user.role,
            },
        }
    ), 201