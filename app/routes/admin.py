from flask import Blueprint, render_template, request, redirect, url_for, flash
from app import db
from app.authz import roles_required
from app.models import (
    Item,
    VenueItem,
    Venue,
    ITEM_TRACKING_MODES,
    ITEM_CATEGORY_OPTIONS,
    normalize_tracking_mode,
    normalize_item_category,
)
from sqlalchemy.orm import selectinload

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

TRACKING_MODE_OPTIONS = [
    ("quantity", "Quantity"),
    ("singleton_asset", "Singleton Asset"),
]
ITEM_CATEGORY_FORM_OPTIONS = [
    ("consumable", "Consumable"),
    ("durable", "Durable"),
    ("beverage", "Beverage"),
    ("cleaning", "Cleaning"),
    ("office", "Office"),
    ("other", "Other"),
]


def to_bool_field(raw_value):
    return str(raw_value or "").strip().lower() in {"1", "true", "on", "yes"}


def parse_parent_item_id(raw_value):
    value = (raw_value or "").strip()
    if not value:
        return None
    if not value.isdigit():
        return "invalid"
    return int(value)


def normalize_legacy_item_type(category_value):
    if category_value in {"durable", "consumable"}:
        return category_value
    return "consumable"


def build_item_form_values(item=None):
    if item is None:
        return {
            "name": "",
            "tracking_mode": "quantity",
            "item_category": "consumable",
            "item_type": "consumable",
            "parent_item_id": "",
            "is_group_parent": False,
            "active": True,
            "unit": "",
            "sort_order": 0,
        }
    return {
        "name": item.name,
        "tracking_mode": normalize_tracking_mode(item.tracking_mode),
        "item_category": normalize_item_category(item.item_category or item.item_type),
        "item_type": item.item_type,
        "parent_item_id": str(item.parent_item_id or ""),
        "is_group_parent": bool(item.is_group_parent),
        "active": bool(item.active),
        "unit": item.unit or "",
        "sort_order": item.sort_order or 0,
    }


def build_item_rows(items):
    by_parent = {}
    root_items = []
    for item in items:
        if item.parent_item_id is None:
            root_items.append(item)
        by_parent.setdefault(item.parent_item_id, []).append(item)

    output_rows = []
    seen_ids = set()

    for root in sorted(root_items, key=lambda row: ((row.sort_order or 0), row.name.lower(), row.id)):
        seen_ids.add(root.id)
        output_rows.append((root, 0))
        for child in sorted(by_parent.get(root.id, []), key=lambda row: ((row.sort_order or 0), row.name.lower(), row.id)):
            seen_ids.add(child.id)
            output_rows.append((child, 1))

    for item in sorted(items, key=lambda row: ((row.sort_order or 0), row.name.lower(), row.id)):
        if item.id in seen_ids:
            continue
        output_rows.append((item, 0 if item.parent_item_id is None else 1))
    return output_rows


def build_item_catalog_summary(items):
    direct_items = [item for item in items if not item.is_group_parent]
    return {
        "total_items": len(items),
        "group_parents": sum(1 for item in items if item.is_group_parent),
        "child_items": sum(1 for item in items if item.parent_item_id is not None),
        "singleton_assets": sum(1 for item in direct_items if item.is_singleton_asset),
        "quantity_items": sum(1 for item in direct_items if not item.is_singleton_asset),
    }


def fetch_parent_options(exclude_item_id=None):
    query = Item.query.filter(
        Item.active == True,
        Item.parent_item_id.is_(None),
        Item.is_group_parent == True,
    )
    if exclude_item_id is not None:
        query = query.filter(Item.id != exclude_item_id)
    return query.order_by(Item.name.asc()).all()


def parse_item_payload(existing_item=None):
    name = (request.form.get("name") or "").strip()
    tracking_mode = normalize_tracking_mode(request.form.get("tracking_mode"))
    item_category = normalize_item_category(request.form.get("item_category"))
    parent_item_id = parse_parent_item_id(request.form.get("parent_item_id"))
    is_group_parent = to_bool_field(request.form.get("is_group_parent"))
    active = to_bool_field(request.form.get("active"))
    unit = (request.form.get("unit") or "").strip() or None
    sort_order_raw = (request.form.get("sort_order") or "").strip()
    try:
        sort_order = int(sort_order_raw) if sort_order_raw else 0
    except ValueError:
        sort_order = 0

    form_values = {
        "name": name,
        "tracking_mode": tracking_mode,
        "item_category": item_category,
        "item_type": normalize_legacy_item_type(item_category),
        "parent_item_id": "" if parent_item_id in (None, "invalid") else str(parent_item_id),
        "is_group_parent": is_group_parent,
        "active": active,
        "unit": unit or "",
        "sort_order": sort_order,
    }

    errors = []
    if not name:
        errors.append("Item name is required.")

    duplicate_query = Item.query.filter(Item.name == name)
    if existing_item is not None:
        duplicate_query = duplicate_query.filter(Item.id != existing_item.id)
    if name and duplicate_query.first():
        errors.append("An item with this name already exists.")

    parent_item = None
    if parent_item_id == "invalid":
        errors.append("Parent item selection is invalid.")
    elif parent_item_id is not None:
        parent_item = Item.query.get(parent_item_id)
        if parent_item is None:
            errors.append("Selected parent item no longer exists.")
        else:
            if not parent_item.is_group_parent:
                errors.append("Parent item must be marked as a group parent.")
            if parent_item.parent_item_id is not None:
                errors.append("Parent item cannot be a child item.")
            if existing_item and parent_item.id == existing_item.id:
                errors.append("An item cannot be its own parent.")

    if is_group_parent and parent_item_id not in (None, "invalid"):
        errors.append("Group parent items cannot also belong to a parent family.")

    if existing_item and parent_item_id not in (None, "invalid"):
        has_children = Item.query.filter(Item.parent_item_id == existing_item.id).first() is not None
        if has_children:
            errors.append("Items with children cannot be moved under another parent.")

    if is_group_parent:
        tracking_mode = "quantity"
        parent_item_id = None

    payload = {
        "name": name,
        "tracking_mode": tracking_mode,
        "item_category": item_category,
        "item_type": normalize_legacy_item_type(item_category),
        "parent_item_id": parent_item_id if parent_item_id != "invalid" else None,
        "is_group_parent": is_group_parent,
        "active": active,
        "unit": unit,
        "sort_order": sort_order,
    }
    return payload, form_values, errors


@admin_bp.route("/items", methods=["GET", "POST"])
@roles_required("admin")
def items():
    form_values = build_item_form_values()
    show_add_item_form = False

    if request.method == "POST":
        show_add_item_form = True
        payload, form_values, errors = parse_item_payload()
        if errors:
            for error in errors:
                flash(error, "error")
        else:
            item = Item(
                name=payload["name"],
                item_type=payload["item_type"],
                tracking_mode=payload["tracking_mode"],
                item_category=payload["item_category"],
                parent_item_id=payload["parent_item_id"],
                is_group_parent=payload["is_group_parent"],
                active=payload["active"],
                unit=payload["unit"],
                sort_order=payload["sort_order"],
            )
            db.session.add(item)
            db.session.commit()
            flash("Item added.", "success")
            return redirect(url_for("admin.items"))

    items = (
        Item.query.options(selectinload(Item.parent_item), selectinload(Item.child_items))
        .order_by(Item.sort_order.asc(), Item.name.asc(), Item.id.asc())
        .all()
    )
    return render_template(
        "admin/items.html",
        items=items,
        item_rows=build_item_rows(items),
        catalog_summary=build_item_catalog_summary(items),
        parent_options=fetch_parent_options(),
        tracking_mode_options=TRACKING_MODE_OPTIONS,
        item_category_options=ITEM_CATEGORY_FORM_OPTIONS,
        valid_tracking_modes=ITEM_TRACKING_MODES,
        valid_item_categories=ITEM_CATEGORY_OPTIONS,
        form_values=form_values,
        show_add_item_form=show_add_item_form,
    )


@admin_bp.route("/items/<int:item_id>/edit", methods=["GET", "POST"])
@roles_required("admin")
def edit_item(item_id):
    item = (
        Item.query.options(selectinload(Item.parent_item), selectinload(Item.child_items))
        .filter(Item.id == item_id)
        .first_or_404()
    )
    form_values = build_item_form_values(item)

    if request.method == "POST":
        payload, form_values, errors = parse_item_payload(existing_item=item)
        if errors:
            for error in errors:
                flash(error, "error")
        else:
            item.name = payload["name"]
            item.item_type = payload["item_type"]
            item.tracking_mode = payload["tracking_mode"]
            item.item_category = payload["item_category"]
            item.parent_item_id = payload["parent_item_id"]
            item.is_group_parent = payload["is_group_parent"]
            item.active = payload["active"]
            item.unit = payload["unit"]
            item.sort_order = payload["sort_order"]
            db.session.commit()
            flash("Item updated.", "success")
            return redirect(url_for("admin.items"))

    parent_options = fetch_parent_options(exclude_item_id=item.id)
    if item.parent_item and all(parent.id != item.parent_item.id for parent in parent_options):
        parent_options = sorted(parent_options + [item.parent_item], key=lambda row: row.name.lower())

    return render_template(
        "admin/item_edit.html",
        item=item,
        parent_options=parent_options,
        tracking_mode_options=TRACKING_MODE_OPTIONS,
        item_category_options=ITEM_CATEGORY_FORM_OPTIONS,
        form_values=form_values,
    )


@admin_bp.route("/items/<int:item_id>/deactivate", methods=["GET", "POST"])
@roles_required("admin")
def deactivate_item(item_id):
    it = Item.query.get_or_404(item_id)

    # Find venues where this item is currently tracked (active mapping)
    active_links = VenueItem.query.filter_by(item_id=item_id, active=True).all()
    venue_ids = [l.venue_id for l in active_links]
    venues = Venue.query.filter(Venue.id.in_(venue_ids)).order_by(Venue.name.asc()).all() if venue_ids else []

    # GET: confirmation page only (no mutations on GET)
    if request.method == "GET":
        return render_template("admin/confirm_deactivate_item.html", item=it, venues=venues)

    # POST: user confirmed “Deactivate anyway”
    it.active = False
    VenueItem.query.filter_by(item_id=item_id, active=True).update({"active": False})
    db.session.commit()

    flash(f"Item deactivated and removed from {len(venues)} venue(s).", "success")
    return redirect(url_for("admin.items"))


@admin_bp.post("/items/<int:item_id>/activate")
@roles_required("admin")
def activate_item(item_id):
    it = Item.query.get_or_404(item_id)
    it.active = True
    db.session.commit()
    flash("Item activated.", "success")
    return redirect(url_for("admin.items"))
