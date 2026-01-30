from datetime import date,datetime
from flask import Blueprint, jsonify, request, send_file, render_template, current_app
from io import BytesIO
from sqlalchemy import or_, func, and_
from sqlalchemy.orm import aliased
from decimal import Decimal

try:
    from weasyprint import HTML
    WEASYPRINT_AVAILABLE = True
except Exception:
    WEASYPRINT_AVAILABLE = False

from app import db
from app.models import (
    User,
    PersonDetails,
    MaintenanceInvoice,
    Payment,
    Settlement,
    OnlinePayment,
    AdminBuilding,
    FundRaiser,
    UnionLedgerEntry
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
    today = datetime.now()

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

def get_paid_invoices_for_month(year: int, month: int):
    """
    ترجع قائمة بالفواتير المدفوعة لشهر/سنة معينة،
    مع بيانات المقيم ونوع الدفع وتاريخ السداد.
    """
    # نجيب كل الفواتير PAID للمقيمين
    query = (
        db.session.query(MaintenanceInvoice, PersonDetails, User)
        .join(User, MaintenanceInvoice.user_id == User.id)
        .join(PersonDetails, PersonDetails.user_id == User.id)
        .filter(
            MaintenanceInvoice.status == "PAID",
            MaintenanceInvoice.year == year,
            MaintenanceInvoice.month == month,
            User.role == "RESIDENT",
        )
        .order_by(
            PersonDetails.building,
            PersonDetails.floor,
            PersonDetails.apartment,
            MaintenanceInvoice.id,
        )
    )

    rows = []
    serial = 1

    for invoice, person, user in query.all():
        # نحدد نوع الدفع و تاريخ السداد
        payment_type = "UNKNOWN"
        payment_date = invoice.paid_date

        # أولوية: لو فيه OnlinePayment APPROVED → نعتبرها Online
        online = (
            OnlinePayment.query
            .filter_by(invoice_id=invoice.id, status="APPROVED")
            .order_by(OnlinePayment.confirmed_at.desc())
            .first()
        )
        if online:
            payment_type = "ONLINE"
            payment_date = online.confirmed_at or payment_date
        else:
            # لو مفيش أونلاين، نشوف الـ Payment (الكاش)
            pay = (
                Payment.query
                .filter_by(invoice_id=invoice.id)
                .order_by(Payment.created_at.desc())
                .first()
            )
            if pay:
                payment_type = "CASH"
                payment_date = pay.created_at or payment_date

        if payment_date is not None:
            payment_date_str = payment_date.date().isoformat()  # YYYY-MM-DD
        else:
            payment_date_str = None

        rows.append(
            {
                "serial": serial,
                "full_name": person.full_name,
                "building": person.building,
                "floor": person.floor,
                "apartment": person.apartment,
                "payment_date": payment_date_str,
                "invoice_type": payment_type,  # "ONLINE" or "CASH" or "UNKNOWN"
                "amount": float(invoice.amount),
                "invoice_id": invoice.id,
            }
        )
        serial += 1

    return rows

def _get_paid_invoices_rows_for_month(year: int, month: int):
    """
    يرجّع list فيها كل المدفوعات (Payments) الخاصة بفواتير هذا الشهر/السنة.
    """
    UserCollected = aliased(User)
    q = (
        db.session.query(
            MaintenanceInvoice.id.label("invoice_id"),
            PersonDetails.full_name.label("resident_name"),
            PersonDetails.building.label("building"),
            PersonDetails.floor.label("floor"),
            PersonDetails.apartment.label("apartment"),
            Payment.created_at.label("payment_date"),
            Payment.method.label("payment_method"),
            UserCollected.role.label("collected_by_role"),  # 👈 إضافة مهمة
        )
        .join(Payment, Payment.invoice_id == MaintenanceInvoice.id)
        .join(User, User.id == MaintenanceInvoice.user_id)
        .join(PersonDetails, PersonDetails.user_id == User.id)
        .join(UserCollected, UserCollected.id == Payment.collected_by_admin_id)  # 👈 نجيب اللي جمع الفلوس
        .filter(
            MaintenanceInvoice.year == year,
            MaintenanceInvoice.month == month,
            # اختياري: نتأكد كمان إن حالة الفاتورة PAID
            MaintenanceInvoice.status == "PAID",
        )
        .order_by(
            PersonDetails.building,
            PersonDetails.floor,
            PersonDetails.apartment,
            Payment.created_at,
        )
    )

    rows = []
    for row in q.all():
        method = (row.payment_method or "").upper()

        # هل الدفع أونلاين بناءً على نوعه؟
        is_online_method = method in ("ONLINE", "INSTAPAY", "ONLINE_INSTAPAY")

        # هل الشخص اللي جمع الدفع هو ONLINE_ADMIN ؟
        collected_by_role = (row.collected_by_role or "").upper()
        is_online_admin = collected_by_role == "ONLINE_ADMIN"

        # النتيجة النهائية
        payment_type = "ONLINE" if is_online_method or is_online_admin else "CASH"

        rows.append(
            {
                "invoice_id": row.invoice_id,
                "resident_name": row.resident_name,
                "building": row.building,
                "floor": row.floor,
                "apartment": row.apartment,
                "payment_date": row.payment_date.isoformat()
                if row.payment_date
                else None,
                "payment_type": payment_type,
            }
        )

    return rows

@admin_bp.route("/residents", methods=["GET"])
def admin_search_residents():
    """
    Search residents by name / building / floor / apartment / username.
    Only ADMIN and SUPERADMIN can use this.
    """

    building = request.args.get("building", type=str)
    floor = request.args.get("floor", type=str)
    apartment = request.args.get("apartment", type=str)

    current_user, error = get_current_user_from_request(
        allowed_roles=["ADMIN", "SUPERADMIN","ONLINE_ADMIN"]
    )
    if error:
        message, status = error
        return jsonify({"message": message}), status


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
    
    if building:
        q = q.filter(PersonDetails.building == building)
    if floor:
        q = q.filter(PersonDetails.floor == floor)
    if apartment:
        q = q.filter(PersonDetails.apartment == apartment)

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
        payment = Payment.query.filter_by(invoice_id=inv.id).order_by(Payment.created_at.desc()).first()

        if payment:
            if payment.collected_by.role == "ONLINE_ADMIN":
                payment_type = "ONLINE"
            else:
                payment_type = "CASH"
            payment_date = payment.created_at.isoformat()
        else:
            payment_type = None
            payment_date = None

        result.append({
            "id": inv.id,
            "year": inv.year,
            "month": inv.month,
            "amount": float(inv.amount),
            "status": inv.status,
            "due_date": inv.due_date.isoformat() if inv.due_date else None,
            "paid_date": payment_date,  # override with real payment date
            "payment_type": payment_type,  # CASH / ONLINE / None
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
    invoice.paid_date = datetime.now()

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
    Also handles online_payments FK constraint.
    """
    current_user, error = get_current_user_from_request(
        allowed_roles=["ADMIN", "ONLINE_ADMIN"]
    )
    if error:
        message, status = error
        return jsonify({"message": message}), status

    invoice = MaintenanceInvoice.query.filter_by(id=invoice_id).first()
    if not invoice:
        return jsonify({"message": "invoice not found"}), 404

    # Restrict admin access by building
    if current_user.role == "ADMIN":
        resident = User.query.get(invoice.user_id)
        details = resident.person_details if resident else None
        allowed = get_admin_allowed_buildings(current_user.id)
        if details and details.building not in allowed:
            return jsonify({"message": "not allowed: resident outside your buildings"}), 403

    # Safety: don't delete PAID invoice
    if invoice.status == "PAID":
        return jsonify({"message": "cannot delete a PAID invoice"}), 400

    # If invoice itself is pending confirmation, block delete
    if invoice.status == "PENDING_CONFIRMATION":
        return jsonify({
            "message": "cannot delete invoice while an online payment is pending confirmation"
        }), 400

    # Block delete if there is any OnlinePayment still pending for this invoice
    pending_online = OnlinePayment.query.filter_by(
        invoice_id=invoice.id, status="PENDING"
    ).count()
    if pending_online > 0:
        return jsonify({
            "message": "cannot delete invoice while an online payment request is still pending"
        }), 400

    # Block delete if there are cash payments recorded
    payments_count = Payment.query.filter_by(invoice_id=invoice.id).count()
    if payments_count > 0:
        return jsonify({"message": "cannot delete invoice that has payments recorded"}), 400

    # ✅ IMPORTANT FIX:
    # Delete related online_payments (REJECTED/APPROVED) rows first to avoid FK update-to-NULL.
    # Approved usually implies invoice got PAID, but we keep this safe anyway.
    OnlinePayment.query.filter_by(invoice_id=invoice.id).delete(synchronize_session=False)

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
    today = datetime.now()
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


@admin_bp.route("/superadmin/residents/<int:user_id>/profile", methods=["POST"])
def superadmin_update_resident_profile(user_id):
    """
    SUPERADMIN: Update a resident's profile and optionally reset password.
    Fields:
      - full_name (required)
      - building (required)
      - floor (required)
      - apartment (required)
      - phone (required)
      - password (optional; if provided, resets password)
      - can_edit_profile (optional bool; default keeps current)
    """
    current_user, error = get_current_user_from_request(allowed_roles=["SUPERADMIN"])
    if error:
        msg, status = error
        return jsonify({"message": msg}), status

    user = User.query.filter_by(id=user_id, role="RESIDENT").first()
    if not user:
        return jsonify({"message": "resident not found"}), 404

    data = request.get_json() or {}

    full_name = (data.get("full_name") or "").strip()
    building = (data.get("building") or "").strip()
    floor = (data.get("floor") or "").strip()
    apartment = (data.get("apartment") or "").strip()
    phone = (data.get("phone") or "").strip()
    new_password = (data.get("password") or "").strip()
    can_edit_profile = data.get("can_edit_profile", None)

    if not full_name or not building or not floor or not apartment or not phone:
        return jsonify({"message": "full_name, building, floor, apartment, and phone are required."}), 400

    details = PersonDetails.query.filter_by(user_id=user.id).first()
    if not details:
        return jsonify({"message": "person details not found for this user"}), 404

    # Update details
    details.full_name = full_name
    details.building = building
    details.floor = floor
    details.apartment = apartment
    details.phone = phone

    # Optional password reset
    if new_password:
        user.set_password(new_password)

    # Optional can_edit_profile override
    if can_edit_profile is not None:
        user.can_edit_profile = bool(can_edit_profile)

    db.session.commit()

    return jsonify({
        "message": "resident profile updated successfully",
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "can_edit_profile": user.can_edit_profile,
        },
        "person": {
            "full_name": details.full_name,
            "building": details.building,
            "floor": details.floor,
            "apartment": details.apartment,
            "phone": details.phone,
        },
    })

@admin_bp.route("/superadmin/invoices/<int:invoice_id>", methods=["PUT", "PATCH"])
def superadmin_update_invoice_status(invoice_id: int):
    """
    SUPERADMIN: تحديث حالة الفاتورة.
    - لو من PAID → UNPAID: نمسح كل الـ Payments المرتبطة بالفاتورة.
    - لو من أي حالة → PAID: نضيف Payment (لو مش موجودة) ونحدّث paid_date.
    """
    user, error = get_current_user_from_request(allowed_roles=["SUPERADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    data = request.get_json() or {}
    new_status = (data.get("status") or "").strip().upper()

    allowed_statuses = {
        "UNPAID",
        "PAID",
        "OVERDUE",
        "PENDING",
        "PENDING_CONFIRMATION",
    }

    if new_status not in allowed_statuses:
        return jsonify({"message": "invalid status"}), 400

    invoice = MaintenanceInvoice.query.get(invoice_id)
    if not invoice:
        return jsonify({"message": "invoice not found"}), 404

    old_status = invoice.status

    # 1) لو من PAID → UNPAID → امسح الـ payments
    if old_status == "PAID" and new_status == "UNPAID":
        Payment.query.filter_by(invoice_id=invoice.id).delete(
            synchronize_session=False
        )
        invoice.paid_date = None

    # 2) لو من أي حاجة → PAID → تأكد فيه Payment وحدث paid_date
    elif new_status == "PAID":
        existing_payment = Payment.query.filter_by(invoice_id=invoice.id).first()
        if not existing_payment:
            # نحاول نجيب الـ resident بتاع الفاتورة
            resident_user = User.query.filter_by(id=invoice.user_id).first()
            if not resident_user:
                return jsonify({"message": "resident user not found"}), 404

            # create payment as CASH by default (أو ONLINE لو عايز لوجيك تاني)
            p = Payment(
                user_id=resident_user.id,
                invoice_id=invoice.id,
                amount=invoice.amount,
                method="CASH",
                collected_by_admin_id=user.id,
                created_at=datetime.now(),
                notes="Created automatically by SUPERADMIN status update",
            )
            db.session.add(p)

        # في كل الأحوال لو بقت PAID خَلّي paid_date = now (لو مش متسجل قبل كده)
        if not invoice.paid_date:
            invoice.paid_date = datetime.now()

    # 3) بقية الحالات (OVERDUE, PENDING, PENDING_CONFIRMATION)
    # لا نلمس الـ payments، بس نغيّر status فقط
    invoice.status = new_status
    db.session.commit()

    return jsonify({"message": "invoice status updated successfully"}), 200
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
    invoice.paid_date = datetime.now()

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

@admin_bp.route("/paid-invoices", methods=["GET"])
def superadmin_paid_invoices_json():
    user, error = get_current_user_from_request(allowed_roles=["SUPERADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    try:
        year = int(request.args.get("year", "0"))
        month = int(request.args.get("month", "0"))
    except ValueError:
        return jsonify({"message": "invalid year or month"}), 400

    if year < 2000 or not (1 <= month <= 12):
        return jsonify({"message": "invalid year or month"}), 400

    rows = _get_paid_invoices_rows_for_month(year, month)

    return jsonify({
        "year": year,
        "month": month,
        "rows": rows,
    })

@admin_bp.route("/paid-invoices/pdf", methods=["GET"])
def superadmin_paid_invoices_pdf():
    user, error = get_current_user_from_request(allowed_roles=["SUPERADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    try:
        year = int(request.args.get("year", "0"))
        month = int(request.args.get("month", "0"))
    except ValueError:
        return jsonify({"message": "invalid year or month"}), 400

    if year < 2000 or not (1 <= month <= 12):
        return jsonify({"message": "invalid year or month"}), 400

    rows = _get_paid_invoices_rows_for_month(year, month)

    # لو مفيش بيانات، ممكن ترجع 404 أو PDF فاضي، زي ما تحب
    if not rows:
        return jsonify({"message": "no paid invoices for this month"}), 404

    # هنستخدم تيمبلت HTML شبيه بالـ invoice.html بس جدول
    html_str = render_template(
        "paid_invoices_report.html",
        year=year,
        month=month,
        rows=rows,
    )

    pdf_io = BytesIO()
    HTML(string=html_str, base_url=current_app.root_path).write_pdf(pdf_io)
    pdf_io.seek(0)

    filename = f"paid_invoices_{year}_{month}.pdf"
    return send_file(
        pdf_io,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )

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

@admin_bp.route("/superadmin/residents/<int:user_id>/profile", methods=["GET"])
def superadmin_get_resident_profile(user_id):
    """
    SUPERADMIN: Fetch full profile of a RESIDENT user
    (User + PersonDetails + can_edit_profile).
    """
    current_user, error = get_current_user_from_request(allowed_roles=["SUPERADMIN"])
    if error:
        msg, status = error
        return jsonify({"message": msg}), status

    user = User.query.filter_by(id=user_id, role="RESIDENT").first()
    if not user:
        return jsonify({"message": "resident not found"}), 404

    details = PersonDetails.query.filter_by(user_id=user.id).first()

    return jsonify({
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "can_edit_profile": user.can_edit_profile,
        },
        "person": {
            "full_name": details.full_name if details else None,
            "building": details.building if details else None,
            "floor": details.floor if details else None,
            "apartment": details.apartment if details else None,
            "phone": details.phone if details else None,
        },
    })

def _get_union_balance():
    last = UnionLedgerEntry.query.order_by(UnionLedgerEntry.id.desc()).first()
    return float(last.balance_after) if last else 0.0

@admin_bp.route("/superadmin/fundraisers", methods=["POST"])
def superadmin_create_fundraiser():

    user, error = get_current_user_from_request(allowed_roles=["SUPERADMIN"])
    if error:
        msg, status = error
        return jsonify({"message": msg}), status
    
    data = request.get_json(force=True) or {}

    name = (data.get("name") or "").strip()
    amount = data.get("amount")
    year = data.get("year")
    month = data.get("month")

    if not name:
        return jsonify({"message": "Name is required"}), 400

    try:
        amount = float(amount)
    except Exception:
        return jsonify({"message": "Amount must be a number"}), 400

    if amount <= 0:
        return jsonify({"message": "Amount must be > 0"}), 400

    try:
        year = int(year)
        month = int(month)
    except Exception:
        return jsonify({"message": "Year/Month are required"}), 400

    if month < 1 or month > 12:
        return jsonify({"message": "Month must be 1..12"}), 400

    # current user from your auth context (adjust if your project uses g.user / current_user)
    # user = getattr(request, "current_user", None)  # <-- عدّل السطر ده حسب مشروعك

    fr = FundRaiser(
        name=name,
        amount=Decimal(str(round(amount, 2))),
        year=year,
        month=month,
        created_by_id=user.id if user else None
    )
    db.session.add(fr)

    # Reflect in Union ledger as CREDIT
    old_balance = _get_union_balance()
    new_balance = old_balance + amount

    entry = UnionLedgerEntry(
        date=datetime.utcnow(),
        description=f"لوحة الشرف: {name} ({month}/{year})",
        debit=Decimal("0"),
        credit=Decimal(str(round(amount, 2))),
        balance_after=Decimal(str(round(new_balance, 2))),
        entry_type="FUNDRAISING",
        created_by_id =user.id
    )
    db.session.add(entry)

    db.session.commit()

    return jsonify({
        "id": fr.id,
        "name": fr.name,
        "amount": float(fr.amount),
        "year": fr.year,
        "month": fr.month,
        "created_at": fr.created_at.isoformat(),
        "union_balance_after": float(entry.balance_after)
    }), 201

@admin_bp.route("/superadmin/fundraisers", methods=["GET"])
def superadmin_list_fundraisers():

    current_user, error = get_current_user_from_request(allowed_roles=["SUPERADMIN"])
    if error:
        msg, status = error
        return jsonify({"message": msg}), status

    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)

    q = FundRaiser.query
    if year:
        q = q.filter(FundRaiser.year == year)
    if month:
        q = q.filter(FundRaiser.month == month)

    q = q.order_by(FundRaiser.year.desc(), FundRaiser.month.desc(), FundRaiser.id.asc())

    rows = q.all()
    return jsonify([{
        "id": r.id,
        "name": r.name,
        "amount": float(r.amount),
        "year": r.year,
        "month": r.month,
        "created_at": r.created_at.isoformat(),
    } for r in rows])

@admin_bp.route("/superadmin/fundraisers/<int:fundraiser_id>", methods=["PUT"])
def superadmin_update_fundraiser(fundraiser_id: int):
    current_user, error = get_current_user_from_request(allowed_roles=["SUPERADMIN"])
    if error:
        return error

    fr = db.session.query(FundRaiser).get(fundraiser_id)
    if not fr:
        return jsonify({"message": "Fundraiser not found"}), 404

    data = request.get_json(silent=True) or {}

    new_name = (data.get("name") or fr.name).strip()
    new_amount = data.get("amount", None)

    if not new_name:
        return jsonify({"message": "name is required"}), 400

    # amount optional في update (لو مش مبعوت يبقى مفيش تعديل رصيد)
    amount_changed = False
    old_amount = float(fr.amount)
    if new_amount is not None:
        try:
            new_amount = float(new_amount)
        except:
            return jsonify({"message": "amount must be a number"}), 400
        if new_amount <= 0:
            return jsonify({"message": "amount must be > 0"}), 400
        amount_changed = (abs(new_amount - old_amount) > 1e-9)

    # Update record
    fr.name = new_name
    if new_amount is not None:
        fr.amount = new_amount
    fr.updated_at = datetime.utcnow()

    # Ledger adjustment if amount changed
    union_balance_after = None
    if amount_changed:
        delta = float(new_amount) - old_amount  # + means increase, - means decrease
        prev_balance = _get_union_balance()
        new_balance = prev_balance + delta

        entry = UnionLedgerEntry(
            date=datetime.utcnow(),
            description=f"تعديل لوحة الشرف - {new_name} ({fr.month}/{fr.year})",
            debit=float(-delta) if delta < 0 else 0.0,
            credit=float(delta) if delta > 0 else 0.0,
            balance_after=new_balance,
            entry_type="FUNDRAISER_ADJUST",
            created_by_id=current_user.id,
        )
        db.session.add(entry)
        union_balance_after = new_balance

    db.session.commit()

    return jsonify({
        "id": fr.id,
        "name": fr.name,
        "amount": float(fr.amount),
        "year": fr.year,
        "month": fr.month,
        "updated_at": fr.updated_at.isoformat() if fr.updated_at else None,
        "union_balance_after": union_balance_after
    })