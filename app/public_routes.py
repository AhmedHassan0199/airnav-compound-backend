from flask import Blueprint
from datetime import datetime
from flask import Blueprint, jsonify, request
from sqlalchemy import func, cast, Integer, case, and_
from sqlalchemy.orm import aliased

from app import db
from app.models import User, PersonDetails, Payment, MaintenanceInvoice, FundRaiser

public_bp = Blueprint("public_bp", __name__)

@public_bp.route("/buildings/<string:building>/units-status", methods=["GET"])
def public_building_units_status(building: str):
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)

    now = datetime.now()
    if not year:
        year = now.year
    if not month:
        month = now.month

    Collector = aliased(User)

    paid_amount_sum = func.coalesce(func.sum(Payment.amount), 0)

    any_online = func.max(
        case((Collector.role == "ONLINE_ADMIN", 1), else_=0)
    )
    any_payment = func.max(
        case((Payment.id.isnot(None), 1), else_=0)
    )

    payment_method = case(
        (any_online == 1, "ONLINE"),
        (any_payment == 1, "CASH"),
        else_=None,
    )

    rows = (
        db.session.query(
            PersonDetails.user_id.label("user_id"),
            PersonDetails.full_name.label("full_name"),
            PersonDetails.building.label("building"),
            PersonDetails.floor.label("floor"),
            PersonDetails.apartment.label("apartment"),

            MaintenanceInvoice.id.label("invoice_id"),
            MaintenanceInvoice.amount.label("invoice_amount"),
            MaintenanceInvoice.status.label("invoice_status"),

            paid_amount_sum.label("paid_amount"),
            payment_method.label("payment_method"),
        )
        .join(User, User.id == PersonDetails.user_id)
        .outerjoin(
            MaintenanceInvoice,
            and_(
                MaintenanceInvoice.user_id == User.id,
                MaintenanceInvoice.year == year,
                MaintenanceInvoice.month == month,
            )
        )
        .outerjoin(Payment, Payment.invoice_id == MaintenanceInvoice.id)
        .outerjoin(Collector, Collector.id == Payment.collected_by_admin_id)
        .filter(
            User.role == "RESIDENT",
            PersonDetails.building == building,
        )
        .group_by(
            PersonDetails.user_id,
            PersonDetails.full_name,
            PersonDetails.building,
            PersonDetails.floor,
            PersonDetails.apartment,
            MaintenanceInvoice.id,
            MaintenanceInvoice.amount,
            MaintenanceInvoice.status,
        )
        .order_by(
            cast(PersonDetails.floor, Integer).asc(),
            cast(PersonDetails.apartment, Integer).asc(),
        )
        .all()
    )

    result = []
    for r in rows:
        invoice_id = r.invoice_id
        invoice_amount = float(r.invoice_amount) if r.invoice_amount is not None else 0.0
        paid_amount = float(r.paid_amount or 0)

        paid_current_month = (r.invoice_status == "PAID") if invoice_id else False

        result.append({
            "user_id": int(r.user_id),
            "full_name": r.full_name,
            "building": r.building,
            "floor": r.floor,
            "apartment": r.apartment,

            "year": year,
            "month": month,

            "invoice_id": int(invoice_id) if invoice_id else None,
            "invoice_amount": invoice_amount,
            "paid_current_month": bool(paid_current_month),

            "paid_amount": paid_amount,
            "payment_method": r.payment_method,
        })

    return jsonify({
        "building": building,
        "year": year,
        "month": month,
        "units": result
    }), 200

@public_bp.route("/fundraisers", methods=["GET"])
def public_fundraisers():
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)

    q = FundRaiser.query
    if year:
        q = q.filter(FundRaiser.year == year)
    if month:
        q = q.filter(FundRaiser.month == month)

    q = q.order_by(FundRaiser.year.desc(), FundRaiser.month.desc(), FundRaiser.id.desc())

    rows = q.all()
    return jsonify([{
        "id": r.id,
        "name": r.name,
        "amount": float(r.amount),
        "year": r.year,
        "month": r.month,
    } for r in rows])