import pytest

from app import db
from app.models import Item
from app.services.spreadsheet_compat import (
    CUSTOM_SETUP_GROUP_OPTION_VALUE,
    SpreadsheetCompatibilityError,
    build_reorder_decision,
    resolve_setup_group_selection,
)


def test_resolve_setup_group_selection_rejects_duplicate_builtin_code_for_custom_group(app):
    with app.app_context():
        with pytest.raises(SpreadsheetCompatibilityError):
            resolve_setup_group_selection(
                selection_value=CUSTOM_SETUP_GROUP_OPTION_VALUE,
                custom_code="01",
                custom_label="Something Else",
            )


def test_resolve_setup_group_selection_reuses_existing_custom_code(app):
    with app.app_context():
        db.session.add(
            Item(
                name="Custom Group Seed",
                item_type="consumable",
                tracking_mode="quantity",
                item_category="consumable",
                active=True,
                setup_group_code="06A",
                setup_group_label="Audio Visual Support",
            )
        )
        db.session.commit()

        code, label = resolve_setup_group_selection(selection_value="06A")

    assert code == "06A"
    assert label == "Audio Visual Support"


def test_resolve_setup_group_selection_rejects_duplicate_custom_code_for_custom_group(app):
    with app.app_context():
        db.session.add(
            Item(
                name="Existing Custom Group",
                item_type="consumable",
                tracking_mode="quantity",
                item_category="consumable",
                active=True,
                setup_group_code="06A",
                setup_group_label="Audio Visual Support",
            )
        )
        db.session.commit()

        with pytest.raises(SpreadsheetCompatibilityError):
            resolve_setup_group_selection(
                selection_value=CUSTOM_SETUP_GROUP_OPTION_VALUE,
                custom_code="06A",
                custom_label="Audio Visual Support",
            )


def test_resolve_setup_group_selection_rejects_duplicate_custom_label_for_custom_group(app):
    with app.app_context():
        db.session.add(
            Item(
                name="Existing Custom Group",
                item_type="consumable",
                tracking_mode="quantity",
                item_category="consumable",
                active=True,
                setup_group_code="06A",
                setup_group_label="Audio Visual Support",
            )
        )
        db.session.commit()

        with pytest.raises(SpreadsheetCompatibilityError):
            resolve_setup_group_selection(
                selection_value=CUSTOM_SETUP_GROUP_OPTION_VALUE,
                custom_code="07B",
                custom_label="Audio Visual Support",
            )


@pytest.mark.parametrize(
    (
        "tracking_mode",
        "raw_count",
        "effective_par",
        "expected_state",
        "expected_suggested",
        "expected_over_par",
    ),
    [
        ("quantity", 3, 8, "comparable", 5, 0),
        ("quantity", 8, 8, "comparable", 0, 0),
        ("quantity", 10, 8, "comparable", 0, 2),
        ("quantity", None, 8, "no_count", None, None),
        ("quantity", 4, None, "no_par", None, None),
        ("singleton_asset", 1, 8, "singleton_asset", None, None),
    ],
)
def test_build_reorder_decision_states(
    tracking_mode,
    raw_count,
    effective_par,
    expected_state,
    expected_suggested,
    expected_over_par,
):
    decision = build_reorder_decision(
        tracking_mode=tracking_mode,
        raw_count=raw_count,
        effective_par=effective_par,
    )

    assert decision.state == expected_state
    assert decision.suggested_order_qty == expected_suggested
    assert decision.over_par_qty == expected_over_par
