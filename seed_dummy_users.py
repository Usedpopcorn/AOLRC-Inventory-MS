import argparse
import os
import sys

from werkzeug.security import check_password_hash, generate_password_hash

from app import create_app, db
from app.models import User, normalize_role


DUMMY_USERS = [
    ("admin@example.com", "Admin User", "admin"),
    ("staff@example.com", "Staff User", "staff"),
    ("viewer@example.com", "Viewer User", "viewer"),
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Ensure active dummy users exist for local SQLite development."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only verify required accounts exist and are active; do not create/update.",
    )
    parser.add_argument(
        "--password",
        default="local-test-password",
        help="Password used when creating/updating dummy users.",
    )
    return parser.parse_args()


def is_sqlite_url(value):
    return (value or "").strip().lower().startswith("sqlite:")


def validate_dummy_users():
    issues = []
    for email, _display_name, role in DUMMY_USERS:
        user = User.query.filter_by(email=email).first()
        if user is None:
            issues.append(f"Missing user: {email}")
            continue
        if normalize_role(user.role) != role:
            issues.append(f"Wrong role for {email}: expected={role}, actual={user.role}")
        if not user.active:
            issues.append(f"Inactive user: {email}")
    return issues


def seed_dummy_users(password):
    created = 0
    updated = 0

    for email, display_name, role in DUMMY_USERS:
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(
                email=email,
                display_name=display_name,
                password_hash=generate_password_hash(password),
                role=role,
                active=True,
            )
            db.session.add(user)
            created += 1
            continue

        changed = False
        if user.display_name != display_name:
            user.display_name = display_name
            changed = True
        if normalize_role(user.role) != role:
            user.role = role
            changed = True
        if not user.active:
            user.active = True
            changed = True
        if not user.password_hash or not check_password_hash(user.password_hash, password):
            user.password_hash = generate_password_hash(password)
            changed = True
        if changed:
            updated += 1

    db.session.commit()
    print(f"Dummy users ready. Created: {created}, Updated: {updated}")


def main():
    args = parse_args()
    app = create_app()

    db_url = os.getenv("DATABASE_URL", "")
    if not is_sqlite_url(db_url):
        print("Warning: DATABASE_URL is not SQLite; continuing anyway.")

    with app.app_context():
        if args.check:
            issues = validate_dummy_users()
            if issues:
                print("Dummy user check failed:")
                for issue in issues:
                    print(f"- {issue}")
                sys.exit(1)
            print("Dummy user check passed: admin/staff/viewer accounts are active.")
            return

        seed_dummy_users(args.password)
        issues = validate_dummy_users()
        if issues:
            print("Dummy users seeded but validation failed:")
            for issue in issues:
                print(f"- {issue}")
            sys.exit(1)
        print("Dummy user validation passed.")


if __name__ == "__main__":
    main()
