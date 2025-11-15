from flask import Blueprint, jsonify, send_file, render_template, current_app
from app.models import PersonDetails, MaintenanceInvoice, User
from .auth.routes import get_current_user_from_request
from io import BytesIO
# Try importing WeasyPrint; on Windows this may fail
try:
    from weasyprint import HTML
    WEASYPRINT_AVAILABLE = True
except OSError:
    HTML = None
    WEASYPRINT_AVAILABLE = False



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
    # If WeasyPrint is not available locally (e.g. on Windows), fail gracefully
    if not WEASYPRINT_AVAILABLE:
        return jsonify({"message": "PDF generation is not available in this environment"}), 500

    user, error = get_current_user_from_request(
        allowed_roles=["RESIDENT", "ADMIN", "SUPERADMIN"]
    )
    if error:
        message, status = error
        return jsonify({"message": message}), status

    # Load invoice (for any user)
    invoice = MaintenanceInvoice.query.filter_by(id=invoice_id).first()
    if not invoice:
        return jsonify({"message": "invoice not found"}), 404

    # Permissions:
    # - RESIDENT: must own this invoice
    # - ADMIN / SUPERADMIN: can view any invoice
    if user.role == "RESIDENT" and invoice.user_id != user.id:
        return jsonify({"message": "not allowed to access this invoice"}), 403

    # Only PAID invoices can be printed
    if invoice.status != "PAID":
        return jsonify({"message": "invoice is not paid yet"}), 403

    # Always show the RESIDENT info in the PDF
    resident_user = User.query.filter_by(id=invoice.user_id, role="RESIDENT").first()
    if not resident_user:
        return jsonify({"message": "resident not found for this invoice"}), 404

    details = PersonDetails.query.filter_by(user_id=resident_user.id).first()

    full_name = details.full_name if details else resident_user.username
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

    html_str = render_template("invoice.html", **context)

    pdf_io = BytesIO()
    HTML(string=html_str, base_url=current_app.root_path).write_pdf(pdf_io)
    pdf_io.seek(0)

    filename = f"maintenance_invoice_{invoice.year}_{invoice.month}.pdf"
    return send_file(
        pdf_io,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )

