from datetime import datetime
from . import db
from werkzeug.security import generate_password_hash, check_password_hash

class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    role = db.Column(db.String(32), nullable=False, default="RESIDENT")
    password_hash = db.Column(db.String(255), nullable=False)

    can_edit_profile = db.Column(db.Boolean, nullable=False, default=True)


    last_login_at = db.Column(db.DateTime, nullable=True)

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
    status = db.Column(db.String(20), nullable=False, default="UNPAID")
    due_date = db.Column(db.DateTime, nullable=False)
    paid_date = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.now())
    updated_at = db.Column(db.DateTime, default=datetime.now(), onupdate=datetime.now())

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

    created_at = db.Column(db.DateTime, default=datetime.now())

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
    created_at = db.Column(db.DateTime, default=datetime.now())
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

class UnionLedgerEntry(db.Model):
    __tablename__ = "union_ledger"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=datetime.now(), nullable=False)
    description = db.Column(db.String(255), nullable=False)

    debit = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    credit = db.Column(db.Numeric(10, 2), default=0, nullable=False)

    balance_after = db.Column(db.Numeric(10, 2), default=0, nullable=False)

    entry_type = db.Column(db.String(50), nullable=False)  # e.g. "SETTLEMENT", "EXPENSE"
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    created_by = db.relationship("User", backref="ledger_entries")

class Expense(db.Model):
    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=datetime.now(), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    category = db.Column(db.String(100), nullable=True)
    description = db.Column(db.String(255), nullable=False)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_by = db.relationship("User", backref="expenses")

class NotificationSubscription(db.Model):
    __tablename__ = "notification_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    token = db.Column(db.String(512), nullable=False, unique=True)
    user_agent = db.Column(db.String(256))
    created_at = db.Column(db.DateTime, default=datetime.now())
    updated_at = db.Column(
        db.DateTime, default=datetime.now(), onupdate=datetime.now()
    )

    user = db.relationship("User", backref="notification_subscriptions")

class OnlinePayment(db.Model):
    __tablename__ = "online_payments"

    id = db.Column(db.Integer, primary_key=True)

    invoice_id = db.Column(
        db.Integer, db.ForeignKey("maintenance_invoices.id"), nullable=False
    )
    resident_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )

    amount = db.Column(db.Numeric(10, 2), nullable=False)
    instapay_sender_id = db.Column(db.String(100), nullable=False)
    transaction_ref = db.Column(db.String(100), nullable=False)

    # PENDING / APPROVED / REJECTED
    status = db.Column(db.String(20), nullable=False, default="PENDING")

    created_at = db.Column(db.DateTime, default=datetime.now(), nullable=False)
    confirmed_at = db.Column(db.DateTime)
    confirmed_by_admin_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    notes = db.Column(db.Text)

    invoice = db.relationship("MaintenanceInvoice", backref="online_payments")
    resident = db.relationship(
        "User", foreign_keys=[resident_id], backref="online_payments"
    )
    confirmed_by_admin = db.relationship(
        "User", foreign_keys=[confirmed_by_admin_id]
    )

class AdminBuilding(db.Model):
    __tablename__ = "admin_buildings"

    id = db.Column(db.Integer, primary_key=True)

    admin_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    building = db.Column(db.String(10), nullable=False)

    admin = db.relationship(
        "User",
        backref=db.backref("allowed_buildings", lazy="dynamic")
    )

    def __repr__(self):
        return f"<AdminBuilding admin={self.admin_id} building={self.building}>"

