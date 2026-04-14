import argparse
import random
from collections import Counter, defaultdict

from app import create_app, db
from app.models import Item, Venue, VenueItem, VenueItemCount


SCENARIO_ORDER = ("complete", "partial", "no_counts")


def clamp_ratio(value):
    return min(max(value, 0.0), 1.0)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Seed venue-item supply counts with deterministic coverage and count variety "
            "for the Supplies audit page."
        )
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260413,
        help="Random seed used for deterministic output.",
    )
    parser.add_argument(
        "--wipe-existing",
        action="store_true",
        help="Delete all existing VenueItemCount rows before generating new counts.",
    )
    parser.add_argument(
        "--missing-ratio",
        type=float,
        default=0.30,
        help="Target share of tracked venue-item mappings left as Not Counted.",
    )
    parser.add_argument(
        "--zero-ratio",
        type=float,
        default=0.15,
        help="Target share of generated count rows set to 0.",
    )
    parser.add_argument(
        "--perfect-par-ratio",
        type=float,
        default=0.20,
        help="Target share of generated par-tracked rows set exactly to par.",
    )
    return parser.parse_args()


def load_active_mappings():
    rows = (
        db.session.query(
            Item.id.label("item_id"),
            Item.name.label("item_name"),
            Item.item_type.label("item_type"),
            Venue.id.label("venue_id"),
            Venue.name.label("venue_name"),
            VenueItem.expected_qty.label("expected_qty"),
        )
        .select_from(VenueItem)
        .join(Item, Item.id == VenueItem.item_id)
        .join(Venue, Venue.id == VenueItem.venue_id)
        .filter(
            VenueItem.active == True,
            Item.active == True,
            Venue.active == True,
        )
        .order_by(Item.name.asc(), Venue.name.asc())
        .all()
    )

    mappings_by_item = defaultdict(list)
    all_pairs = []
    for row in rows:
        mapping = {
            "item_id": row.item_id,
            "item_name": row.item_name,
            "item_type": row.item_type,
            "venue_id": row.venue_id,
            "venue_name": row.venue_name,
            "expected_qty": row.expected_qty,
        }
        mappings_by_item[row.item_id].append(mapping)
        all_pairs.append((row.venue_id, row.item_id))
    return mappings_by_item, all_pairs


def choose_missing_pairs(mappings_by_item, target_missing, rng):
    missing_pairs = set()

    for item_index, item_id in enumerate(sorted(mappings_by_item.keys())):
        mappings = list(mappings_by_item[item_id])
        scenario = SCENARIO_ORDER[item_index % len(SCENARIO_ORDER)]

        if scenario == "no_counts":
            for mapping in mappings:
                missing_pairs.add((mapping["venue_id"], mapping["item_id"]))
            continue

        if scenario == "partial":
            if len(mappings) <= 1:
                continue
            missing_count = max(1, min(len(mappings) - 1, len(mappings) // 2))
            for mapping in rng.sample(mappings, k=missing_count):
                missing_pairs.add((mapping["venue_id"], mapping["item_id"]))

    all_pairs = [
        (mapping["venue_id"], mapping["item_id"])
        for item_id in sorted(mappings_by_item.keys())
        for mapping in mappings_by_item[item_id]
    ]
    rng.shuffle(all_pairs)

    for pair in all_pairs:
        if len(missing_pairs) >= target_missing:
            break
        missing_pairs.add(pair)

    return missing_pairs


def choose_count_value(expected_qty, rng):
    if expected_qty is None or expected_qty <= 0:
        return rng.randint(1, 18)

    mode = rng.random()
    if mode < 0.34:
        return max(0, expected_qty - rng.randint(1, max(1, expected_qty // 2 + 1)))
    if mode < 0.67:
        return expected_qty
    return expected_qty + rng.randint(1, max(2, expected_qty // 2 + 1))


def assign_count_values(counted_mappings, args, rng):
    count_values = {}
    for mapping in counted_mappings:
        pair = (mapping["venue_id"], mapping["item_id"])
        count_values[pair] = choose_count_value(mapping["expected_qty"], rng)

    if not counted_mappings:
        return count_values, {"zero": 0, "perfect_par": 0, "under_par": 0, "over_par": 0}

    target_zero = max(1, round(len(counted_mappings) * clamp_ratio(args.zero_ratio)))
    zero_pairs = rng.sample(
        [((m["venue_id"], m["item_id"])) for m in counted_mappings],
        k=min(target_zero, len(counted_mappings)),
    )
    for pair in zero_pairs:
        count_values[pair] = 0

    par_mappings = [m for m in counted_mappings if m["expected_qty"] is not None and m["expected_qty"] > 0]
    if par_mappings:
        target_perfect = max(1, round(len(par_mappings) * clamp_ratio(args.perfect_par_ratio)))
        perfect_mappings = rng.sample(par_mappings, k=min(target_perfect, len(par_mappings)))
        for mapping in perfect_mappings:
            pair = (mapping["venue_id"], mapping["item_id"])
            count_values[pair] = mapping["expected_qty"]

        leftover_par_mappings = [m for m in par_mappings if m not in perfect_mappings]
        if leftover_par_mappings:
            under_mapping = rng.choice(leftover_par_mappings)
            under_pair = (under_mapping["venue_id"], under_mapping["item_id"])
            count_values[under_pair] = max(0, under_mapping["expected_qty"] - 1)

        if len(leftover_par_mappings) > 1:
            over_candidates = [m for m in leftover_par_mappings if (m["venue_id"], m["item_id"]) != under_pair]
            if over_candidates:
                over_mapping = rng.choice(over_candidates)
                over_pair = (over_mapping["venue_id"], over_mapping["item_id"])
                count_values[over_pair] = over_mapping["expected_qty"] + 2

    under_par = 0
    over_par = 0
    perfect_par = 0
    for mapping in counted_mappings:
        par = mapping["expected_qty"]
        if par is None or par <= 0:
            continue
        value = count_values[(mapping["venue_id"], mapping["item_id"])]
        if value == par:
            perfect_par += 1
        elif value < par:
            under_par += 1
        else:
            over_par += 1

    return count_values, {
        "zero": sum(1 for value in count_values.values() if value == 0),
        "perfect_par": perfect_par,
        "under_par": under_par,
        "over_par": over_par,
    }


def coverage_key(tracked_count, counted_count):
    if tracked_count == 0:
        return "not_tracked"
    if counted_count == 0:
        return "no_counts"
    if counted_count < tracked_count:
        return "partial"
    return "complete"


def seed_supplies_counts(args):
    rng = random.Random(args.seed)

    mappings_by_item, all_pairs = load_active_mappings()
    if not all_pairs:
        raise RuntimeError("No active venue/item mappings found. Seed items and venue items first.")

    existing_rows = VenueItemCount.query.all()
    existing_by_pair = {(row.venue_id, row.item_id): row for row in existing_rows}

    if args.wipe_existing and existing_rows:
        VenueItemCount.query.delete()
        db.session.flush()
        existing_by_pair = {}

    target_missing = round(len(all_pairs) * clamp_ratio(args.missing_ratio))
    missing_pairs = choose_missing_pairs(mappings_by_item, target_missing, rng)

    counted_mappings = []
    for item_id in sorted(mappings_by_item.keys()):
        for mapping in mappings_by_item[item_id]:
            pair = (mapping["venue_id"], mapping["item_id"])
            if pair in missing_pairs:
                continue
            counted_mappings.append(mapping)

    count_values, stats = assign_count_values(counted_mappings, args, rng)

    created = 0
    updated = 0
    deleted_for_missing = 0
    for pair in missing_pairs:
        existing = existing_by_pair.get(pair)
        if existing is not None:
            db.session.delete(existing)
            deleted_for_missing += 1

    for mapping in counted_mappings:
        pair = (mapping["venue_id"], mapping["item_id"])
        raw_count = count_values[pair]
        existing = existing_by_pair.get(pair)
        if existing is None:
            db.session.add(
                VenueItemCount(
                    venue_id=mapping["venue_id"],
                    item_id=mapping["item_id"],
                    raw_count=raw_count,
                )
            )
            created += 1
        else:
            existing.raw_count = raw_count
            updated += 1

    db.session.commit()

    item_coverage = Counter()
    for item_id, mappings in mappings_by_item.items():
        tracked_count = len(mappings)
        counted_count = sum(
            1
            for mapping in mappings
            if (mapping["venue_id"], item_id) not in missing_pairs
        )
        item_coverage[coverage_key(tracked_count, counted_count)] += 1

    print("Supplies count seed complete.")
    print(f"Seed: {args.seed}")
    print(f"Tracked mappings: {len(all_pairs)}")
    print(f"Not counted pairs left missing: {len(missing_pairs)}")
    print(f"Rows created: {created}")
    print(f"Rows updated: {updated}")
    print(f"Existing rows deleted for missing state: {deleted_for_missing}")
    print(f"Zero-count rows: {stats['zero']}")
    print(f"Perfect-par rows: {stats['perfect_par']}")
    print(f"Under-par rows: {stats['under_par']}")
    print(f"Over-par rows: {stats['over_par']}")
    print(
        "Item coverage estimate: "
        f"complete={item_coverage['complete']}, "
        f"partial={item_coverage['partial']}, "
        f"no_counts={item_coverage['no_counts']}, "
        f"not_tracked={item_coverage['not_tracked']}"
    )


def main():
    args = parse_args()
    app = create_app()
    with app.app_context():
        seed_supplies_counts(args)


if __name__ == "__main__":
    main()
