from app import create_app, db
from app.models import Venue

CORE_VENUES = [
    "Main Meditation Hall",
    "Veda 1",
    "Veda 2",
    "Veda 3",
    "Veda 4",
    "Ananda Hall",
    "Shakti Hall",
    "Gita Hall",
    "Argun",
]

def seed():
    app = create_app()
    with app.app_context():
        created = 0
        updated = 0

        for name in CORE_VENUES:
            venue = Venue.query.filter_by(name=name).first()

            if venue:
                # Ensure it's core + active
                changed = False
                if not venue.is_core:
                    venue.is_core = True
                    changed = True
                if not venue.active:
                    venue.active = True
                    changed = True

                if changed:
                    updated += 1
            else:
                db.session.add(Venue(name=name, is_core=True, active=True))
                created += 1

        db.session.commit()
        print(f"Core venues seeded. Created: {created}, Updated: {updated}")

if __name__ == "__main__":
    seed()