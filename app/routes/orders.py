from datetime import datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.orm import selectinload

from app import db
from app.authz import roles_required
from app.models import (
    ORDER_BATCH_TYPES,
    ORDER_LINE_STATUSES,
    OrderBatch,
    OrderLine,
    normalize_order_batch_type,
    normalize_order_line_status,
)
from app.services.csv_exports import (
    EXPORT_SCOPE_FILTERED,
    EXPORT_SCOPE_FULL,
    build_csv_response,
    normalize_export_scope,
)
from app.services.inventory_rules import InventoryRuleError
from app.services.inventory_status import format_timestamp, get_app_timezone
from app.services.orders import (
    ORDER_BATCH_TYPE_LABELS,
    ORDER_LINE_EXPORT_HEADERS,
    ORDER_LINE_STATUS_LABELS,
    ORDER_PURCHASE_SUMMARY_EXPORT_HEADERS,
    build_grouped_order_line_summaries,
    build_grouped_summary_support_text,
    build_order_batch_summary,
    build_order_line_csv_rows,
    build_order_line_export_filename,
    build_order_line_search_text,
    build_order_purchase_summary_export_filename,
    build_order_scope_options,
    build_purchase_summary_csv_rows,
    build_purchase_summary_rows,
    build_purchase_summary_support_text,
    bulk_update_order_lines_status,
    create_order_batch,
    filter_purchase_summary_rows,
    format_order_batch_type_label,
    format_order_line_status_label,
    format_order_snapshot_setup_group_display,
    format_user_label,
    update_order_batch_notes,
    update_order_line,
    validate_order_batch_type_value,
    validate_order_bulk_action_value,
    validate_order_line_status_value,
    validate_order_scope_setup_group,
    validate_order_scope_venue_id,
)

orders_bp = Blueprint("orders", __name__)


def build_order_batch_form_values(form_data=None):
    default_type = "monthly"
    today_stamp = datetime.now(get_app_timezone()).strftime("%Y-%m-%d")
    if form_data is None:
        default_name = f"{ORDER_BATCH_TYPE_LABELS[default_type]} Order {today_stamp}"
        return {
            "name": default_name,
            "batch_type": default_type,
            "notes": "",
            "scope_venue_id": "",
            "scope_setup_group": "",
            "invalid_batch_type": "",
            "invalid_scope_venue_id": "",
            "invalid_scope_setup_group": "",
        }

    raw_batch_type = (form_data.get("batch_type") or "").strip()
    raw_scope_venue_id = (form_data.get("scope_venue_id") or "").strip()
    raw_scope_setup_group = (form_data.get("scope_setup_group") or "").strip()
    return {
        "name": (form_data.get("name") or "").strip(),
        "batch_type": raw_batch_type if raw_batch_type in ORDER_BATCH_TYPES else "",
        "notes": (form_data.get("notes") or "").strip(),
        "scope_venue_id": raw_scope_venue_id if raw_scope_venue_id.isdigit() else "",
        "scope_setup_group": raw_scope_setup_group,
        "invalid_batch_type": "" if raw_batch_type in ORDER_BATCH_TYPES else raw_batch_type,
        "invalid_scope_venue_id": "",
        "invalid_scope_setup_group": "",
    }


def normalize_order_list_filters(args=None, *, search_key="q", type_key="type"):
    source = args or request.args
    search_query = (source.get(search_key) or "").strip()
    raw_batch_type = (source.get(type_key) or "").strip()
    batch_type = normalize_order_batch_type(raw_batch_type)
    raw_batch_type_key = raw_batch_type.lower()
    if (
        not raw_batch_type
        or batch_type not in ORDER_BATCH_TYPES
        or batch_type != raw_batch_type_key
    ):
        batch_type = "all"
    return {
        "q": search_query,
        "type": batch_type,
    }


def normalize_order_line_filters(args=None, *, search_key="q", status_key="status"):
    source = args or request.args
    search_query = (source.get(search_key) or "").strip()
    raw_status = (source.get(status_key) or "").strip()
    status = normalize_order_line_status(raw_status)
    if not raw_status or status not in ORDER_LINE_STATUSES or status != raw_status.lower():
        status = "all"
    return {
        "q": search_query,
        "status": status,
    }


def normalize_order_purchase_filters(args=None, *, search_key="purchase_q"):
    source = args or request.args
    return {
        "q": (source.get(search_key) or "").strip(),
    }


def build_order_batch_list_rows(*, filters):
    batches = (
        OrderBatch.query.options(
            selectinload(OrderBatch.created_by),
            selectinload(OrderBatch.lines),
        )
        .order_by(OrderBatch.created_at.desc(), OrderBatch.id.desc())
        .all()
    )

    search_query = filters["q"].lower()
    selected_type = filters["type"]
    rows = []
    for batch in batches:
        if selected_type != "all" and batch.batch_type != selected_type:
            continue

        summary = build_order_batch_summary(batch)
        row = {
            "id": batch.id,
            "name": batch.name,
            "batch_type": batch.batch_type,
            "batch_type_label": format_order_batch_type_label(batch.batch_type),
            "created_at": batch.created_at,
            "created_at_text": format_timestamp(batch.created_at, missing_text="Unknown time"),
            "created_by_label": format_user_label(batch.created_by),
            "notes": batch.notes,
            "notes_excerpt": (batch.notes or "").strip()[:160],
            "summary": summary,
        }
        if search_query:
            search_text = " ".join(
                [
                    row["name"],
                    row["batch_type_label"],
                    row["created_by_label"],
                    row["notes"] or "",
                ]
            ).lower()
            if search_query not in search_text:
                continue
        rows.append(row)
    return rows


def build_order_line_rows(batch, *, filters):
    rows = []
    search_query = filters["q"].lower()
    status_filter = filters["status"]
    next_path = url_for("orders.detail", batch_id=batch.id)

    lines = sorted(
        batch.lines,
        key=lambda line: (
            (line.venue_name_snapshot or "").lower(),
            (line.item_name_snapshot or "").lower(),
            line.id,
        ),
    )
    for line in lines:
        normalized_status = normalize_order_line_status(line.status)
        if status_filter != "all" and normalized_status != status_filter:
            continue
        if search_query and search_query not in build_order_line_search_text(line):
            continue

        quick_check_url = None
        if line.venue_id and line.item_id:
            quick_check_url = url_for(
                "venue_items.quick_check",
                venue_id=line.venue_id,
                focus_item_id=line.item_id,
                mode="raw_counts",
                next=next_path,
            )

        rows.append(
            {
                "id": line.id,
                "item_id": line.item_id,
                "venue_id": line.venue_id,
                "item_name": line.item_name_snapshot,
                "venue_name": line.venue_name_snapshot,
                "setup_group_code": line.setup_group_code_snapshot,
                "setup_group_label": line.setup_group_label_snapshot,
                "setup_group_display": format_order_snapshot_setup_group_display(
                    line.setup_group_code_snapshot,
                    line.setup_group_label_snapshot,
                ),
                "count_snapshot": line.count_snapshot,
                "par_snapshot": line.par_snapshot,
                "count_par_text": (
                    f"{line.count_snapshot} / {line.par_snapshot}"
                    if line.count_snapshot is not None and line.par_snapshot is not None
                    else "Snapshot unavailable"
                ),
                "suggested_order_qty_snapshot": int(line.suggested_order_qty_snapshot or 0),
                "over_par_qty_snapshot": int(line.over_par_qty_snapshot or 0),
                "actual_ordered_qty": line.actual_ordered_qty,
                "status": normalized_status,
                "status_label": format_order_line_status_label(normalized_status),
                "notes": line.notes,
                "quick_check_url": quick_check_url,
            }
        )
    return rows


def render_orders_index_page(
    *,
    batch_filters=None,
    create_form_values=None,
    create_form_errors=None,
    scope_options=None,
    status_code=200,
):
    resolved_batch_filters = batch_filters or normalize_order_list_filters()
    resolved_scope_options = scope_options or build_order_scope_options()
    context = {
        "batch_rows": build_order_batch_list_rows(filters=resolved_batch_filters),
        "batch_filters": resolved_batch_filters,
        "batch_type_labels": ORDER_BATCH_TYPE_LABELS,
        "create_form_values": create_form_values or build_order_batch_form_values(),
        "create_form_errors": create_form_errors or [],
        "scope_options": resolved_scope_options,
    }
    return render_template("orders/index.html", **context), status_code


def parse_order_batch_submission(form_data, *, scope_options):
    form_values = build_order_batch_form_values(form_data)
    errors = []

    name = (form_data.get("name") or "").strip()
    if not name:
        errors.append("Batch name is required.")

    try:
        batch_type = validate_order_batch_type_value(form_data.get("batch_type"))
    except InventoryRuleError as exc:
        errors.append(str(exc))
        form_values["invalid_batch_type"] = (form_data.get("batch_type") or "").strip()
        batch_type = None

    try:
        scope_venue_id = validate_order_scope_venue_id(
            form_data.get("scope_venue_id"),
            valid_venue_ids=scope_options["valid_venue_ids"],
        )
    except InventoryRuleError as exc:
        errors.append(str(exc))
        form_values["invalid_scope_venue_id"] = (form_data.get("scope_venue_id") or "").strip()
        form_values["scope_venue_id"] = ""
        scope_venue_id = None

    try:
        scope_setup_group = validate_order_scope_setup_group(
            form_data.get("scope_setup_group"),
            valid_setup_group_values=scope_options["valid_setup_group_values"],
        )
    except InventoryRuleError as exc:
        errors.append(str(exc))
        form_values["invalid_scope_setup_group"] = (
            form_data.get("scope_setup_group") or ""
        ).strip()
        form_values["scope_setup_group"] = ""
        scope_setup_group = None

    return {
        "errors": errors,
        "form_values": form_values,
        "values": {
            "name": name,
            "batch_type": batch_type,
            "notes": (form_data.get("notes") or "").strip(),
            "scope_venue_id": scope_venue_id,
            "scope_setup_group": scope_setup_group,
        },
    }


def build_order_detail_context(batch, *, line_filters, purchase_filters=None):
    line_rows = build_order_line_rows(batch, filters=line_filters)
    resolved_purchase_filters = purchase_filters or normalize_order_purchase_filters()
    purchase_summary_rows = filter_purchase_summary_rows(
        build_purchase_summary_rows(line_rows),
        resolved_purchase_filters["q"],
    )
    setup_group_summaries = build_grouped_order_line_summaries(
        line_rows,
        group_by="setup_group",
    )
    venue_summaries = build_grouped_order_line_summaries(
        line_rows,
        group_by="venue",
    )
    return {
        "batch": batch,
        "batch_summary": build_order_batch_summary(batch),
        "batch_type_label": format_order_batch_type_label(batch.batch_type),
        "batch_created_by_label": format_user_label(batch.created_by),
        "line_rows": line_rows,
        "purchase_summary_rows": purchase_summary_rows,
        "purchase_summary_support": build_purchase_summary_support_text(
            purchase_summary_rows
        ),
        "purchase_summary_filters": resolved_purchase_filters,
        "line_filters": line_filters,
        "status_labels": ORDER_LINE_STATUS_LABELS,
        "setup_group_summaries": setup_group_summaries,
        "setup_group_summary_support": build_grouped_summary_support_text(
            setup_group_summaries,
            singular_label="group",
            plural_label="groups",
        ),
        "venue_summaries": venue_summaries,
        "venue_summary_support": build_grouped_summary_support_text(
            venue_summaries,
            singular_label="venue",
            plural_label="venues",
        ),
    }


def load_order_batch(batch_id):
    return (
        OrderBatch.query.options(
            selectinload(OrderBatch.created_by),
            selectinload(OrderBatch.lines),
        )
        .filter_by(id=batch_id)
        .first_or_404()
    )


def build_order_lines_export_url(batch_id, *, line_filters, scope=EXPORT_SCOPE_FILTERED):
    return url_for(
        "orders.export_line_batch",
        batch_id=batch_id,
        q=line_filters["q"] or None,
        status=line_filters["status"] if line_filters["status"] != "all" else None,
        scope=scope,
    )


def build_order_purchase_summary_export_url(
    batch_id,
    *,
    line_filters,
    purchase_query="",
    scope=EXPORT_SCOPE_FILTERED,
):
    return url_for(
        "orders.export_purchase_summary",
        batch_id=batch_id,
        q=line_filters["q"] or None,
        status=line_filters["status"] if line_filters["status"] != "all" else None,
        purchase_q=purchase_query or None,
        scope=scope,
    )


@orders_bp.get("/orders")
@roles_required("viewer", "staff", "admin")
def index():
    filters = normalize_order_list_filters()
    return render_orders_index_page(batch_filters=filters)


@orders_bp.post("/orders")
@roles_required("admin")
def create_batch():
    batch_filters = normalize_order_list_filters(
        request.form,
        search_key="return_batch_q",
        type_key="return_batch_type",
    )
    scope_options = build_order_scope_options()
    submission = parse_order_batch_submission(request.form, scope_options=scope_options)
    if submission["errors"]:
        return render_orders_index_page(
            batch_filters=batch_filters,
            create_form_values=submission["form_values"],
            create_form_errors=submission["errors"],
            scope_options=scope_options,
            status_code=400,
        )

    try:
        batch = create_order_batch(
            name=submission["values"]["name"],
            batch_type=submission["values"]["batch_type"],
            notes=submission["values"]["notes"],
            created_by_user_id=current_user.id,
            scope_venue_id=submission["values"]["scope_venue_id"],
            scope_setup_group=submission["values"]["scope_setup_group"],
        )
        db.session.commit()
    except InventoryRuleError as exc:
        db.session.rollback()
        return render_orders_index_page(
            batch_filters=batch_filters,
            create_form_values=submission["form_values"],
            create_form_errors=[str(exc)],
            scope_options=scope_options,
            status_code=400,
        )

    line_count = len(batch.lines)
    if line_count:
        line_suffix = "s" if line_count != 1 else ""
        flash(
            f"Created {batch.name} with {line_count} actionable order line{line_suffix}.",
            "success",
        )
    else:
        flash(f"Created {batch.name}. No actionable order lines were found.", "success")
    return redirect(url_for("orders.detail", batch_id=batch.id))


@orders_bp.get("/orders/<int:batch_id>")
@roles_required("viewer", "staff", "admin")
def detail(batch_id):
    batch = load_order_batch(batch_id)
    line_filters = normalize_order_line_filters()
    purchase_filters = normalize_order_purchase_filters()
    context = build_order_detail_context(
        batch,
        line_filters=line_filters,
        purchase_filters=purchase_filters,
    )
    export_url = build_order_lines_export_url(batch.id, line_filters=line_filters)
    if request.args.get("detail_partial") == "1":
        html = render_template(
            "orders/_detail_content.html",
            line_export_url=build_order_lines_export_url(
                batch.id,
                line_filters=line_filters,
                scope=EXPORT_SCOPE_FILTERED,
            ),
            full_line_export_url=build_order_lines_export_url(
                batch.id,
                line_filters={"q": "", "status": "all"},
                scope=EXPORT_SCOPE_FULL,
            ),
            purchase_summary_export_url=build_order_purchase_summary_export_url(
                batch.id,
                line_filters=line_filters,
                purchase_query=purchase_filters["q"],
                scope=EXPORT_SCOPE_FILTERED,
            ),
            full_purchase_summary_export_url=build_order_purchase_summary_export_url(
                batch.id,
                line_filters={"q": "", "status": "all"},
                scope=EXPORT_SCOPE_FULL,
            ),
            **context,
        )
        return jsonify(
            {
                "html": html,
                "export_url": export_url,
            }
        )
    return render_template(
        "orders/detail.html",
        export_url=export_url,
        export_base_url=url_for("orders.export_line_batch", batch_id=batch.id),
        detail_partial_url=url_for("orders.detail", batch_id=batch.id),
        line_export_url=build_order_lines_export_url(
            batch.id,
            line_filters=line_filters,
            scope=EXPORT_SCOPE_FILTERED,
        ),
        full_line_export_url=build_order_lines_export_url(
            batch.id,
            line_filters={"q": "", "status": "all"},
            scope=EXPORT_SCOPE_FULL,
        ),
        purchase_summary_export_url=build_order_purchase_summary_export_url(
            batch.id,
            line_filters=line_filters,
            purchase_query=purchase_filters["q"],
            scope=EXPORT_SCOPE_FILTERED,
        ),
        full_purchase_summary_export_url=build_order_purchase_summary_export_url(
            batch.id,
            line_filters={"q": "", "status": "all"},
            scope=EXPORT_SCOPE_FULL,
        ),
        **context,
    )


@orders_bp.get("/orders/<int:batch_id>/export.csv")
@roles_required("viewer", "staff", "admin")
def export_line_batch(batch_id):
    batch = load_order_batch(batch_id)
    scope = normalize_export_scope(request.args.get("scope"))
    if scope == EXPORT_SCOPE_FULL:
        line_filters = {"q": "", "status": "all"}
    else:
        line_filters = normalize_order_line_filters()
    line_rows = build_order_line_rows(batch, filters=line_filters)
    csv_rows = build_order_line_csv_rows(batch, line_rows)
    filename = build_order_line_export_filename(batch, scope=scope)
    return build_csv_response(ORDER_LINE_EXPORT_HEADERS, csv_rows, filename)


@orders_bp.get("/orders/<int:batch_id>/purchase-summary-export.csv")
@roles_required("viewer", "staff", "admin")
def export_purchase_summary(batch_id):
    batch = load_order_batch(batch_id)
    scope = normalize_export_scope(request.args.get("scope"))
    if scope == EXPORT_SCOPE_FULL:
        line_filters = {"q": "", "status": "all"}
        purchase_query = ""
    else:
        line_filters = normalize_order_line_filters()
        purchase_query = normalize_order_purchase_filters()["q"]

    line_rows = build_order_line_rows(batch, filters=line_filters)
    purchase_summary_rows = filter_purchase_summary_rows(
        build_purchase_summary_rows(line_rows),
        purchase_query,
    )
    csv_rows = build_purchase_summary_csv_rows(batch, purchase_summary_rows)
    filename = build_order_purchase_summary_export_filename(batch, scope=scope)
    return build_csv_response(ORDER_PURCHASE_SUMMARY_EXPORT_HEADERS, csv_rows, filename)


@orders_bp.post("/orders/<int:batch_id>")
@roles_required("admin")
def update_batch(batch_id):
    batch = db.get_or_404(OrderBatch, batch_id)
    try:
        update_order_batch_notes(batch, notes=request.form.get("notes"))
        db.session.commit()
    except InventoryRuleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        flash("Batch notes updated.", "success")
    redirect_filters = normalize_order_line_filters(
        request.form,
        search_key="return_q",
        status_key="return_status",
    )
    return redirect(url_for("orders.detail", batch_id=batch.id, **redirect_filters))


@orders_bp.post("/orders/<int:batch_id>/lines/bulk")
@roles_required("admin")
def update_line_items_bulk(batch_id):
    try:
        validate_order_bulk_action_value(request.form.get("bulk_action"))
        validate_order_line_status_value(request.form.get("bulk_status"))
        updated_lines = bulk_update_order_lines_status(
            batch_id=batch_id,
            raw_line_ids=request.form.getlist("line_ids"),
            status_raw=request.form.get("bulk_status"),
        )
        db.session.commit()
    except InventoryRuleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        line_count = len(updated_lines)
        line_suffix = "s" if line_count != 1 else ""
        status_label = format_order_line_status_label(request.form.get("bulk_status"))
        flash(f"Updated {line_count} order line{line_suffix} to {status_label}.", "success")
    redirect_filters = normalize_order_line_filters(
        request.form,
        search_key="return_q",
        status_key="return_status",
    )
    return redirect(url_for("orders.detail", batch_id=batch_id, **redirect_filters))


@orders_bp.post("/orders/<int:batch_id>/lines/<int:line_id>")
@roles_required("admin")
def update_line_item(batch_id, line_id):
    line = OrderLine.query.filter_by(id=line_id, order_batch_id=batch_id).first_or_404()
    try:
        update_order_line(
            line=line,
            actual_ordered_qty_raw=request.form.get("actual_ordered_qty"),
            status_raw=request.form.get("line_status"),
            notes=request.form.get("notes"),
        )
        db.session.commit()
    except InventoryRuleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        flash(f"Updated order line for {line.item_name_snapshot}.", "success")
    redirect_filters = normalize_order_line_filters(
        request.form,
        search_key="return_q",
        status_key="return_status",
    )
    return redirect(url_for("orders.detail", batch_id=batch_id, **redirect_filters))
