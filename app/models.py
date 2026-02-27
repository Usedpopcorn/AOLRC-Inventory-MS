from datetime import datetime, timezone
from . import db

class Venue(db.Model):
    __tablename__ = "venues"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)

    # core venues cannot be deleted 
    is_core = db.Column(db.Boolean, default=False, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

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