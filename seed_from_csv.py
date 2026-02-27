import csv
from pathlib import Path

from app import create_app, db
from app.models import Venue, Item, VenueItem

# Map CSV venue names -> DB venue names
VENUE_ALIASES = {
    "Ananda": "Ananda Hall",
    "Gita": "Gita Hall",
    "Shakti": "Shakti Hall",
}

def to_bool(val) -> bool:
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ("true", "1", "yes", "y")

def to_int_or_none(val):
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return None
    try:
        return int(float(s))
    except ValueError:
        return None

def to_str_or_none(val):
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return None
    return s

def resolve_venue_name(name: str) -> str:
    name = name.strip()
    return VENUE_ALIASES.get(name, name)

def seed(items_csv: Path, venue_items_csv: Path):
    app = create_app()
    with app.app_context():
        created_items = 0
        updated_items = 0
        created_links = 0
        updated_links = 0
        skipped_links = 0

        # ---------- ITEMS ----------
        with items_csv.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                item_name = row["item_name"].strip()
                item_type = row["item_type"].strip().lower()
                active = to_bool(row.get("active", "TRUE"))
                unit = to_str_or_none(row.get("unit"))
                notes = to_str_or_none(row.get("notes"))

                if item_type not in ("durable", "consumable"):
                    print(f"Skipping item with invalid type: {item_name} -> {item_type}")
                    continue

                it = Item.query.filter_by(name=item_name).first()
                if it:
                    changed = False
                    if it.item_type != item_type:
                        it.item_type = item_type
                        changed = True
                    if it.active != active:
                        it.active = active
                        changed = True
                    # unit/notes are optional; only overwrite if provided
                    if unit is not None and getattr(it, "unit", None) != unit:
                        # only if you later add unit/notes columns to Item
                        pass
                    if notes is not None and getattr(it, "notes", None) != notes:
                        pass

                    if changed:
                        updated_items += 1
                else:
                    db.session.add(Item(name=item_name, item_type=item_type, active=active))
                    created_items += 1

        db.session.commit()

        # ---------- VENUE ITEMS ----------
        with venue_items_csv.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                venue_name = resolve_venue_name(row["venue_name"])
                item_name = row["item_name"].strip()
                active = to_bool(row.get("active", "TRUE"))
                expected_qty = to_int_or_none(row.get("expected_qty"))
                reorder_threshold = to_int_or_none(row.get("reorder_threshold"))
                notes = to_str_or_none(row.get("notes"))

                venue = Venue.query.filter_by(name=venue_name).first()
                if not venue:
                    print(f"Skipping mapping (venue not found): {venue_name} / {item_name}")
                    skipped_links += 1
                    continue

                item = Item.query.filter_by(name=item_name).first()
                if not item:
                    print(f"Skipping mapping (item not found): {venue_name} / {item_name}")
                    skipped_links += 1
                    continue

                link = VenueItem.query.filter_by(venue_id=venue.id, item_id=item.id).first()
                if link:
                    changed = False
                    if link.active != active:
                        link.active = active
                        changed = True
                    if link.expected_qty != expected_qty:
                        link.expected_qty = expected_qty
                        changed = True
                    if link.reorder_threshold != reorder_threshold:
                        link.reorder_threshold = reorder_threshold
                        changed = True
                    # notes currently not in VenueItem model; if you add it later, you can set it here.
                    if changed:
                        updated_links += 1
                else:
                    db.session.add(
                        VenueItem(
                            venue_id=venue.id,
                            item_id=item.id,
                            active=active,
                            expected_qty=expected_qty,
                            reorder_threshold=reorder_threshold,
                        )
                    )
                    created_links += 1

        db.session.commit()

        print("Seed complete ✅")
        print(f"Items: created={created_items}, updated={updated_items}")
        print(f"VenueItems: created={created_links}, updated={updated_links}, skipped={skipped_links}")

if __name__ == "__main__":
    seed(
        Path("seed_data/items.csv"),
        Path("seed_data/venue_items.csv"),
    )