from datetime import datetime, timezone
from flask_login import UserMixin
from . import db

VALID_ROLES = ("viewer", "staff", "admin")
VALID_THEME_PREFERENCES = ("purple", "blue")
DEFAULT_THEME_PREFERENCE = "purple"
ITEM_TRACKING_MODES = ("quantity", "singleton_asset")
ITEM_CATEGORY_OPTIONS = ("consumable", "durable", "beverage", "cleaning", "office", "other")
PASSWORD_ACTION_PURPOSES = ("password_setup", "password_reset")
ORDER_BATCH_TYPES = ("monthly", "quarterly", "ad_hoc")
ORDER_LINE_STATUSES = ("planned", "ordered", "received", "skipped")
FEEDBACK_SUBMISSION_TYPES = ("feedback", "bug_report")


def normalize_role(raw_role):
    role = (raw_role or "").strip().lower()
    if role not in VALID_ROLES:
        return "viewer"
    return role


def normalize_theme_preference(raw_theme):
    theme = (raw_theme or "").strip().lower()
    if theme not in VALID_THEME_PREFERENCES:
        return DEFAULT_THEME_PREFERENCE
    return theme


def normalize_tracking_mode(raw_mode):
    mode = (raw_mode or "").strip().lower()
    if mode not in ITEM_TRACKING_MODES:
        return "quantity"
    return mode


def normalize_item_category(raw_category):
    category = (raw_category or "").strip().lower()
    if category in ITEM_CATEGORY_OPTIONS:
        return category
    if category in {"durable", "consumable"}:
        return category
    return "other"


def normalize_order_batch_type(raw_value):
    batch_type = (raw_value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if batch_type == "adhoc":
        batch_type = "ad_hoc"
    if batch_type not in ORDER_BATCH_TYPES:
        return "monthly"
    return batch_type


def normalize_order_line_status(raw_value):
    status = (raw_value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if status not in ORDER_LINE_STATUSES:
        return "planned"
    return status


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    display_name = db.Column(db.String(120), nullable=True)
    theme_preference = db.Column(
        db.String(20),
        nullable=False,
        default=DEFAULT_THEME_PREFERENCE,
    )
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="viewer")
    active = db.Column(db.Boolean, default=True, nullable=False)
    failed_login_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    password_changed_at = db.Column(db.DateTime, nullable=True)
    force_password_change = db.Column(db.Boolean, nullable=False, default=False)
    session_version = db.Column(db.Integer, nullable=False, default=1)
    deactivated_at = db.Column(db.DateTime, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    deactivated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
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


class PasswordActionToken(db.Model):
    __tablename__ = "password_action_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    purpose = db.Column(db.String(32), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    consumed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    user = db.relationship("User", foreign_keys=[user_id])
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])


class AccountAuditEvent(db.Model):
    __tablename__ = "account_audit_events"

    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(64), nullable=False, index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    target_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    target_email = db.Column(db.String(255), nullable=True)
    details_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    actor = db.relationship("User", foreign_keys=[actor_user_id])
    target = db.relationship("User", foreign_keys=[target_user_id])


class Venue(db.Model):
    __tablename__ = "venues"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)

    # primary venues cannot be deleted
    is_core = db.Column(db.Boolean, default=False, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    stale_threshold_days = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class VenueNote(db.Model):
    __tablename__ = "venue_notes"

    id = db.Column(db.Integer, primary_key=True)
    venue_id = db.Column(db.Integer, db.ForeignKey("venues.id"), nullable=False, index=True)
    author_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=True, index=True)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    item = db.relationship("Item", foreign_keys=[item_id])


class SupplyNote(db.Model):
    __tablename__ = "supply_notes"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False, index=True)
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

    item = db.relationship("Item", foreign_keys=[item_id])


class FeedbackSubmission(db.Model):
    __tablename__ = "feedback_submissions"

    id = db.Column(db.Integer, primary_key=True)
    submission_type = db.Column(db.String(32), nullable=False, index=True)
    summary = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    is_anonymous = db.Column(db.Boolean, nullable=False, default=False)
    source_path = db.Column(db.String(255), nullable=False)
    source_query = db.Column(db.Text, nullable=True)
    user_agent = db.Column(db.String(512), nullable=True)
    submitter_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    submitter = db.relationship("User", foreign_keys=[submitter_user_id])


class Item(db.Model):
    __tablename__ = "items"

    id = db.Column(db.Integer, primary_key=True)

    # “Yoga Mats”, “Tissue Boxes”, etc.
    name = db.Column(db.String(120), nullable=False, unique=True)

    # "durable" or "consumable" (we’ll keep it as a string for simplicity)
    item_type = db.Column(db.String(20), nullable=False)

    # New structured fields for flexible item behavior.
    tracking_mode = db.Column(db.String(40), nullable=False, default="quantity")
    item_category = db.Column(db.String(40), nullable=False, default="consumable")
    parent_item_id = db.Column(
        db.Integer,
        db.ForeignKey("items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_group_parent = db.Column(db.Boolean, nullable=False, default=False)
    unit = db.Column(db.String(40), nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    default_par_level = db.Column(db.Integer, nullable=True)
    stale_threshold_days = db.Column(db.Integer, nullable=True)
    setup_group_code = db.Column(db.String(32), nullable=True, index=True)
    setup_group_label = db.Column(db.String(120), nullable=True)

    # Allows “archiving” items instead of deleting
    active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    parent_item = db.relationship(
        "Item",
        remote_side=[id],
        backref=db.backref("child_items", lazy="selectin"),
        foreign_keys=[parent_item_id],
    )

    @property
    def effective_item_category(self):
        if self.item_category:
            return self.item_category
        return self.item_type or "other"

    @property
    def is_singleton_asset(self):
        return normalize_tracking_mode(self.tracking_mode) == "singleton_asset"

    @property
    def can_be_tracked_directly(self):
        return not bool(self.is_group_parent)


class InventoryPolicy(db.Model):
    __tablename__ = "inventory_policies"

    id = db.Column(db.Integer, primary_key=True)
    default_stale_threshold_days = db.Column(db.Integer, nullable=False, default=2)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

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


class InventoryAdminEvent(db.Model):
    __tablename__ = "inventory_admin_events"

    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(64), nullable=False, index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    subject_type = db.Column(db.String(32), nullable=False)
    subject_id = db.Column(db.Integer, nullable=True, index=True)
    subject_label = db.Column(db.String(255), nullable=True)
    details_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    actor = db.relationship("User", foreign_keys=[actor_user_id])

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


class OrderBatch(db.Model):
    __tablename__ = "order_batches"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    batch_type = db.Column(db.String(32), nullable=False, default="monthly", index=True)
    notes = db.Column(db.Text, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    lines = db.relationship(
        "OrderLine",
        back_populates="batch",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class OrderLine(db.Model):
    __tablename__ = "order_lines"

    id = db.Column(db.Integer, primary_key=True)
    order_batch_id = db.Column(
        db.Integer,
        db.ForeignKey("order_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id = db.Column(
        db.Integer,
        db.ForeignKey("items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    venue_id = db.Column(
        db.Integer,
        db.ForeignKey("venues.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    item_name_snapshot = db.Column(db.String(120), nullable=False)
    venue_name_snapshot = db.Column(db.String(120), nullable=False)
    setup_group_code_snapshot = db.Column(db.String(32), nullable=True)
    setup_group_label_snapshot = db.Column(db.String(120), nullable=True)
    count_snapshot = db.Column(db.Integer, nullable=True)
    par_snapshot = db.Column(db.Integer, nullable=True)
    suggested_order_qty_snapshot = db.Column(db.Integer, nullable=False, default=0)
    over_par_qty_snapshot = db.Column(db.Integer, nullable=False, default=0)
    actual_ordered_qty = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(32), nullable=False, default="planned", index=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        db.UniqueConstraint(
            "order_batch_id",
            "venue_id",
            "item_id",
            name="uq_order_lines_batch_venue_item",
        ),
    )

    batch = db.relationship("OrderBatch", back_populates="lines")
    item = db.relationship("Item", foreign_keys=[item_id])
    venue = db.relationship("Venue", foreign_keys=[venue_id])
