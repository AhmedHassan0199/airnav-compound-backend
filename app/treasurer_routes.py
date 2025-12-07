from datetime import date, datetime
from flask import Blueprint, jsonify, request
from sqlalchemy import func, cast, Integer, case, and_, or_
import os

from app import db
from app.models import User, PersonDetails, Payment, Settlement, MaintenanceInvoice, UnionLedgerEntry, Expense, NotificationSubscription
from .auth.routes import get_current_user_from_request
from app.fcm import send_push_v1

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

    admins = (User.query.filter(or_(User.role == "ADMIN", User.role == "ONLINE_ADMIN")).all())

    results = []
    for admin in admins:
        details = PersonDetails.query.filter_by(user_id=admin.id).first()
        summary = _admin_summary_for_treasurer(admin.id)

        results.append(
            {
                "id": admin.id,
                "username": admin.username,
                "full_name": details.full_name if details else admin.username,
                "role": admin.role,
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

    admin = (
        User.query
        .filter(
            User.id == admin_id,
            or_(User.role == "ADMIN", User.role == "ONLINE_ADMIN")
        )
        .first()
    )
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
                "role": admin.role,
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

    admin = (
        User.query
        .filter(
            User.id == admin_id,
            or_(User.role == "ADMIN", User.role == "ONLINE_ADMIN")
        )
        .first()
    )
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
        created_at=datetime.now(),
        notes=notes,
    )

    db.session.add(settlement)
    # Ledger: settlement increases union balance (credit)
    current_balance = get_union_balance()
    new_balance = current_balance + amount_val

    ledger_entry = UnionLedgerEntry(
        date=datetime.now(),
        description=f"تسوية من مسؤول التحصيل {admin.username}",
        debit=0,
        credit=amount_val,
        balance_after=new_balance,
        entry_type="SETTLEMENT",
        created_by_id=current_user.id,
    )
    db.session.add(ledger_entry)
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
    current_user, error = get_current_user_from_request(allowed_roles=["TREASURER", "SUPERADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    today = datetime.now()
    first_of_month = date(today.year, today.month, 1)

    # إجمالي المبالغ المحصلة من السكان (عن طريق مسؤولي التحصيل)
    total_collected = (
        db.session.query(func.coalesce(func.sum(Payment.amount), 0))
        .scalar()
        or 0
    )

    # إجمالي ما تم تسويته من مسؤولي التحصيل إلى الاتحاد
    total_settled = (
        db.session.query(func.coalesce(func.sum(Settlement.amount), 0))
        .scalar()
        or 0
    )

    # إجمالي المصروفات
    total_expenses = (
        db.session.query(func.coalesce(func.sum(Expense.amount), 0))
        .scalar()
        or 0
    )

    # رصيد الاتحاد الحالي حسب دفتر القيود (الأصح)
    union_balance = get_union_balance()

    # تحصيل اليوم
    today_collected = (
        db.session.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.created_at == today)
        .scalar()
        or 0
    )

    # تحصيل هذا الشهر
    this_month_collected = (
        db.session.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.created_at >= first_of_month)
        .scalar()
        or 0
    )

    # Only consider invoices that should already be due
    # (exclude future months from the stats)
    total_invoices = (
        db.session.query(func.count(MaintenanceInvoice.id))
        .filter(MaintenanceInvoice.due_date <= today)
        .scalar()
        or 0
    )

    paid_invoices = (
        db.session.query(func.count(MaintenanceInvoice.id))
        .filter(
            MaintenanceInvoice.status == "PAID",
            MaintenanceInvoice.due_date <= today,
        )
        .scalar()
        or 0
    )

    unpaid_invoices = total_invoices - paid_invoices


    return jsonify(
        {
            "total_collected": float(total_collected),
            "total_settled": float(total_settled),
            "total_expenses": float(total_expenses),
            "union_balance": float(union_balance),
            "today_collected": float(today_collected),
            "this_month_collected": float(this_month_collected),
            "total_invoices": int(total_invoices),
            "paid_invoices": int(paid_invoices),
            "unpaid_invoices": int(unpaid_invoices),
        }
    )

def get_union_balance():
    last_entry = (
        db.session.query(UnionLedgerEntry)
        .order_by(UnionLedgerEntry.id.desc())
        .first()
    )
    if last_entry:
        return float(last_entry.balance_after)
    return 0.0

@treasurer_bp.route("/ledger", methods=["GET"])
def treasurer_ledger_list():
    """
    List union ledger entries (latest first).
    Optional query param: limit (default 50)
    """
    current_user, error = get_current_user_from_request(allowed_roles=["TREASURER", "SUPERADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50

    entries = (
        db.session.query(UnionLedgerEntry, User)
        .join(User, UnionLedgerEntry.created_by_id == User.id)
        .order_by(UnionLedgerEntry.id.desc())
        .limit(limit)
        .all()
    )

    result = []
    for entry, user in entries:
        result.append(
            {
                "id": entry.id,
                "date": entry.date.isoformat(),
                "description": entry.description,
                "debit": float(entry.debit),
                "credit": float(entry.credit),
                "balance_after": float(entry.balance_after),
                "entry_type": entry.entry_type,
                "created_by": user.username,
            }
        )

    return jsonify(result)

@treasurer_bp.route("/expenses", methods=["POST"])
def treasurer_create_expense():
    """
    Treasurer records a union expense.
    Also writes a ledger entry (debit).
    """
    current_user, error = get_current_user_from_request(allowed_roles=["TREASURER", "SUPERADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    data = request.get_json() or {}
    amount = data.get("amount")
    description = data.get("description", "").strip()
    category = data.get("category", "").strip() or None

    if amount is None or not description:
        return jsonify({"message": "amount and description are required"}), 400

    try:
        amount_val = float(amount)
        if amount_val <= 0:
            raise ValueError()
    except Exception:
        return jsonify({"message": "invalid amount"}), 400

    exp = Expense(
        amount=amount_val,
        description=description,
        category=category,
        date=datetime.now(),
        created_by_id=current_user.id,
    )
    db.session.add(exp)

    # Ledger: expense decreases union balance (debit)
    current_balance = get_union_balance()
    new_balance = current_balance - amount_val

    ledger_entry = UnionLedgerEntry(
        date=datetime.now(),
        description=f"مصروف: {description}",
        debit=amount_val,
        credit=0,
        balance_after=new_balance,
        entry_type="EXPENSE",
        created_by_id=current_user.id,
    )
    db.session.add(ledger_entry)

    db.session.commit()

    return jsonify({"message": "expense recorded"}), 201

@treasurer_bp.route("/expenses", methods=["GET"])
def treasurer_list_expenses():
    """
    List recent expenses. Optional ?limit=
    """
    current_user, error = get_current_user_from_request(allowed_roles=["TREASURER", "SUPERADMIN"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50

    expenses = (
        db.session.query(Expense, User)
        .join(User, Expense.created_by_id == User.id)
        .order_by(Expense.date.desc(), Expense.id.desc())
        .limit(limit)
        .all()
    )

    result = []
    for exp, user in expenses:
        result.append(
            {
                "id": exp.id,
                "date": exp.date.isoformat(),
                "amount": float(exp.amount),
                "category": exp.category,
                "description": exp.description,
                "created_by": user.username,
            }
        )

    return jsonify(result)


def _get_late_residents_data():
    """
    Core logic to compute late residents:
    - current month unpaid after day 5
    - ≥3 months overdue
    - partial payments
    Returns dict: { "today": ..., "cutoff_day": ..., "late_residents": [...] }
    """
    today = datetime.now()
    cutoff_day = 5

    inv_rows = (
        db.session.query(
            MaintenanceInvoice.id,
            MaintenanceInvoice.user_id,
            MaintenanceInvoice.year,
            MaintenanceInvoice.month,
            MaintenanceInvoice.amount,
            func.coalesce(func.sum(Payment.amount), 0).label("paid_amount"),
        )
        .outerjoin(Payment, Payment.invoice_id == MaintenanceInvoice.id)
        .group_by(MaintenanceInvoice.id)
        .all()
    )

    per_user = {}

    for row in inv_rows:
        amount = float(row.amount)
        paid = float(row.paid_amount or 0)
        unpaid = amount - paid

        if unpaid <= 0:
            continue

        months_diff = (today.year - row.year) * 12 + (today.month - row.month)

        is_current_month_late = (
            row.year == today.year
            and row.month == today.month
            and today.day > cutoff_day
            and unpaid > 0
        )
        has_3_plus = months_diff >= 3 and unpaid > 0
        is_partial = paid > 0 and unpaid > 0

        if not (is_current_month_late or has_3_plus or is_partial):
            continue

        info = per_user.setdefault(
            row.user_id,
            {
                "user_id": row.user_id,
                "total_overdue_amount": 0.0,
                "current_month_late": False,
                "more_than_3_months": False,
                "partial_payments": False,
                "overdue_months": [],
            },
        )

        info["total_overdue_amount"] += unpaid
        if is_current_month_late:
            info["current_month_late"] = True
        if has_3_plus:
            info["more_than_3_months"] = True
        if is_partial:
            info["partial_payments"] = True

        info["overdue_months"].append(
            {
                "year": row.year,
                "month": row.month,
                "amount": amount,
                "paid_amount": paid,
                "unpaid_amount": unpaid,
            }
        )

    if not per_user:
        return {
            "today": today.isoformat(),
            "cutoff_day": cutoff_day,
            "late_residents": [],
        }

    user_ids = list(per_user.keys())

    users_rows = (
        db.session.query(User, PersonDetails)
        .outerjoin(PersonDetails, PersonDetails.user_id == User.id)
        .filter(User.id.in_(user_ids))
        .all()
    )

    result = []
    for user, person in users_rows:
        info = per_user[user.id]
        result.append(
            {
                "user_id": user.id,
                "username": user.username,
                "full_name": person.full_name if person else user.username,
                "building": person.building if person else None,
                "floor": person.floor if person else None,
                "apartment": person.apartment if person else None,
                "phone": person.phone if person else None,
                "status_flags": {
                    "current_month_late": info["current_month_late"],
                    "more_than_3_months": info["more_than_3_months"],
                    "partial_payments": info["partial_payments"],
                },
                "total_overdue_amount": round(info["total_overdue_amount"], 2),
                "overdue_months": info["overdue_months"],
            }
        )

    return {
        "today": today.isoformat(),
        "cutoff_day": cutoff_day,
        "late_residents": result,
    }

@treasurer_bp.route("/late-residents", methods=["GET"])
def treasurer_late_residents():
    current_user, error = get_current_user_from_request(
        allowed_roles=["TREASURER", "SUPERADMIN"]
    )
    if error:
        msg, status = error
        return jsonify({"message": msg}), status

    data = _get_late_residents_data()
    return jsonify(data), 200

@treasurer_bp.route("/late-residents/notify-push", methods=["POST"])
def treasurer_notify_late_residents_push():
    """
    Sends a push notification to all late residents who have a notification subscription.
    """
    current_user, error = get_current_user_from_request(
        allowed_roles=["TREASURER", "SUPERADMIN"]
    )
    if error:
        msg, status = error
        return jsonify({"message": msg}), status

    data = _get_late_residents_data()
    late_residents = data["late_residents"]
    if not late_residents:
        return jsonify({"message": "لا يوجد سكان متأخرون حالياً.", "count": 0}), 200

    project_id = os.getenv("FIREBASE_PROJECT_ID")
    if not project_id:
        return jsonify({"message": "FIREBASE_PROJECT_ID not configured"}), 500

    total_targets = 0
    total_sent = 0
    total_failed = 0
    details = []

    for r in late_residents:
        user_id = r["user_id"]
        subs = NotificationSubscription.query.filter_by(user_id=user_id).all()
        if not subs:
            details.append(
                {
                    "user_id": user_id,
                    "full_name": r["full_name"],
                    "status": "no_subscription",
                }
            )
            continue

        total_targets += 1

        title = "تنبيه سداد صيانة"
        body = (
            f"عزيزي {r['full_name']}, يوجد مديونية صيانة قدرها "
            f"{r['total_overdue_amount']:.2f} جنيه على وحدتكم. "
            "برجاء السداد أو التواصل مع أمين الصندوق."
        )

        success_for_user = False
        for sub in subs:
            status_code, resp_text = send_push_v1(
                project_id, sub.token, title, body
            )
            if status_code == 200:
                success_for_user = True
                break  # one success per user is enough

        if success_for_user:
            total_sent += 1
            details.append(
                {
                    "user_id": user_id,
                    "full_name": r["full_name"],
                    "status": "sent",
                }
            )
        else:
            total_failed += 1
            details.append(
                {
                    "user_id": user_id,
                    "full_name": r["full_name"],
                    "status": "failed",
                }
            )

    return jsonify(
        {
            "total_late_residents": len(late_residents),
            "total_targets": total_targets,  # with at least one subscription
            "total_sent": total_sent,
            "total_failed": total_failed,
            "details": details,
        }
    ), 200

@treasurer_bp.route("/treasurer/buildings/paid-ranking", methods=["GET"])
def treasurer_buildings_paid_ranking():
    user, error = get_current_user_from_request(allowed_roles=["TREASURER"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)

    # default to current year/month if not provided
    now = datetime.now()
    if not year:
        year = now.year
    if not month:
        month = now.month

    rows = (
        db.session.query(
            PersonDetails.building.label("building"),
            func.sum(
                case(
                    [
                        (
                            and_(
                                MaintenanceInvoice.status == "PAID",
                                MaintenanceInvoice.year == year,
                                MaintenanceInvoice.month == month,
                            ),
                            1,
                        )
                    ],
                    else_=0,
                )
            ).label("paid_invoices"),
            func.max(
                cast(PersonDetails.apartment, Integer)
            ).label("max_apartment"),
        )
        # 🔴 IMPORTANT: only RESIDENT users
        .join(User, User.id == PersonDetails.user_id)
        .outerjoin(MaintenanceInvoice, MaintenanceInvoice.user_id == User.id)
        .filter(
            User.role == "RESIDENT",
            PersonDetails.building.isnot(None),
            PersonDetails.building != "",
        )
        .group_by(PersonDetails.building)
        .all()
    )

    buildings = []
    for row in rows:
        building = row.building
        paid_invoices = int(row.paid_invoices or 0)
        max_apt = row.max_apartment or 0

        # 7 floors per building as you specified
        total_apartments = max_apt * 7 if max_apt > 0 else 0

        if total_apartments > 0:
            percentage = (paid_invoices / total_apartments) * 100.0
        else:
            percentage = 0.0

        buildings.append(
            {
                "building": building,
                "paid_invoices": paid_invoices,
                "total_apartments": total_apartments,
                "percentage": round(percentage, 2),
            }
        )

    # sort descending by percentage
    buildings_sorted = sorted(
        buildings, key=lambda b: b["percentage"], reverse=True
    )

    top5 = buildings_sorted[:5]
    bottom5 = list(reversed(buildings_sorted))[:5]

    return jsonify(
        {
            "year": year,
            "month": month,
            "buildings": buildings_sorted,
            "top5": top5,
            "bottom5": bottom5,
        }
    )