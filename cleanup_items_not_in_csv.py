import csv
from pathlib import Path

from app import create_app, db
from app.models import Item, VenueItem

ITEMS_CSV = Path("seed_data/items.csv")

def read_csv_item_names(path: Path) -> set[str]:
    keep = set()
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("item_name") or "").strip()
            if name:
                keep.add(name)
    return keep

def cleanup(dry_run: bool = True):
    app = create_app()
    with app.app_context():
        keep_names = read_csv_item_names(ITEMS_CSV)
        if not keep_names:
            print("ERROR: No item names found in seed_data/items.csv. Aborting.")
            return

        # Items currently in DB that are NOT in CSV
        to_delete = Item.query.filter(~Item.name.in_(keep_names)).all()

        print(f"CSV keep list: {len(keep_names)} items")
        print(f"DB items to DELETE: {len(to_delete)} items")
        if to_delete:
            print("Will delete:")
            for it in sorted(to_delete, key=lambda x: x.name.lower()):
                print(f" - {it.name} (id={it.id})")

        if dry_run:
            print("\nDRY RUN only. No changes were made.")
            print("Re-run with dry_run=False to actually delete.")
            return

        # Delete dependent venue_items first (FK-safe)
        ids = [it.id for it in to_delete]
        if ids:
            deleted_links = VenueItem.query.filter(VenueItem.item_id.in_(ids)).delete(synchronize_session=False)
            deleted_items = Item.query.filter(Item.id.in_(ids)).delete(synchronize_session=False)
            db.session.commit()
            print(f"\nDeleted venue_items rows: {deleted_links}")
            print(f"Deleted items rows: {deleted_items}")
        else:
            print("Nothing to delete.")

if __name__ == "__main__":
    # First run as dry-run to preview deletions.
    cleanup(dry_run=False)