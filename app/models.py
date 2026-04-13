from datetime import datetime, timezone
from flask_login import UserMixin
from . import db

VALID_ROLES = ("viewer", "staff", "admin")


def normalize_role(raw_role):
    role = (raw_role or "").strip().lower()
    if role not in VALID_ROLES:
        return "viewer"
    return role


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    display_name = db.Column(db.String(120), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="viewer")
    active = db.Column(db.Boolean, default=True, nullable=False)
    failed_login_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    avatar_filename = db.Column(db.String(255), nullable=True)
    avatar_updated_at = db.Column(db.DateTime, nullable=True)

    def get_id(self):
        return str(self.id)

    @property
    def is_active(self):
        return self.active

    def has_role(self, *roles):
        return self.role in {normalize_role(r) for r in roles}

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_staff(self):
        return self.role in {"staff", "admin"}

class Venue(db.Model):
    __tablename__ = "venues"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)

    # primary venues cannot be deleted
    is_core = db.Column(db.Boolean, default=False, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class VenueNote(db.Model):
    __tablename__ = "venue_notes"

    id = db.Column(db.Integer, primary_key=True)
    venue_id = db.Column(db.Integer, db.ForeignKey("venues.id"), nullable=False, index=True)
    author_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

class Item(db.Model):
    __tablename__ = "items"

    id = db.Column(db.Integer, primary_key=True)

    # “Yoga Mats”, “Tissue Boxes”, etc.
    name = db.Column(db.String(120), nullable=False, unique=True)

    # "durable" or "consumable" (we’ll keep it as a string for simplicity)
    item_type = db.Column(db.String(20), nullable=False)

    # Allows “archiving” items instead of deleting
    active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

class VenueItem(db.Model):
    __tablename__ = "venue_items"

    id = db.Column(db.Integer, primary_key=True)

    venue_id = db.Column(db.Integer, db.ForeignKey("venues.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)

    # Optional configuration fields (nice now, useful later)
    expected_qty = db.Column(db.Integer, nullable=True)        # mostly for durable
    reorder_threshold = db.Column(db.Integer, nullable=True)   # mostly for consumable

    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Prevent duplicate “venue has this item” rows
    __table_args__ = (
        db.UniqueConstraint("venue_id", "item_id", name="uq_venue_item"),
    )

class Check(db.Model):
    __tablename__ = "checks"

    id = db.Column(db.Integer, primary_key=True)

    venue_id = db.Column(db.Integer, db.ForeignKey("venues.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    # later: user_id (when we add login)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class CheckLine(db.Model):
    __tablename__ = "check_lines"

    id = db.Column(db.Integer, primary_key=True)

    check_id = db.Column(db.Integer, db.ForeignKey("checks.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)

    # good | ok | low | out | not_checked
    status = db.Column(db.String(20), nullable=False)

    __table_args__ = (
        db.UniqueConstraint("check_id", "item_id", name="uq_checkline_check_item"),
    )


class CountSession(db.Model):
    __tablename__ = "count_sessions"

    id = db.Column(db.Integer, primary_key=True)
    venue_id = db.Column(db.Integer, db.ForeignKey("venues.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class CountLine(db.Model):
    __tablename__ = "count_lines"

    id = db.Column(db.Integer, primary_key=True)
    count_session_id = db.Column(db.Integer, db.ForeignKey("count_sessions.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    raw_count = db.Column(db.Integer, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("count_session_id", "item_id", name="uq_countline_session_item"),
    )


class VenueItemCount(db.Model):
    __tablename__ = "venue_item_counts"

    id = db.Column(db.Integer, primary_key=True)
    venue_id = db.Column(db.Integer, db.ForeignKey("venues.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    raw_count = db.Column(db.Integer, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        db.UniqueConstraint("venue_id", "item_id", name="uq_venue_item_count"),
    )
