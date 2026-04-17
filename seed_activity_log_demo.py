import argparse
import random
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import func
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import (
    Check,
    CheckLine,
    CountLine,
    CountSession,
    Item,
    User,
    Venue,
    VenueItem,
    VenueItemCount,
)


FALLBACK_VENUES = [
    "Main Meditation Hall",
    "Veda 1",
    "Veda 2",
    "Ananda Hall",
    "Shakti Hall",
    "Gita Hall",
]

FALLBACK_ITEMS = [
    ("Yoga Mats", "durable"),
    ("Blankets", "durable"),
    ("Bolsters", "durable"),
    ("Blocks", "durable"),
    ("Hand Towels", "consumable"),
    ("Spray Bottles", "consumable"),
    ("Tissues", "consumable"),
    ("Candles", "consumable"),
    ("Tea Lights", "consumable"),
    ("Water Cups", "consumable"),
    ("Paper Towels", "consumable"),
    ("Sanitizer Bottles", "consumable"),
]

FALLBACK_USERS = [
    ("activity.admin@local.test", "Activity Admin", "admin"),
    ("activity.staff1@local.test", "Avery Staff", "staff"),
    ("activity.staff2@local.test", "Jordan Staff", "staff"),
    ("activity.staff3@local.test", "Taylor Staff", "staff"),
]

STATUS_FLOW = {
    "not_checked": [("good", 0.40), ("ok", 0.25), ("low", 0.20), ("out", 0.15)],
    "good": [("good", 0.58), ("ok", 0.18), ("low", 0.15), ("out", 0.04), ("not_checked", 0.05)],
    "ok": [("good", 0.18), ("ok", 0.42), ("low", 0.20), ("out", 0.10), ("not_checked", 0.10)],
    "low": [("good", 0.10), ("ok", 0.22), ("low", 0.34), ("out", 0.22), ("not_checked", 0.12)],
    "out": [("good", 0.18), ("ok", 0.25), ("low", 0.18), ("out", 0.28), ("not_checked", 0.11)],
}


def choose_weighted(rng, options):
    roll = rng.random()
    cumulative = 0.0
    for value, weight in options:
        cumulative += weight
        if roll <= cumulative:
            return value
    return options[-1][0]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate random activity-log history for local testing."
    )
    parser.add_argument("--days", type=int, default=45, help="How many past days of activity to create.")
    parser.add_argument(
        "--events-per-day",
        type=int,
        default=10,
        help="How many random sessions to create per day.",
    )
    parser.add_argument(
        "--count-ratio",
        type=float,
        default=0.45,
        help="Share of sessions that should be count sessions (0.0 to 1.0).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260407,
        help="Random seed for reproducible output.",
    )
    parser.add_argument(
        "--commit-every",
        type=int,
        default=25,
        help="How many sessions to build between commits.",
    )
    return parser.parse_args()


def ensure_users():
    users = []
    for email, display_name, role in FALLBACK_USERS:
        normalized_email = email.strip().lower()
        user = User.query.filter_by(email=normalized_email).first()
        if user is None:
            user = User(
                email=normalized_email,
                display_name=display_name,
                password_hash=generate_password_hash("local-test-password"),
                role=role,
                active=True,
            )
            db.session.add(user)
        else:
            user.display_name = user.display_name or display_name
            user.role = role
            user.active = True
        users.append(user)

    db.session.flush()
    return users


def ensure_fixture_data():
    active_mapping_count = (
        db.session.query(func.count(VenueItem.id))
        .join(Venue, Venue.id == VenueItem.venue_id)
        .join(Item, Item.id == VenueItem.item_id)
        .filter(VenueItem.active == True, Venue.active == True, Item.active == True)
        .scalar()
    )
    if active_mapping_count:
        return

    venues = []
    for venue_name in FALLBACK_VENUES:
        venue = Venue.query.filter_by(name=venue_name).first()
        if venue is None:
            venue = Venue(name=venue_name, is_core=True, active=True)
            db.session.add(venue)
        else:
            venue.active = True
        venues.append(venue)

    items = []
    for item_name, item_type in FALLBACK_ITEMS:
        item = Item.query.filter_by(name=item_name).first()
        if item is None:
            item = Item(
                name=item_name,
                item_type=item_type,
                item_category=item_type,
                tracking_mode="quantity",
                active=True,
            )
            db.session.add(item)
        else:
            item.active = True
            item.item_type = item_type
            item.item_category = item_type
            item.tracking_mode = "quantity"
        items.append(item)

    db.session.flush()

    for venue in venues:
        for item in items:
            mapping = VenueItem.query.filter_by(venue_id=venue.id, item_id=item.id).first()
            if mapping is None:
                mapping = VenueItem(
                    venue_id=venue.id,
                    item_id=item.id,
                    active=True,
                )
                db.session.add(mapping)
            else:
                mapping.active = True

    db.session.flush()


def load_active_mappings():
    rows = (
        db.session.query(
            Venue.id.label("venue_id"),
            Venue.name.label("venue_name"),
            Item.id.label("item_id"),
            Item.name.label("item_name"),
        )
        .select_from(VenueItem)
        .join(Venue, Venue.id == VenueItem.venue_id)
        .join(Item, Item.id == VenueItem.item_id)
        .filter(VenueItem.active == True, Venue.active == True, Item.active == True)
        .order_by(Venue.name.asc(), Item.name.asc())
        .all()
    )

    mappings_by_venue = defaultdict(list)
    for row in rows:
        mappings_by_venue[row.venue_id].append(
            {
                "venue_id": row.venue_id,
                "venue_name": row.venue_name,
                "item_id": row.item_id,
                "item_name": row.item_name,
            }
        )
    return mappings_by_venue


def load_current_counts():
    rows = VenueItemCount.query.all()
    current_rows = {(row.venue_id, row.item_id): row for row in rows}
    current_values = {(row.venue_id, row.item_id): row.raw_count for row in rows}
    return current_rows, current_values


def next_status_value(rng, previous_status):
    options = STATUS_FLOW.get(previous_status or "not_checked", STATUS_FLOW["not_checked"])
    return choose_weighted(rng, options)


def next_raw_count(rng, previous_value):
    base_value = 0 if previous_value is None else previous_value
    roll = rng.random()

    if roll < 0.12:
        return 0
    if roll < 0.28:
        return max(0, base_value - rng.randint(2, 9))
    if roll < 0.72:
        return max(0, base_value + rng.randint(-2, 3))
    return max(0, base_value + rng.randint(4, 14))


def session_timestamp_for(day_value, slot_index, slots_per_day, rng):
    minutes_open = 14 * 60
    minutes_per_slot = max(minutes_open // max(slots_per_day, 1), 1)
    base_minute = 6 * 60 + slot_index * minutes_per_slot
    offset = rng.randint(0, max(minutes_per_slot - 1, 1))
    event_time = time(hour=((base_minute + offset) // 60) % 24, minute=(base_minute + offset) % 60)
    return datetime.combine(day_value, event_time, tzinfo=timezone.utc)


def seed_activity_history(days, events_per_day, count_ratio, seed, commit_every):
    rng = random.Random(seed)
    users = ensure_users()
    ensure_fixture_data()

    mappings_by_venue = load_active_mappings()
    if not mappings_by_venue:
        raise RuntimeError("No active venue/item mappings available to seed.")

    venue_ids = list(mappings_by_venue.keys())
    current_count_rows, raw_count_state = load_current_counts()
    status_state = {}

    today_utc = datetime.now(timezone.utc).date()
    first_day = today_utc - timedelta(days=max(days - 1, 0))

    total_check_sessions = 0
    total_check_lines = 0
    total_count_sessions = 0
    total_count_lines = 0
    session_counter = 0

    for day_offset in range(days):
        current_day = first_day + timedelta(days=day_offset)
        session_specs = []
        for slot_index in range(events_per_day):
            session_specs.append(
                {
                    "timestamp": session_timestamp_for(current_day, slot_index, events_per_day, rng),
                    "venue_id": rng.choice(venue_ids),
                    "user": rng.choice(users),
                    "mode": "raw_count" if rng.random() < count_ratio else "status",
                }
            )
        session_specs.sort(key=lambda entry: entry["timestamp"])

        for spec in session_specs:
            mappings = mappings_by_venue[spec["venue_id"]]
            if spec["mode"] == "status":
                check = Check(
                    venue_id=spec["venue_id"],
                    user_id=spec["user"].id,
                    created_at=spec["timestamp"],
                )
                db.session.add(check)
                db.session.flush()

                for mapping in mappings:
                    key = (mapping["venue_id"], mapping["item_id"])
                    next_status = next_status_value(rng, status_state.get(key))
                    db.session.add(
                        CheckLine(
                            check_id=check.id,
                            item_id=mapping["item_id"],
                            status=next_status,
                        )
                    )
                    status_state[key] = next_status
                    total_check_lines += 1

                total_check_sessions += 1
            else:
                count_session = CountSession(
                    venue_id=spec["venue_id"],
                    user_id=spec["user"].id,
                    created_at=spec["timestamp"],
                )
                db.session.add(count_session)
                db.session.flush()

                for mapping in mappings:
                    key = (mapping["venue_id"], mapping["item_id"])
                    next_count = next_raw_count(rng, raw_count_state.get(key))
                    db.session.add(
                        CountLine(
                            count_session_id=count_session.id,
                            item_id=mapping["item_id"],
                            raw_count=next_count,
                        )
                    )

                    venue_item_count = current_count_rows.get(key)
                    if venue_item_count is None:
                        venue_item_count = VenueItemCount(
                            venue_id=mapping["venue_id"],
                            item_id=mapping["item_id"],
                            raw_count=next_count,
                            updated_at=spec["timestamp"],
                        )
                        db.session.add(venue_item_count)
                        current_count_rows[key] = venue_item_count
                    else:
                        venue_item_count.raw_count = next_count
                        venue_item_count.updated_at = spec["timestamp"]

                    raw_count_state[key] = next_count
                    total_count_lines += 1

                total_count_sessions += 1

            session_counter += 1
            if session_counter % max(commit_every, 1) == 0:
                db.session.commit()

    db.session.commit()

    estimated_activity_rows = total_check_lines + total_count_lines
    estimated_pages = max((estimated_activity_rows + 49) // 50, 1)
    print("Activity demo seed complete.")
    print(f"Days generated: {days}")
    print(f"Sessions created: checks={total_check_sessions}, raw_counts={total_count_sessions}")
    print(f"Lines created: check_lines={total_check_lines}, count_lines={total_count_lines}")
    print(f"Approx. activity rows now available to test: {estimated_activity_rows}")
    print(f"Approx. pages at 50 results/page: {estimated_pages}")
    print("Tip: run this against your local SQLite DATABASE_URL only.")


def main():
    args = parse_args()
    app = create_app()
    with app.app_context():
        seed_activity_history(
            days=max(args.days, 1),
            events_per_day=max(args.events_per_day, 1),
            count_ratio=min(max(args.count_ratio, 0.0), 1.0),
            seed=args.seed,
            commit_every=max(args.commit_every, 1),
        )


if __name__ == "__main__":
    main()
