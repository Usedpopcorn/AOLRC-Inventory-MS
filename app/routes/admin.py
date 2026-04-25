import hmac

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user
from sqlalchemy.orm import selectinload

from app import db
from app.authz import roles_required
from app.models import (
    ITEM_CATEGORY_OPTIONS,
    ITEM_TRACKING_MODES,
    VALID_ROLES,
    Item,
    User,
    Venue,
    VenueItem,
    normalize_item_category,
    normalize_tracking_mode,
)
from app.security import is_safe_redirect_target
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
    build_admin_history_view_model,
    build_admin_overview_view_model,
    build_admin_user_audit_view_model,
    build_admin_user_detail_view_model,
    build_admin_user_list_view_model,
)
from app.services.csv_exports import (
    EXPORT_SCOPE_FILTERED,
    build_csv_response,
    build_dated_csv_filename,
    normalize_export_scope,
    sanitize_csv_cell,
)
from app.services.feedback import (
    FEEDBACK_REVIEW_SESSION_KEY,
    build_feedback_inbox_view_model,
)
from app.services.inventory_rules import (
    ITEM_HARD_DELETE_WINDOW_DAYS,
    InventoryRuleError,
    build_item_delete_guard,
    ensure_inventory_policy,
    find_similar_items,
    get_default_stale_threshold_days,
    has_exact_item_name_duplicate,
    log_inventory_admin_event,
    normalize_optional_threshold_days,
    normalize_optional_tracking_value,
    resolve_effective_par_level,
    resolve_effective_stale_threshold_days,
    sync_item_venue_assignments,
)
from app.services.spreadsheet_compat import (
    CUSTOM_SETUP_GROUP_OPTION_VALUE,
    SpreadsheetCompatibilityError,
    format_setup_group_display,
    get_distinct_setup_groups,
    resolve_setup_group_selection,
)

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
ITEM_CATALOG_EXPORT_HEADERS = (
    "Item Name",
    "Active",
    "Tracking Mode",
    "Category",
    "Parent Item",
    "Setup Group Code",
    "Setup Group Label",
    "Default Par",
    "Item Stale Override",
    "Created At",
)
FEEDBACK_TYPE_FILTER_OPTIONS = [
    ("all", "All submissions"),
    ("feedback", "Feedback"),
    ("bug_report", "Bug reports"),
]


def _is_feedback_review_pin_configured():
    return bool((current_app.config.get("FEEDBACK_REVIEW_PIN") or "").strip())


def _clear_feedback_review_pin_session():
    session.pop(FEEDBACK_REVIEW_SESSION_KEY, None)


def _is_feedback_inbox_unlocked():
    try:
        return int(session.get(FEEDBACK_REVIEW_SESSION_KEY, 0) or 0) == int(current_user.id)
    except (TypeError, ValueError):
        return False


def _user_return_target(default_endpoint="admin.users", **default_values):
    next_path = request.form.get("next") or request.args.get("next")
    if is_safe_redirect_target(next_path):
        return next_path
    return url_for(default_endpoint, **default_values)


def _parse_user_page(value, default=1):
    try:
        return max(1, int(value or default))
    except (TypeError, ValueError):
        return default


def _parse_optional_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
            "setup_group_selection": "",
            "setup_group_code": "",
            "setup_group_label": "",
            "is_group_parent": False,
            "active": True,
            "unit": "",
            "sort_order": 0,
            "default_par_level": "",
            "stale_threshold_days": "",
            "selected_venue_ids": [],
            "confirm_similar_name": False,
        }
    selected_venue_ids = [
        link.venue_id
        for link in VenueItem.query.filter(
            VenueItem.item_id == item.id,
            VenueItem.active == True,
        ).all()
    ]
    return {
        "name": item.name,
        "tracking_mode": normalize_tracking_mode(item.tracking_mode),
        "item_category": normalize_item_category(item.item_category or item.item_type),
        "item_type": item.item_type,
        "parent_item_id": str(item.parent_item_id or ""),
        "setup_group_selection": item.setup_group_code or "",
        "setup_group_code": item.setup_group_code or "",
        "setup_group_label": item.setup_group_label or "",
        "is_group_parent": bool(item.is_group_parent),
        "active": bool(item.active),
        "unit": item.unit or "",
        "sort_order": item.sort_order or 0,
        "default_par_level": "" if item.default_par_level is None else str(item.default_par_level),
        "stale_threshold_days": "" if item.stale_threshold_days is None else str(item.stale_threshold_days),
        "selected_venue_ids": selected_venue_ids,
        "confirm_similar_name": False,
    }


def parse_selected_ids(raw_values):
    selected_ids = []
    for raw_value in raw_values or []:
        value = (raw_value or "").strip()
        if not value or not value.isdigit():
            continue
        parsed = int(value)
        if parsed not in selected_ids:
            selected_ids.append(parsed)
    return selected_ids


def fetch_active_venues():
    return Venue.query.filter(Venue.active == True).order_by(Venue.name.asc()).all()


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
        item.setup_group_code or "",
        item.setup_group_label or "",
        format_setup_group_display(item.setup_group_code, item.setup_group_label) or "",
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


def build_item_catalog_export_rows(item_rows):
    rows = []
    for item, _depth in item_rows:
        tracking_mode = "Singleton Asset" if item.is_singleton_asset else "Quantity"
        rows.append(
            {
                "Item Name": sanitize_csv_cell(item.name or ""),
                "Active": "Yes" if item.active else "No",
                "Tracking Mode": tracking_mode,
                "Category": sanitize_csv_cell(
                    normalize_item_category(item.item_category or item.item_type)
                ),
                "Parent Item": sanitize_csv_cell(item.parent_item.name if item.parent_item else ""),
                "Setup Group Code": sanitize_csv_cell(item.setup_group_code or ""),
                "Setup Group Label": sanitize_csv_cell(item.setup_group_label or ""),
                "Default Par": "" if item.default_par_level is None else item.default_par_level,
                "Item Stale Override": (
                    "" if item.stale_threshold_days is None else item.stale_threshold_days
                ),
                "Created At": (
                    item.created_at.strftime("%Y-%m-%d") if item.created_at else ""
                ),
            }
        )
    return rows


def build_item_catalog_export_filename(*, scope):
    tokens = []
    if scope == EXPORT_SCOPE_FILTERED:
        tokens.append("filtered")
    return build_dated_csv_filename("item_catalog", *tokens)


def fetch_parent_options(exclude_item_id=None):
    query = Item.query.filter(
        Item.active == True,
        Item.parent_item_id.is_(None),
        Item.is_group_parent == True,
    )
    if exclude_item_id is not None:
        query = query.filter(Item.id != exclude_item_id)
    return query.order_by(Item.name.asc()).all()


def build_inventory_rules_form_values(source=None, policy=None):
    if source is not None:
        return {
            "default_stale_threshold_days": (source.get("default_stale_threshold_days") or "").strip(),
        }
    return {
        "default_stale_threshold_days": str(
            (policy.default_stale_threshold_days if policy is not None else get_default_stale_threshold_days())
        ),
    }


def build_item_assignment_rows(*, item, venues):
    active_links = {}
    if venues:
        active_links = {
            link.venue_id: link
            for link in VenueItem.query.filter(
                VenueItem.item_id == item.id,
                VenueItem.venue_id.in_([venue.id for venue in venues]),
            ).all()
        }
    rows = []
    for venue in venues:
        link = active_links.get(venue.id)
        link_override = link.expected_qty if link and link.active else None
        effective_par = resolve_effective_par_level(
            item_default_par_level=item.default_par_level,
            venue_par_override=link_override,
        )
        rows.append(
            {
                "id": venue.id,
                "name": venue.name,
                "is_core": bool(venue.is_core),
                "is_selected": bool(link and link.active),
                "par_override": "" if link_override is None else str(link_override),
                "effective_par": effective_par.value,
                "effective_par_source": effective_par.source,
            }
        )
    return rows


def build_tracking_setup_view_model(selected_item_id=None):
    active_items = (
        Item.query.filter(
            Item.active == True,
            Item.is_group_parent == False,
        )
        .order_by(Item.name.asc(), Item.id.asc())
        .all()
    )
    active_venues = fetch_active_venues()
    selected_item = None
    if active_items:
        selected_item = next((item for item in active_items if item.id == selected_item_id), active_items[0])

    venue_rows = []
    if selected_item is not None:
        venue_rows = build_item_assignment_rows(item=selected_item, venues=active_venues)

    global_stale_threshold_days = get_default_stale_threshold_days()
    selected_item_rules = None
    if selected_item is not None:
        stale_rule = resolve_effective_stale_threshold_days(
            item_stale_threshold_days=selected_item.stale_threshold_days,
            global_stale_threshold_days=global_stale_threshold_days,
        )
        selected_item_rules = {
            "default_par_level": selected_item.default_par_level,
            "stale_threshold_days": stale_rule.value,
            "stale_threshold_source": stale_rule.source,
        }

    return {
        "items": active_items,
        "venues": active_venues,
        "selected_item": selected_item,
        "selected_item_rules": selected_item_rules,
        "venue_rows": venue_rows,
        "global_stale_threshold_days": global_stale_threshold_days,
    }


def parse_item_payload(existing_item=None):
    name = (request.form.get("name") or "").strip()
    tracking_mode = normalize_tracking_mode(request.form.get("tracking_mode"))
    item_category = normalize_item_category(request.form.get("item_category"))
    parent_item_id = parse_parent_item_id(request.form.get("parent_item_id"))
    setup_group_selection = (request.form.get("setup_group_selection") or "").strip()
    custom_setup_group_code = request.form.get("setup_group_code")
    custom_setup_group_label = request.form.get("setup_group_label")
    is_group_parent = to_bool_field(request.form.get("is_group_parent"))
    active = to_bool_field(request.form.get("active"))
    unit = (request.form.get("unit") or "").strip() or None
    confirm_similar_name = to_bool_field(request.form.get("confirm_similar_name"))
    selected_venue_ids = parse_selected_ids(request.form.getlist("venue_ids"))
    sort_order_raw = (request.form.get("sort_order") or "").strip()
    try:
        sort_order = int(sort_order_raw) if sort_order_raw else 0
    except ValueError:
        sort_order = 0
    try:
        default_par_level = normalize_optional_tracking_value(
            request.form.get("default_par_level"),
            field_label="Default par",
        )
    except InventoryRuleError as exc:
        default_par_level = request.form.get("default_par_level")
        default_par_error = str(exc)
    else:
        default_par_error = None
    try:
        stale_threshold_days = normalize_optional_threshold_days(
            request.form.get("stale_threshold_days"),
            field_label="Item stale threshold",
        )
    except InventoryRuleError as exc:
        stale_threshold_days = request.form.get("stale_threshold_days")
        stale_threshold_error = str(exc)
    else:
        stale_threshold_error = None
    try:
        setup_group_code, setup_group_label = resolve_setup_group_selection(
            selection_value=setup_group_selection,
            custom_code=custom_setup_group_code,
            custom_label=custom_setup_group_label,
        )
    except SpreadsheetCompatibilityError as exc:
        setup_group_code = custom_setup_group_code
        setup_group_label = custom_setup_group_label
        setup_group_error = str(exc)
    else:
        setup_group_error = None

    form_values = {
        "name": name,
        "tracking_mode": tracking_mode,
        "item_category": item_category,
        "item_type": normalize_legacy_item_type(item_category),
        "parent_item_id": "" if parent_item_id in (None, "invalid") else str(parent_item_id),
        "setup_group_selection": setup_group_selection,
        "setup_group_code": setup_group_code or (custom_setup_group_code or "").strip(),
        "setup_group_label": setup_group_label or (custom_setup_group_label or "").strip(),
        "is_group_parent": is_group_parent,
        "active": active,
        "unit": unit or "",
        "sort_order": sort_order,
        "default_par_level": "" if default_par_level in (None, "") else str(default_par_level),
        "stale_threshold_days": "" if stale_threshold_days in (None, "") else str(stale_threshold_days),
        "selected_venue_ids": selected_venue_ids,
        "confirm_similar_name": confirm_similar_name,
    }

    errors = []
    similar_matches = []
    if not name:
        errors.append("Item name is required.")
    if default_par_error:
        errors.append(default_par_error)
    if stale_threshold_error:
        errors.append(stale_threshold_error)
    if setup_group_error:
        errors.append(setup_group_error)

    name_changed = existing_item is None or existing_item.name.strip() != name

    if name and has_exact_item_name_duplicate(name, exclude_item_id=getattr(existing_item, "id", None)):
        errors.append("An item with this name already exists.")
    elif name and name_changed:
        similar_matches = find_similar_items(name, exclude_item_id=getattr(existing_item, "id", None))
        if similar_matches and not confirm_similar_name:
            errors.append("Similar item names already exist. Review them and submit again to confirm.")

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

    active_venue_ids = {venue.id for venue in fetch_active_venues()}
    invalid_venue_ids = [venue_id for venue_id in selected_venue_ids if venue_id not in active_venue_ids]
    if invalid_venue_ids:
        errors.append("One or more selected venues are no longer available.")

    if is_group_parent:
        tracking_mode = "quantity"
        parent_item_id = None
        default_par_level = None
        selected_venue_ids = []
        form_values["selected_venue_ids"] = []
    elif existing_item and existing_item.is_group_parent and selected_venue_ids:
        errors.append("Family organizers cannot be assigned directly to venues.")

    if existing_item and is_group_parent:
        has_links = VenueItem.query.filter(VenueItem.item_id == existing_item.id).first() is not None
        if has_links:
            errors.append("Items with venue assignments cannot become family organizers.")

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
        "default_par_level": default_par_level if isinstance(default_par_level, int) or default_par_level is None else None,
        "stale_threshold_days": (
            stale_threshold_days if isinstance(stale_threshold_days, int) or stale_threshold_days is None else None
        ),
        "setup_group_code": setup_group_code if isinstance(setup_group_code, str) or setup_group_code is None else None,
        "setup_group_label": (
            setup_group_label if isinstance(setup_group_label, str) or setup_group_label is None else None
        ),
        "selected_venue_ids": selected_venue_ids,
    }
    return payload, form_values, errors, similar_matches


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


@admin_bp.route("/inventory-rules", methods=["GET", "POST"])
@roles_required("admin")
def inventory_rules():
    policy = ensure_inventory_policy()
    form_values = build_inventory_rules_form_values(policy=policy)

    if request.method == "POST":
        form_values = build_inventory_rules_form_values(source=request.form)
        try:
            default_stale_threshold_days = normalize_optional_threshold_days(
                request.form.get("default_stale_threshold_days"),
                field_label="Global stale threshold",
            )
        except InventoryRuleError as exc:
            flash(str(exc), "error")
        else:
            if default_stale_threshold_days is None:
                flash("Global stale threshold is required.", "error")
            else:
                previous_threshold = int(policy.default_stale_threshold_days or get_default_stale_threshold_days())
                policy.default_stale_threshold_days = default_stale_threshold_days
                if previous_threshold != default_stale_threshold_days:
                    log_inventory_admin_event(
                        "inventory_policy_updated",
                        actor=current_user,
                        subject_type="inventory_policy",
                        subject_id=policy.id,
                        subject_label="Inventory Rules",
                        details={
                            "previous_threshold_days": previous_threshold,
                            "new_threshold_days": default_stale_threshold_days,
                        },
                    )
                db.session.commit()
                flash("Inventory rules saved.", "success")
                return redirect(url_for("admin.inventory_rules"))

    return render_template(
        "admin/inventory_rules.html",
        admin_page_key="inventory_rules",
        form_values=form_values,
        policy=policy,
    )


@admin_bp.route("/tracking-setup", methods=["GET", "POST"])
@roles_required("admin")
def tracking_setup():
    selected_item_id = _parse_optional_id(request.values.get("item_id"))
    tracking_setup_view = build_tracking_setup_view_model(selected_item_id)

    if request.method == "POST":
        selected_item = tracking_setup_view["selected_item"]
        if selected_item is None:
            flash("Select an item to manage tracking assignments.", "error")
        else:
            selected_venue_ids = parse_selected_ids(request.form.getlist("venue_ids"))
            par_overrides = {}
            errors = []
            for venue in tracking_setup_view["venues"]:
                try:
                    par_overrides[venue.id] = normalize_optional_tracking_value(
                        request.form.get(f"par_override_{venue.id}"),
                        field_label=f"Par override for {venue.name}",
                    )
                except InventoryRuleError as exc:
                    errors.append(str(exc))

            if errors:
                for error in errors:
                    flash(error, "error")
            else:
                summary = sync_item_venue_assignments(
                    item=selected_item,
                    selected_venue_ids=selected_venue_ids,
                    par_overrides={venue_id: par_overrides.get(venue_id) for venue_id in selected_venue_ids},
                )
                if any(summary.values()):
                    log_inventory_admin_event(
                        "bulk_tracking_updated",
                        actor=current_user,
                        subject_type="item",
                        subject_id=selected_item.id,
                        subject_label=selected_item.name,
                        details=summary,
                    )
                    db.session.commit()
                    flash("Bulk tracking setup saved.", "success")
                else:
                    db.session.rollback()
                    flash("No bulk tracking changes were needed.", "success")
                return redirect(url_for("admin.tracking_setup", item_id=selected_item.id))

    return render_template(
        "admin/tracking_setup.html",
        admin_page_key="tracking_setup",
        tracking_setup_view=tracking_setup_view,
    )


@admin_bp.get("/audit/users")
@roles_required("admin")
def user_audit():
    return render_template(
        "admin/user_audit.html",
        admin_page_key="audit",
        user_audit=build_admin_user_audit_view_model(),
    )


@admin_bp.get("/feedback")
@roles_required("admin")
def feedback_inbox():
    pin_configured = _is_feedback_review_pin_configured()
    if not pin_configured:
        _clear_feedback_review_pin_session()
    review_unlocked = pin_configured and _is_feedback_inbox_unlocked()
    feedback_inbox_view = None
    if review_unlocked:
        feedback_inbox_view = build_feedback_inbox_view_model(
            search=request.args.get("q"),
            submission_type=request.args.get("type"),
            page=request.args.get("page"),
        )
    return render_template(
        "admin/feedback.html",
        admin_page_key="feedback",
        feedback_inbox=feedback_inbox_view,
        feedback_review_unlocked=review_unlocked,
        feedback_pin_configured=pin_configured,
        feedback_type_options=FEEDBACK_TYPE_FILTER_OPTIONS,
    )


@admin_bp.post("/feedback/pin")
@roles_required("admin")
def unlock_feedback_inbox():
    configured_pin = (current_app.config.get("FEEDBACK_REVIEW_PIN") or "").strip()
    if not configured_pin:
        _clear_feedback_review_pin_session()
        flash("Set FEEDBACK_REVIEW_PIN before opening the feedback inbox.", "error")
        return redirect(url_for("admin.feedback_inbox"))

    submitted_pin = (request.form.get("review_pin") or "").strip()
    if not submitted_pin or not hmac.compare_digest(submitted_pin, configured_pin):
        _clear_feedback_review_pin_session()
        flash("Review PIN is incorrect.", "error")
        return redirect(url_for("admin.feedback_inbox"))

    session[FEEDBACK_REVIEW_SESSION_KEY] = int(current_user.id)
    flash("Feedback inbox unlocked for this session.", "success")
    return redirect(url_for("admin.feedback_inbox"))


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
    similar_name_matches = []
    available_venues = fetch_active_venues()
    global_stale_threshold_days = get_default_stale_threshold_days()
    setup_group_options = get_distinct_setup_groups()
    catalog_filters = parse_item_catalog_filters(request.form if request.method == "POST" else request.args)

    if request.method == "POST":
        show_add_item_form = True
        payload, form_values, errors, similar_name_matches = parse_item_payload()
        if errors:
            for error in errors:
                flash(
                    error,
                    "warning" if error.startswith("Similar item names already exist.") else "error",
                )
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
                default_par_level=payload["default_par_level"],
                stale_threshold_days=payload["stale_threshold_days"],
                setup_group_code=payload["setup_group_code"],
                setup_group_label=payload["setup_group_label"],
            )
            db.session.add(item)
            db.session.flush()
            assignment_summary = sync_item_venue_assignments(
                item=item,
                selected_venue_ids=payload["selected_venue_ids"],
            )
            log_inventory_admin_event(
                "item_created",
                actor=current_user,
                subject_type="item",
                subject_id=item.id,
                subject_label=item.name,
                details={
                    "default_par_level": item.default_par_level,
                    "stale_threshold_days": item.stale_threshold_days,
                    "setup_group": format_setup_group_display(item.setup_group_code, item.setup_group_label),
                    "assigned_venue_count": len(payload["selected_venue_ids"]),
                },
            )
            if any(assignment_summary.values()):
                log_inventory_admin_event(
                    "item_tracking_updated",
                    actor=current_user,
                    subject_type="item",
                    subject_id=item.id,
                    subject_label=item.name,
                    details=assignment_summary,
                )
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
        available_venues=available_venues,
        setup_group_options=setup_group_options,
        custom_setup_group_option_value=CUSTOM_SETUP_GROUP_OPTION_VALUE,
        similar_name_matches=similar_name_matches,
        global_stale_threshold_days=global_stale_threshold_days,
    )


@admin_bp.get("/items/export.csv")
@roles_required("admin")
def export_item_catalog():
    catalog_filters = parse_item_catalog_filters(request.args)
    scope = normalize_export_scope(request.args.get("scope"), default=EXPORT_SCOPE_FILTERED)
    all_items = (
        Item.query.options(selectinload(Item.parent_item), selectinload(Item.child_items))
        .order_by(Item.sort_order.asc(), Item.name.asc(), Item.id.asc())
        .all()
    )

    export_items = all_items
    if scope == EXPORT_SCOPE_FILTERED:
        export_items = [
            item for item in all_items if item_matches_catalog_filters(item, catalog_filters)
        ]

    csv_rows = build_item_catalog_export_rows(build_item_rows(export_items))
    filename = build_item_catalog_export_filename(scope=scope)
    return build_csv_response(ITEM_CATALOG_EXPORT_HEADERS, csv_rows, filename)


@admin_bp.route("/items/<int:item_id>/edit", methods=["GET", "POST"])
@roles_required("admin")
def edit_item(item_id):
    item = (
        Item.query.options(selectinload(Item.parent_item), selectinload(Item.child_items))
        .filter(Item.id == item_id)
        .first_or_404()
    )
    form_values = build_item_form_values(item)
    similar_name_matches = []
    available_venues = fetch_active_venues()
    global_stale_threshold_days = get_default_stale_threshold_days()
    setup_group_options = get_distinct_setup_groups()

    if request.method == "POST":
        payload, form_values, errors, similar_name_matches = parse_item_payload(existing_item=item)
        if errors:
            for error in errors:
                flash(
                    error,
                    "warning" if error.startswith("Similar item names already exist.") else "error",
                )
        else:
            changed_fields = []
            if item.name != payload["name"]:
                changed_fields.append("name")
            if item.item_category != payload["item_category"]:
                changed_fields.append("category")
            if item.tracking_mode != payload["tracking_mode"]:
                changed_fields.append("tracking mode")
            if item.parent_item_id != payload["parent_item_id"]:
                changed_fields.append("parent family")
            if bool(item.is_group_parent) != bool(payload["is_group_parent"]):
                changed_fields.append("family structure")
            if bool(item.active) != bool(payload["active"]):
                changed_fields.append("active status")
            if (item.unit or None) != payload["unit"]:
                changed_fields.append("unit")
            if int(item.sort_order or 0) != int(payload["sort_order"]):
                changed_fields.append("sort order")
            if item.default_par_level != payload["default_par_level"]:
                changed_fields.append("default par")
            if item.stale_threshold_days != payload["stale_threshold_days"]:
                changed_fields.append("stale threshold")
            if (
                (item.setup_group_code or None) != payload["setup_group_code"]
                or (item.setup_group_label or None) != payload["setup_group_label"]
            ):
                changed_fields.append("setup group")

            item.name = payload["name"]
            item.item_type = payload["item_type"]
            item.tracking_mode = payload["tracking_mode"]
            item.item_category = payload["item_category"]
            item.parent_item_id = payload["parent_item_id"]
            item.is_group_parent = payload["is_group_parent"]
            item.active = payload["active"]
            item.unit = payload["unit"]
            item.sort_order = payload["sort_order"]
            item.default_par_level = payload["default_par_level"]
            item.stale_threshold_days = payload["stale_threshold_days"]
            item.setup_group_code = payload["setup_group_code"]
            item.setup_group_label = payload["setup_group_label"]
            assignment_summary = sync_item_venue_assignments(
                item=item,
                selected_venue_ids=payload["selected_venue_ids"],
            )
            if changed_fields:
                log_inventory_admin_event(
                    "item_updated",
                    actor=current_user,
                    subject_type="item",
                    subject_id=item.id,
                    subject_label=item.name,
                    details={"changed_fields": changed_fields},
                )
            if any(assignment_summary.values()):
                log_inventory_admin_event(
                    "item_tracking_updated",
                    actor=current_user,
                    subject_type="item",
                    subject_id=item.id,
                    subject_label=item.name,
                    details=assignment_summary,
                )
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
        setup_group_options=setup_group_options,
        custom_setup_group_option_value=CUSTOM_SETUP_GROUP_OPTION_VALUE,
        form_values=form_values,
        available_venues=available_venues,
        similar_name_matches=similar_name_matches,
        global_stale_threshold_days=global_stale_threshold_days,
        item_delete_guard=build_item_delete_guard(item),
        item_delete_window_days=ITEM_HARD_DELETE_WINDOW_DAYS,
    )


@admin_bp.post("/items/<int:item_id>/delete")
@roles_required("admin")
def hard_delete_item(item_id):
    item = db.get_or_404(Item, item_id)
    delete_guard = build_item_delete_guard(item)
    confirmation_name = (request.form.get("confirm_delete_name") or "").strip()

    if confirmation_name != item.name:
        flash("Type the exact item name to confirm hard delete.", "error")
        return redirect(url_for("admin.edit_item", item_id=item.id))

    if not delete_guard["eligible"]:
        for blocker in delete_guard["blockers"]:
            flash(blocker, "error")
        return redirect(url_for("admin.edit_item", item_id=item.id))

    item_name = item.name
    log_inventory_admin_event(
        "item_hard_deleted",
        actor=current_user,
        subject_type="item",
        subject_id=item.id,
        subject_label=item_name,
    )
    VenueItem.query.filter(VenueItem.item_id == item.id).delete(synchronize_session=False)
    db.session.delete(item)
    db.session.commit()
    flash(f"{item_name} was permanently deleted.", "success")
    return redirect(url_for("admin.items"))


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
    log_inventory_admin_event(
        "item_updated",
        actor=current_user,
        subject_type="item",
        subject_id=it.id,
        subject_label=it.name,
        details={"changed_fields": ["active status"]},
    )
    db.session.commit()

    flash(f"Item deactivated and removed from {len(venues)} venue(s).", "success")
    return redirect(url_for("admin.items"))


@admin_bp.post("/items/<int:item_id>/activate")
@roles_required("admin")
def activate_item(item_id):
    it = db.get_or_404(Item, item_id)
    it.active = True
    log_inventory_admin_event(
        "item_updated",
        actor=current_user,
        subject_type="item",
        subject_id=it.id,
        subject_label=it.name,
        details={"changed_fields": ["active status"]},
    )
    db.session.commit()
    flash("Item activated.", "success")
    return redirect(url_for("admin.items"))
