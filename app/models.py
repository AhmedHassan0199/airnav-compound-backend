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