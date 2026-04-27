import pytest

from app.routes.main import build_overall_status_badge as dashboard_build_overall_status_badge
from app.services.inventory_status import (
    build_overall_status_badge as shared_build_overall_status_badge,
)


@pytest.mark.parametrize(
    "badge_builder",
    [shared_build_overall_status_badge, dashboard_build_overall_status_badge],
)
def test_overall_status_badge_keeps_good_without_asterisk_when_fully_good(badge_builder):
    counts = {"good": 5, "ok": 0, "low": 0, "out": 0, "not_checked": 0}

    badge = badge_builder(total_tracked=5, counts=counts)

    assert badge["key"] == "good"
    assert badge["text"] == "Good"
    assert badge["icon_class"] == "bi-check-circle-fill"


@pytest.mark.parametrize(
    "badge_builder",
    [shared_build_overall_status_badge, dashboard_build_overall_status_badge],
)
def test_overall_status_badge_adds_asterisk_when_some_items_are_ok(badge_builder):
    counts = {"good": 4, "ok": 1, "low": 0, "out": 0, "not_checked": 0}

    badge = badge_builder(total_tracked=5, counts=counts)

    assert badge["key"] == "good"
    assert badge["text"] == "Good*"
    assert badge["icon_class"] == "bi-check-circle-fill"


@pytest.mark.parametrize(
    "badge_builder",
    [shared_build_overall_status_badge, dashboard_build_overall_status_badge],
)
def test_overall_status_badge_adds_asterisk_when_some_items_are_not_checked(badge_builder):
    counts = {"good": 4, "ok": 0, "low": 0, "out": 0, "not_checked": 1}

    badge = badge_builder(total_tracked=5, counts=counts)

    assert badge["key"] == "good"
    assert badge["text"] == "Good*"
    assert badge["icon_class"] == "bi-check-circle-fill"
