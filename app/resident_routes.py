from flask import Blueprint, jsonify, send_file, render_template, current_app
from app.models import PersonDetails, MaintenanceInvoice
from .auth.routes import get_current_user_from_request
from io import BytesIO
from weasyprint import HTML

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

@resident_bp.route("/invoices/<int:invoice_id>/pdf", methods=["GET"])
def resident_invoice_pdf(invoice_id: int):
    user, error = get_current_user_from_request(allowed_roles=["RESIDENT"])
    if error:
        message, status = error
        return jsonify({"message": message}), status

    invoice = (
        MaintenanceInvoice.query
        .filter_by(id=invoice_id, user_id=user.id)
        .first()
    )
    if not invoice:
        return jsonify({"message": "invoice not found"}), 404

    details = PersonDetails.query.filter_by(user_id=user.id).first()

    full_name = details.full_name if details else user.username
    building = details.building if details else "-"
    floor = details.floor if details else "-"
    apartment = details.apartment if details else "-"

    context = {
        "full_name": full_name,
        "building": building,
        "floor": floor,
        "apartment": apartment,
        "year": invoice.year,
        "month": invoice.month,
        "amount": f"{float(invoice.amount):.2f}",
        "status": invoice.status,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "paid_date": invoice.paid_date.isoformat() if invoice.paid_date else None,
        "notes": invoice.notes,
    }

    # Render HTML using Flask template
    html_str = render_template("invoice.html", **context)

    # Generate PDF in memory
    pdf_io = BytesIO()
    # base_url is important so WeasyPrint can resolve relative URLs (if we add fonts/images later)
    HTML(string=html_str, base_url=current_app.root_path).write_pdf(pdf_io)
    pdf_io.seek(0)

    filename = f"maintenance_invoice_{invoice.year}_{invoice.month}.pdf"
    return send_file(
        pdf_io,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )
