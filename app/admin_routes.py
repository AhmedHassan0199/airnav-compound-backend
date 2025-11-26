from datetime import date,datetime
from flask import Blueprint, jsonify, request
from sqlalchemy import or_, func
from decimal import Decimal

from app import db
from app.models import (
    User,
    PersonDetails,
    MaintenanceInvoice,
    Payment,
    Settlement,
    OnlinePayment,
    AdminBuilding,
)
from .auth.routes import get_current_user_from_request

admin_bp = Blueprint("admin", __name__)

def get_admin_allowed_buildings(admin_id: int):
    rows = AdminBuilding.query.filter_by(admin_id=admin_id).all()
    return [r.building for r in rows]


def create_initial_invoices_for_resident(user: User):
    """
    Create maintenance invoices for this resident:
    - From the current month until the end of this year
    - Plus the full next year (Jan–Dec)
    """
    today = date.today()

    # Start from current month & year
    year = today.year
    month = today.month

    # End at December next year
    end_year = year + 1
    end_month = 12

    while year < end_year or (year == end_year and month <= end_month):
        # 👇 keep your existing amount / due_date / notes logic here
        # Example (you probably already have something like this):
        #
        # amount = Decimal("200.00")   # or from config / DB
        # due_day = 5
        # due_date = date(year, month, due_day)

        invoice = MaintenanceInvoice(
            user_id=user.id,
            year=year,
            month=month,
            amount=Decimal("200.00"),  # TODO: replace with your actual logic
            status="UNPAID",
            due_date=date(year, month, 5),  # TODO: replace with your actual due date logic
            notes=None,
        )
        db.session.add(invoice)

        # Move to next month
        month += 1
        if month > 12:
            month = 1
            year += 1


@admin_bp.route("/residents", methods=["GET"])
def admin_search_residents():
    """
    Search residents by name / building / floor / apartment / username.
    Only ADMIN and SUPERADMIN can use this.
    """
    current_user, error = get_current_user_from_request(
        allowed_roles=["ADMIN", "SUPERADMIN","ONLINE_ADMIN"]
    )
    if error:
        message, status = error
        return jsonify({"message": message}), status

    query = request.args.get("query", "", type=str).strip()

    # Base query
    q = (
        db.session.query(User, PersonDetails)
        .join(PersonDetails, PersonDetails.user_id == User.id)
        .filter(User.role == "RESIDENT")
    )

    # Restrict for ADMIN users
    if current_user.role == "ADMIN":
        allowed = get_admin_allowed_buildings(current_user.id)
        q = q.filter(PersonDetails.building.in_(allowed))

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
        allowed_roles=["ADMIN", "SUPERADMIN","ONLINE_ADMIN"]
    )
    if error:
        message, status = error
        return jsonify({"message": message}), status

    resident = User.query.filter_by(id=user_id, role="RESIDENT").first()

    # If admin, make sure resident is in allowed buildings
    if current_user.role == "ADMIN":
        details = resident.person_details
        allowed = get_admin_allowed_buildings(current_user.id)
        if details and details.building not in allowed:
            return jsonify({"message": "not allowed: resident outside your buildings"}), 403

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
        allowed_roles=["ADMIN","ONLINE_ADMIN"]
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

    # Restrict admin access by building
    if current_user.role == "ADMIN":
        resident = User.query.get(user_id)
        details = resident.person_details
        allowed = get_admin_allowed_buildings(current_user.id)
        if details and details.building not in allowed:
            return jsonify({"message": "not allowed: resident outside your buildings"}), 403

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
    current_user, error = get_current_user_from_request(allowed_roles=["ADMIN","ONLINE_ADMIN"])
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

    # If admin, ensure this resident belongs to your buildings
    if current_user.role == "ADMIN":
        details = resident.person_details
        allowed = get_admin_allowed_buildings(current_user.id)
        if details and details.building not in allowed:
            return jsonify({"message": "not allowed: resident outside your buildings"}), 403

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
    current_user, error = get_current_user_from_request(allowed_roles=["ADMIN","ONLINE_ADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    invoice = MaintenanceInvoice.query.filter_by(id=invoice_id).first()

    # Restrict admin access by building
    if current_user.role == "ADMIN":
        resident = User.query.get(invoice.user_id)
        details = resident.person_details
        allowed = get_admin_allowed_buildings(current_user.id)
        if details and details.building not in allowed:
            return jsonify({"message": "not allowed: resident outside your buildings"}), 403

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
    current_user, error = get_current_user_from_request(allowed_roles=["ADMIN","ONLINE_ADMIN"])
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

VALID_ROLES = {"RESIDENT", "ADMIN", "TREASURER", "SUPERADMIN","ONLINE_ADMIN"}

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

    def clean_str(key: str, default: str = "") -> str:
        """
        Required string: always return a stripped string (may be empty).
        """
        value = data.get(key, default)
        if not isinstance(value, str):
            return default
        return value.strip()
    
    def clean_optional_str(key: str):
        """
        Optional string: return None if missing/empty/non-string, otherwise stripped value.
        """
        value = data.get(key, None)
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None


    username = clean_str("username")
    password = clean_str("password")
    role = clean_str("role", "RESIDENT").upper()

    full_name = clean_optional_str("full_name")
    building = clean_optional_str("building")
    floor = clean_optional_str("floor")
    apartment = clean_optional_str("apartment")
    phone = clean_optional_str("phone")

    if not username or not password or not role:
        return jsonify({"message": "username, password and role are required"}), 400

    if role not in VALID_ROLES:
        return jsonify({"message": "invalid role"}), 400

    # Check username uniqueness
    existing = User.query.filter_by(username=username).first()
    if existing:
        return jsonify({"message": "username already exists"}), 400

    # ✅ For RESIDENT only: enforce unique (building, floor, apartment) among RESIDENTS
    if role == "RESIDENT":
        # Require full unit info
        if not (building and floor and apartment):
            return (
                jsonify(
                    {
                        "message": (
                            "For RESIDENT users, building, floor and apartment "
                            "are required and must be unique."
                        )
                    }
                ),
                400,
            )

        # Check if another RESIDENT already has this exact unit
        existing_unit = (
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

        if existing_unit:
            return (
                jsonify(
                    {
                        "message": (
                            "There is already a RESIDENT assigned to this unit "
                            f"(B{building} - F{floor} - A{apartment})."
                        )
                    }
                ),
                400,
            )

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

@admin_bp.route("/residents/<int:user_id>/profile", methods=["GET"])
def superadmin_get_resident_profile(user_id: int):
    """
    SUPERADMIN can see a resident's profile (user + person_details).
    """
    current_user = get_current_user_from_request(allowed_roles=["SUPERADMIN"])

    user = User.query.get_or_404(user_id)

    if user.role != "RESIDENT":
        return jsonify({"message": "Target user is not a resident."}), 400

    person = PersonDetails.query.filter_by(user_id=user.id).first()
    if not person:
        return jsonify({"message": "Person details not found for this user."}), 404

    return jsonify(
        {
            "user": {
                "id": user.id,
                "username": user.username,
                "role": user.role,
                "can_edit_profile": user.can_edit_profile,
            },
            "person": {
                "full_name": person.full_name,
                "building": person.building,
                "floor": person.floor,
                "apartment": person.apartment,
                "phone": person.phone,
            },
        }
    )

@admin_bp.route("/residents/<int:user_id>/profile", methods=["POST"])
def superadmin_update_resident_profile(user_id: int):
    """
    SUPERADMIN can update a resident's full_name, phone, and/or password.
    This is NOT limited by can_edit_profile (that's only for the resident self-edit).
    """
    current_user = get_current_user_from_request(allowed_roles=["SUPERADMIN"])

    user = User.query.get_or_404(user_id)

    if user.role != "RESIDENT":
        return jsonify({"message": "Target user is not a resident."}), 400

    person = PersonDetails.query.filter_by(user_id=user.id).first()
    if not person:
        return jsonify({"message": "Person details not found for this user."}), 404

    data = request.get_json() or {}
    full_name = (data.get("full_name") or "").strip()
    phone = (data.get("phone") or "").strip()
    new_password = (data.get("password") or "").strip()
    reset_edit_flag = bool(data.get("reset_can_edit_profile", False))

    if full_name:
        person.full_name = full_name
    if phone:
        person.phone = phone

    if new_password:
        user.set_password(new_password)

    if reset_edit_flag:
        user.can_edit_profile = True

    db.session.commit()

    return jsonify({"message": "Resident profile updated successfully."})

@admin_bp.route("/invoices/<int:invoice_id>/status", methods=["POST"])
def superadmin_update_invoice_status(invoice_id: int):
    """
    SUPERADMIN can flip invoice status between PAID and UNPAID.
    NOTE: This only updates the invoice itself. It does NOT automatically create/delete payments.
    """
    current_user = get_current_user_from_request(allowed_roles=["SUPERADMIN"])

    invoice = MaintenanceInvoice.query.get_or_404(invoice_id)

    data = request.get_json() or {}
    new_status = (data.get("status") or "").upper().strip()

    if new_status not in ("PAID", "UNPAID"):
        return jsonify({"message": "Invalid status. Only PAID or UNPAID allowed."}), 400

    if new_status == "PAID":
        invoice.status = "PAID"
        if not invoice.paid_date:
          invoice.paid_date = datetime.utcnow().date()
    else:
        invoice.status = "UNPAID"
        invoice.paid_date = None

    db.session.commit()

    return jsonify({"message": "Invoice status updated successfully.", "status": invoice.status})

@admin_bp.route("/online_payments/pending", methods=["GET"])
def admin_list_pending_online_payments():
    """
    List all pending online payments (Instapay) for admins to review.
    """
    current_user, error = get_current_user_from_request(allowed_roles=["ONLINE_ADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    q = (
        OnlinePayment.query
        .filter(OnlinePayment.status == "PENDING")
        .join(MaintenanceInvoice, OnlinePayment.invoice_id == MaintenanceInvoice.id)
        .join(User, OnlinePayment.resident_id == User.id)
        .outerjoin(PersonDetails, PersonDetails.user_id == User.id)
        .order_by(OnlinePayment.created_at.asc())
        .all()
    )

    result = []
    for op in q:
        inv = op.invoice
        resident = op.resident
        person = resident.person_details

        result.append({
            "id": op.id,
            "invoice_id": inv.id,
            "invoice_status": inv.status,
            "year": inv.year,
            "month": inv.month,
            "amount": float(op.amount),
            "resident_id": resident.id,
            "resident_username": resident.username,
            "resident_name": person.full_name if person else None,
            "building": person.building if person else None,
            "floor": person.floor if person else None,
            "apartment": person.apartment if person else None,
            "instapay_sender_id": op.instapay_sender_id,
            "transaction_ref": op.transaction_ref,
            "created_at": op.created_at.isoformat(),
        })

    return jsonify(result), 200

@admin_bp.route("/online_payments/<int:payment_id>/approve", methods=["POST"])
def admin_approve_online_payment(payment_id: int):
    """
    Approve an online Instapay payment:
    - Mark online payment as APPROVED
    - Mark invoice as PAID
    - Create Payment record with method='ONLINE'
    """
    current_user, error = get_current_user_from_request(allowed_roles=["ONLINE_ADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    data = request.get_json() or {}
    extra_notes = data.get("notes")

    op = OnlinePayment.query.get(payment_id)
    if not op:
        return jsonify({"message": "online payment not found"}), 404

    if op.status != "PENDING":
        return jsonify({"message": "online payment is not pending"}), 400

    invoice = op.invoice
    if invoice.status == "PAID":
        return jsonify({"message": "invoice already paid"}), 400

    # Mark invoice as PAID
    invoice.status = "PAID"
    invoice.paid_date = date.today()

    # Create Payment record for this online operation
    base_note = f"Instapay TX {op.transaction_ref} from {op.instapay_sender_id}"
    full_note = base_note
    if extra_notes:
        full_note = f"{base_note} - {extra_notes}"

    payment = Payment(
        user_id=invoice.user_id,
        invoice_id=invoice.id,
        amount=op.amount,
        method="ONLINE",
        notes=full_note,
        collected_by_admin_id=current_user.id,
    )

    # Update OnlinePayment
    op.status = "APPROVED"
    op.confirmed_at = datetime.utcnow()
    op.confirmed_by_admin_id = current_user.id
    if extra_notes:
        op.notes = extra_notes

    db.session.add(payment)
    db.session.commit()

    return jsonify({"message": "online payment approved"}), 200

@admin_bp.route("/online_payments/<int:payment_id>/reject", methods=["POST"])
def admin_reject_online_payment(payment_id: int):
    """
    Reject an online Instapay payment:
    - Mark online payment as REJECTED
    - Set invoice back to UNPAID if it is in PENDING_CONFIRMATION
    """
    current_user, error = get_current_user_from_request(allowed_roles=["ONLINE_ADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    data = request.get_json() or {}
    extra_notes = data.get("notes")

    op = OnlinePayment.query.get(payment_id)
    if not op:
        return jsonify({"message": "online payment not found"}), 404

    if op.status != "PENDING":
        return jsonify({"message": "online payment is not pending"}), 400

    invoice = op.invoice

    # لو الفاتورة لسه في حالة PENDING_CONFIRMATION نرجعها UNPAID
    if invoice.status == "PENDING_CONFIRMATION":
        invoice.status = "UNPAID"
        invoice.paid_date = None

    op.status = "REJECTED"
    op.confirmed_at = datetime.utcnow()
    op.confirmed_by_admin_id = current_user.id
    if extra_notes:
        op.notes = extra_notes

    db.session.commit()

    return jsonify({"message": "online payment rejected"}), 200

@admin_bp.route("/buildings", methods=["GET"])
def superadmin_list_buildings():
    """
    SuperAdmin: list all distinct buildings that appear in PersonDetails.
    Used to assign them to admins.
    """
    current_user, error = get_current_user_from_request(allowed_roles=["SUPERADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    rows = (
        db.session.query(PersonDetails.building.label("building"))
        .filter(PersonDetails.building != "")
        .distinct()
        .order_by(PersonDetails.building.asc())
        .all()
    )

    buildings = [row.building for row in rows]
    return jsonify(buildings), 200

@admin_bp.route("/admins-with-buildings", methods=["GET"])
def superadmin_list_admins_with_buildings():
    """
    SuperAdmin: list all admins with the buildings assigned to each.
    """
    current_user, error = get_current_user_from_request(allowed_roles=["SUPERADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    admins = User.query.filter(User.role == "ADMIN").all()

    result = []
    for admin in admins:
        details = admin.person_details
        buildings = get_admin_allowed_buildings(admin.id)

        result.append(
            {
                "id": admin.id,
                "username": admin.username,
                "role": admin.role,
                "full_name": details.full_name if details else admin.username,
                "buildings": buildings,
            }
        )

    return jsonify(result), 200

@admin_bp.route("/admin_buildings", methods=["POST"])
def superadmin_add_admin_building():
    """
    SuperAdmin: assign a building to an admin.
    Body: { "admin_id": int, "building": "A1" }
    """
    current_user, error = get_current_user_from_request(allowed_roles=["SUPERADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    data = request.get_json() or {}
    admin_id = data.get("admin_id")
    building = (data.get("building") or "").strip()

    if not admin_id or not building:
        return jsonify({"message": "admin_id and building are required"}), 400

    admin = User.query.filter_by(id=admin_id, role="ADMIN").first()
    if not admin:
        return jsonify({"message": "admin not found or not an ADMIN"}), 404

    existing = AdminBuilding.query.filter_by(admin_id=admin_id, building=building).first()
    if existing:
        return jsonify({"message": "building already assigned to this admin"}), 400

    entry = AdminBuilding(admin_id=admin_id, building=building)
    db.session.add(entry)
    db.session.commit()

    return jsonify({"message": "building assigned"}), 201

@admin_bp.route("/admin_buildings", methods=["DELETE"])
def superadmin_remove_admin_building():
    """
    SuperAdmin: remove a building from an admin.
    Body: { "admin_id": int, "building": "A1" }
    """
    current_user, error = get_current_user_from_request(allowed_roles=["SUPERADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    data = request.get_json() or {}
    admin_id = data.get("admin_id")
    building = (data.get("building") or "").strip()

    if not admin_id or not building:
        return jsonify({"message": "admin_id and building are required"}), 400

    entry = AdminBuilding.query.filter_by(admin_id=admin_id, building=building).first()
    if not entry:
        return jsonify({"message": "assignment not found"}), 404

    db.session.delete(entry)
    db.session.commit()

    return jsonify({"message": "building removed"}), 200

