from flask import Blueprint, jsonify, send_file, render_template, current_app
from app.models import PersonDetails, MaintenanceInvoice, User, OnlinePayment
from .auth.routes import get_current_user_from_request
from io import BytesIO
import request
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

@resident_bp.route("/invoices/<int:invoice_id>/instapay", methods=["POST"])
def submit_instapay_payment(invoice_id):
    """
    Resident declares an InstaPay payment for a specific invoice.
    This does NOT auto-mark as PAID; it sets invoice status to PENDING_CONFIRMATION
    and creates an OnlinePayment record with status PENDING.
    """
    current_user, error = get_current_user_from_request(allowed_roles=["RESIDENT"])
    if error:
        msg, status = error
        return jsonify({"message": msg}), status

    data = request.get_json() or {}
    transaction_ref = data.get("transaction_ref")
    sender_id = data.get("instapay_sender_id")  # mobile or InstaPay ID
    amount = data.get("amount")

    if not transaction_ref or not sender_id or not amount:
        return (
            jsonify(
                {
                    "message": "برجاء إدخال رقم العملية، وحساب/موبايل إنستا باي، والمبلغ."
                }
            ),
            400,
        )

    try:
        amount = float(amount)
    except ValueError:
        return jsonify({"message": "المبلغ غير صالح."}), 400

    if amount <= 0:
        return jsonify({"message": "المبلغ يجب أن يكون أكبر من صفر."}), 400

    invoice = MaintenanceInvoice.query.get(invoice_id)
    if not invoice:
        return jsonify({"message": "الفاتورة غير موجودة."}), 404

    # Check invoice belongs to this resident
    if invoice.user_id != current_user.id:
        return jsonify({"message": "لا يمكنك تسجيل دفع لفاتورة لا تخص حسابك."}), 403

    # Check not already fully paid
    if invoice.status == "PAID":
        return jsonify({"message": "هذه الفاتورة مسددة بالفعل."}), 400

    # Optional: prevent multiple pending records for the same invoice
    existing_pending = OnlinePayment.query.filter_by(
        invoice_id=invoice.id, status="PENDING"
    ).first()
    if existing_pending:
        return (
            jsonify(
                {
                    "message": "يوجد طلب دفع إلكتروني قيد المراجعة بالفعل لهذه الفاتورة."
                }
            ),
            400,
        )

    # Create OnlinePayment
    op = OnlinePayment(
        invoice_id=invoice.id,
        resident_id=current_user.id,
        amount=amount,
        instapay_sender_id=sender_id,
        transaction_ref=transaction_ref,
        status="PENDING",
        created_at=datetime.utcnow(),
    )
    db.session.add(op)

    # Set invoice status to PENDING_CONFIRMATION
    invoice.status = "PENDING_CONFIRMATION"
    db.session.commit()

    return jsonify({"message": "تم تسجيل عملية إنستا باي وجاري مراجعتها.", "id": op.id}), 201

