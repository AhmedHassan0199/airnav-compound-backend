from datetime import date
from . import db
from werkzeug.security import generate_password_hash, check_password_hash

class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    role = db.Column(db.String(32), nullable=False, default="RESIDENT")
    password_hash = db.Column(db.String(255), nullable=False)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"

class PersonDetails(db.Model):
    __tablename__ = "person_details"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    building = db.Column(db.String(10), nullable=False)
    floor = db.Column(db.String(10), nullable=False)
    apartment = db.Column(db.String(10), nullable=False)

    # one-to-one with users table
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        unique=True,
    )

    phone = db.Column(db.String(30), nullable=True)  # e.g. +201001234567
    
    user = db.relationship(
        "User",
        backref=db.backref("person_details", uselist=False),
    )

    def __repr__(self):
        return f"<PersonDetails {self.full_name} (B{self.building}/F{self.floor}/A{self.apartment})>"

class MaintenanceInvoice(db.Model):
    __tablename__ = "maintenance_invoices"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)  # 1–12
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="PENDING")
    due_date = db.Column(db.Date, nullable=False)
    paid_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.Date, default=date.today)
    updated_at = db.Column(db.Date, default=date.today, onupdate=date.today)

    user = db.relationship(
        "User",
        backref=db.backref("maintenance_invoices", lazy="dynamic"),
    )

    def __repr__(self):
        return f"<Invoice {self.year}-{self.month} for user {self.user_id}: {self.status}>"

class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    invoice_id = db.Column(
        db.Integer,
        db.ForeignKey("maintenance_invoices.id"),
        nullable=False,
        index=True,
    )

    amount = db.Column(db.Numeric(10, 2), nullable=False)
    method = db.Column(db.String(20), nullable=False, default="CASH")
    notes = db.Column(db.String(255), nullable=True)

    collected_by_admin_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    created_at = db.Column(db.Date, default=date.today)

    user = db.relationship(
        "User",
        foreign_keys=[user_id],
        backref=db.backref("payments", lazy="dynamic"),
    )
    invoice = db.relationship(
        "MaintenanceInvoice",
        backref=db.backref("payments", lazy="dynamic"),
    )
    collected_by = db.relationship(
        "User",
        foreign_keys=[collected_by_admin_id],
        backref=db.backref("collected_payments", lazy="dynamic"),
    )

    def __repr__(self):
        return f"<Payment {self.amount} for invoice {self.invoice_id} by admin {self.collected_by_admin_id}>"

class Settlement(db.Model):
    __tablename__ = "settlements"

    id = db.Column(db.Integer, primary_key=True)

    admin_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    treasurer_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    amount = db.Column(db.Numeric(10, 2), nullable=False)
    created_at = db.Column(db.Date, default=date.today)
    notes = db.Column(db.String(255), nullable=True)

    admin = db.relationship(
        "User",
        foreign_keys=[admin_id],
        backref=db.backref("settlements", lazy="dynamic"),
    )

    treasurer = db.relationship(
        "User",
        foreign_keys=[treasurer_id],
        backref=db.backref("approved_settlements", lazy="dynamic"),
    )

    def __repr__(self):
        return f"<Settlement {self.amount} from admin {self.admin_id}>"


