from urllib.parse import urljoin, urlparse

from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user
from app import db
from app.authz import roles_required
from app.models import (
    Item,
    User,
    VALID_ROLES,
    VenueItem,
    Venue,
    ITEM_TRACKING_MODES,
    ITEM_CATEGORY_OPTIONS,
    normalize_tracking_mode,
    normalize_item_category,
)
from app.services.account_security import (
    AccountManagementError,
    build_dev_password_link_message,
    create_managed_user,
    issue_admin_password_link,
    set_user_active_state,
    unlock_user_account,
    update_managed_user,
)
from app.services.admin_hub import (
    build_admin_user_detail_view_model,
    build_admin_history_view_model,
    build_admin_overview_view_model,
    build_admin_user_audit_view_model,
    build_admin_user_list_view_model,
)
from sqlalchemy.orm import selectinload

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

TRACKING_MODE_OPTIONS = [
    ("quantity", "Quantity"),
    ("singleton_asset", "Singleton Asset"),
]
USER_ROLE_OPTIONS = [(role, role.title()) for role in VALID_ROLES]
ITEM_STATUS_FILTER_OPTIONS = [
    ("all", "All statuses"),
    ("active", "Active"),
    ("inactive", "Inactive"),
]
ITEM_STRUCTURE_FILTER_OPTIONS = [
    ("all", "All structures"),
    ("direct", "Direct items"),
    ("family_parent", "Family organizers"),
    ("family_member", "Family members"),
    ("singleton_asset", "Singleton assets"),
]
ITEM_CATEGORY_FORM_OPTIONS = [
    ("consumable", "Consumable"),
    ("durable", "Durable"),
    ("beverage", "Beverage"),
    ("cleaning", "Cleaning"),
    ("office", "Office"),
    ("other", "Other"),
]
ITEM_CATALOG_PER_PAGE = 50


def _is_safe_local_redirect(target):
    if not target:
        return False
    host_url = urlparse(request.host_url)
    redirect_url = urlparse(urljoin(request.host_url, target))
    return redirect_url.scheme in {"http", "https"} and host_url.netloc == redirect_url.netloc


def _user_return_target(default_endpoint="admin.users", **default_values):
    next_path = request.form.get("next") or request.args.get("next")
    if _is_safe_local_redirect(next_path):
        return next_path
    return url_for(default_endpoint, **default_values)


def _parse_user_page(value, default=1):
    try:
        return max(1, int(value or default))
    except (TypeError, ValueError):
        return default


def build_user_form_values(source=None):
    source = source or {}
    return {
        "email": (source.get("email") or "").strip().lower(),
        "display_name": (source.get("display_name") or "").strip(),
        "role": (source.get("role") or "viewer").strip().lower(),
    }


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
    active_items = sum(1 for item in items if item.active)
    return {
        "total_items": len(items),
        "active_items": active_items,
        "inactive_items": len(items) - active_items,
        "group_parents": sum(1 for item in items if item.is_group_parent),
        "child_items": sum(1 for item in items if item.parent_item_id is not None),
        "singleton_assets": sum(1 for item in direct_items if item.is_singleton_asset),
        "quantity_items": sum(1 for item in direct_items if not item.is_singleton_asset),
    }


def parse_item_catalog_filters(source):
    status = (source.get("status") or "all").strip().lower()
    structure = (source.get("structure") or "all").strip().lower()
    query = (source.get("q") or "").strip()
    if status not in {value for value, _ in ITEM_STATUS_FILTER_OPTIONS}:
        status = "all"
    if structure not in {value for value, _ in ITEM_STRUCTURE_FILTER_OPTIONS}:
        structure = "all"
    try:
        page = max(1, int(source.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    return {
        "q": query,
        "status": status,
        "structure": structure,
        "page": page,
        "is_filtered": bool(query or status != "all" or structure != "all"),
    }


def item_matches_catalog_filters(item, filters):
    status = filters["status"]
    structure = filters["structure"]
    query = filters["q"].lower()

    if status == "active" and not item.active:
        return False
    if status == "inactive" and item.active:
        return False

    if structure == "direct" and (item.is_group_parent or item.parent_item_id is not None):
        return False
    if structure == "family_parent" and not item.is_group_parent:
        return False
    if structure == "family_member" and item.parent_item_id is None:
        return False
    if structure == "singleton_asset" and (item.is_group_parent or not item.is_singleton_asset):
        return False

    if not query:
        return True

    search_haystack = [
        item.name or "",
        item.parent_item.name if item.parent_item else "",
        item.unit or "",
        normalize_item_category(item.item_category or item.item_type).replace("_", " "),
        "family organizer" if item.is_group_parent else "",
        "family member" if item.parent_item_id is not None else "",
        "direct item" if not item.is_group_parent and item.parent_item_id is None else "",
        "singleton asset" if item.is_singleton_asset else "quantity",
    ]
    return any(query in value.lower() for value in search_haystack if value)


def build_item_catalog_pagination(rows, page, per_page=ITEM_CATALOG_PER_PAGE):
    total_count = len(rows)
    total_pages = max(1, (total_count + per_page - 1) // per_page) if total_count else 1
    current_page = min(max(1, page), total_pages)
    start_index = (current_page - 1) * per_page
    end_index = start_index + per_page
    page_rows = rows[start_index:end_index]
    showing_from = start_index + 1 if total_count else 0
    showing_to = start_index + len(page_rows)
    return {
        "rows": page_rows,
        "pagination": {
            "current_page": current_page,
            "total_pages": total_pages,
            "total_count": total_count,
            "showing_from": showing_from,
            "showing_to": showing_to,
            "has_prev": current_page > 1,
            "has_next": current_page < total_pages,
            "prev_page": current_page - 1,
            "next_page": current_page + 1,
            "per_page": per_page,
        },
    }


def build_item_catalog_query_args(filters, *, page=None):
    args = {}
    if filters.get("q"):
        args["q"] = filters["q"]
    if filters.get("status") and filters["status"] != "all":
        args["status"] = filters["status"]
    if filters.get("structure") and filters["structure"] != "all":
        args["structure"] = filters["structure"]
    if page and page > 1:
        args["page"] = page
    return args


def build_item_catalog_view_model(all_items, catalog_filters):
    filtered_items = [item for item in all_items if item_matches_catalog_filters(item, catalog_filters)]
    filtered_rows = build_item_rows(filtered_items)
    paginated_catalog = build_item_catalog_pagination(filtered_rows, catalog_filters["page"])
    pagination = paginated_catalog["pagination"]
    pagination["prev_url"] = (
        url_for("admin.items", **build_item_catalog_query_args(catalog_filters, page=pagination["prev_page"]))
        if pagination["has_prev"]
        else None
    )
    pagination["next_url"] = (
        url_for("admin.items", **build_item_catalog_query_args(catalog_filters, page=pagination["next_page"]))
        if pagination["has_next"]
        else None
    )
    return {
        "item_rows": paginated_catalog["rows"],
        "item_catalog": {
            "filters": catalog_filters,
            "status_options": ITEM_STATUS_FILTER_OPTIONS,
            "structure_options": ITEM_STRUCTURE_FILTER_OPTIONS,
            "pagination": pagination,
            "query_args": build_item_catalog_query_args(catalog_filters),
        },
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
        parent_item = db.session.get(Item, parent_item_id)
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


@admin_bp.get("")
@roles_required("admin")
def overview():
    return render_template(
        "admin/overview.html",
        admin_page_key="overview",
        overview=build_admin_overview_view_model(),
    )


@admin_bp.route("/users", methods=["GET", "POST"])
@roles_required("admin")
def users():
    page = _parse_user_page(request.args.get("page"))
    form_values = build_user_form_values()
    show_add_user_form = False

    if request.method == "POST":
        page = _parse_user_page(request.form.get("page"), default=page)
        form_values = build_user_form_values(request.form)
        show_add_user_form = True
        try:
            user, issued_link = create_managed_user(
                actor=current_user,
                email=form_values["email"],
                display_name=form_values["display_name"],
                role=form_values["role"],
            )
        except AccountManagementError as exc:
            flash(str(exc), "error")
        else:
            flash(f"Created account for {user.email}.", "success")
            if current_app.config["AUTH_DEV_EXPOSE_PASSWORD_LINKS"]:
                flash(build_dev_password_link_message(user, issued_link), "success")
            return redirect(url_for("admin.users", page=page))

    return render_template(
        "admin/users.html",
        admin_page_key="users",
        user_center=build_admin_user_list_view_model(page=page, actor=current_user),
        role_options=USER_ROLE_OPTIONS,
        form_values=form_values,
        show_add_user_form=show_add_user_form,
    )


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@roles_required("admin")
def edit_user(user_id):
    if request.method == "POST":
        try:
            _user, changed_fields = update_managed_user(
                actor=current_user,
                user_id=user_id,
                display_name=request.form.get("display_name"),
                role=request.form.get("role"),
            )
        except AccountManagementError as exc:
            flash(str(exc), "error")
        else:
            if changed_fields:
                flash("User details updated.", "success")
            else:
                flash("No account changes were needed.", "success")
            return redirect(_user_return_target("admin.edit_user", user_id=user_id))

    user = db.get_or_404(User, user_id)
    return render_template(
        "admin/user_edit.html",
        admin_page_key="users",
        user_detail=build_admin_user_detail_view_model(user.id, actor=current_user),
        role_options=USER_ROLE_OPTIONS,
        next_path=_user_return_target("admin.users"),
    )


@admin_bp.post("/users/<int:user_id>/activate")
@roles_required("admin")
def activate_user(user_id):
    try:
        user, changed = set_user_active_state(
            actor=current_user,
            user_id=user_id,
            should_be_active=True,
        )
    except AccountManagementError as exc:
        flash(str(exc), "error")
    else:
        flash(
            "User activated." if changed else f"{user.email} is already active.",
            "success",
        )
    return redirect(_user_return_target("admin.users"))


@admin_bp.post("/users/<int:user_id>/deactivate")
@roles_required("admin")
def deactivate_user(user_id):
    try:
        user, changed = set_user_active_state(
            actor=current_user,
            user_id=user_id,
            should_be_active=False,
        )
    except AccountManagementError as exc:
        flash(str(exc), "error")
    else:
        flash(
            "User deactivated." if changed else f"{user.email} is already inactive.",
            "success",
        )
    return redirect(_user_return_target("admin.users"))


@admin_bp.post("/users/<int:user_id>/unlock")
@roles_required("admin")
def unlock_user(user_id):
    try:
        user, changed = unlock_user_account(
            actor=current_user,
            user_id=user_id,
        )
    except AccountManagementError as exc:
        flash(str(exc), "error")
    else:
        flash(
            "User unlocked." if changed else f"{user.email} is not currently locked.",
            "success",
        )
    return redirect(_user_return_target("admin.users"))


@admin_bp.post("/users/<int:user_id>/password-link")
@roles_required("admin")
def issue_user_password_link(user_id):
    try:
        user, issued_link = issue_admin_password_link(
            actor=current_user,
            user_id=user_id,
        )
    except AccountManagementError as exc:
        flash(str(exc), "error")
    else:
        purpose_label = "setup" if issued_link.purpose == "password_setup" else "reset"
        flash(f"Password {purpose_label} link prepared for {user.email}.", "success")
        if current_app.config["AUTH_DEV_EXPOSE_PASSWORD_LINKS"]:
            flash(build_dev_password_link_message(user, issued_link), "success")
    return redirect(_user_return_target("admin.users"))


@admin_bp.get("/audit/users")
@roles_required("admin")
def user_audit():
    return render_template(
        "admin/user_audit.html",
        admin_page_key="audit",
        user_audit=build_admin_user_audit_view_model(),
    )


@admin_bp.get("/history")
@roles_required("admin")
def history():
    return render_template(
        "admin/history.html",
        admin_page_key="history",
        history_view=build_admin_history_view_model(),
    )


@admin_bp.route("/items", methods=["GET", "POST"])
@roles_required("admin")
def items():
    form_values = build_item_form_values()
    show_add_item_form = False
    catalog_filters = parse_item_catalog_filters(request.form if request.method == "POST" else request.args)

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
            return redirect(url_for("admin.items", **build_item_catalog_query_args(catalog_filters, page=catalog_filters["page"])))

    all_items = (
        Item.query.options(selectinload(Item.parent_item), selectinload(Item.child_items))
        .order_by(Item.sort_order.asc(), Item.name.asc(), Item.id.asc())
        .all()
    )
    catalog_view = build_item_catalog_view_model(all_items, catalog_filters)

    if request.method == "GET" and request.args.get("catalog_partial") == "1":
        return jsonify(
            {
                "html": render_template(
                    "admin/_item_catalog_results.html",
                    item_rows=catalog_view["item_rows"],
                    item_catalog=catalog_view["item_catalog"],
                ),
                "pagination": catalog_view["item_catalog"]["pagination"],
            }
        )

    return render_template(
        "admin/items.html",
        admin_page_key="items",
        items=all_items,
        item_rows=catalog_view["item_rows"],
        catalog_summary=build_item_catalog_summary(all_items),
        item_catalog=catalog_view["item_catalog"],
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
        admin_page_key="items",
        item=item,
        parent_options=parent_options,
        tracking_mode_options=TRACKING_MODE_OPTIONS,
        item_category_options=ITEM_CATEGORY_FORM_OPTIONS,
        form_values=form_values,
    )


@admin_bp.route("/items/<int:item_id>/deactivate", methods=["GET", "POST"])
@roles_required("admin")
def deactivate_item(item_id):
    it = db.get_or_404(Item, item_id)

    # Find venues where this item is currently tracked (active mapping)
    active_links = VenueItem.query.filter_by(item_id=item_id, active=True).all()
    venue_ids = [l.venue_id for l in active_links]
    venues = Venue.query.filter(Venue.id.in_(venue_ids)).order_by(Venue.name.asc()).all() if venue_ids else []

    # GET: confirmation page only (no mutations on GET)
    if request.method == "GET":
        return render_template(
            "admin/confirm_deactivate_item.html",
            admin_page_key="items",
            item=it,
            venues=venues,
        )

    # POST: user confirmed “Deactivate anyway”
    it.active = False
    VenueItem.query.filter_by(item_id=item_id, active=True).update({"active": False})
    db.session.commit()

    flash(f"Item deactivated and removed from {len(venues)} venue(s).", "success")
    return redirect(url_for("admin.items"))


@admin_bp.post("/items/<int:item_id>/activate")
@roles_required("admin")
def activate_item(item_id):
    it = db.get_or_404(Item, item_id)
    it.active = True
    db.session.commit()
    flash("Item activated.", "success")
    return redirect(url_for("admin.items"))
