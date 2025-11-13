from flask import Blueprint, jsonify, send_file
from app.models import PersonDetails, MaintenanceInvoice
from .auth.routes import get_current_user_from_request
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

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

    # Create PDF in memory
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Simple layout (can prettify later)
    y = height - 80

    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, "فاتورة صيانة شهرية")
    y -= 40

    c.setFont("Helvetica", 11)
    c.drawString(50, y, f"المقيم: {details.full_name if details else user.username}")
    y -= 18
    if details:
        c.drawString(50, y, f"الوحدة: مبنى {details.building} - دور {details.floor} - شقة {details.apartment}")
        y -= 18

    c.drawString(50, y, f"الشهر: {invoice.month}/{invoice.year}")
    y -= 18
    c.drawString(50, y, f"القيمة: {float(invoice.amount):.2f} جنيه مصري")
    y -= 18
    c.drawString(50, y, f"الحالة: {invoice.status}")
    y -= 18
    if invoice.due_date:
        c.drawString(50, y, f"تاريخ الاستحقاق: {invoice.due_date.isoformat()}")
        y -= 18
    if invoice.paid_date:
        c.drawString(50, y, f"تاريخ السداد: {invoice.paid_date.isoformat()}")
        y -= 18

    if invoice.notes:
        y -= 10
        c.drawString(50, y, f"ملاحظات: {invoice.notes}")

    y -= 40
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(50, y, "تم إنشاء هذه الفاتورة من بوابة اتحاد شاغلين مدينة الملاحة الجوية.")

    c.showPage()
    c.save()
    buffer.seek(0)

    filename = f"maintenance_invoice_{invoice.year}_{invoice.month}.pdf"
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )