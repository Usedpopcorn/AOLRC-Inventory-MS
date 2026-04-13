import secrets
import string

from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import User


ADMIN_EMAILS = [
    "kaminskems@appstate.edu",
    "brasherjr@appstate.edu",
    "sternjm@appstate.edu",
    "decamillistw@appstate.edu",
]


def make_password(length=9):
    alphabet = string.ascii_letters + string.digits
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.islower() for c in password) and any(c.isupper() for c in password) and any(
            c.isdigit() for c in password
        ):
            return password


def seed_admins():
    credentials = []

    for email in ADMIN_EMAILS:
        password = make_password()
        existing = User.query.filter_by(email=email).first()
        display_name = email.split("@", 1)[0]

        if existing is None:
            existing = User(
                email=email,
                display_name=display_name,
                password_hash=generate_password_hash(password),
                role="admin",
                active=True,
            )
            db.session.add(existing)
        else:
            existing.display_name = existing.display_name or display_name
            existing.password_hash = generate_password_hash(password)
            existing.role = "admin"
            existing.active = True

        credentials.append((email, password))

    db.session.commit()

    print("Live test admin users are ready:")
    for email, password in credentials:
        print(f"{email},{password}")


def main():
    app = create_app()
    with app.app_context():
        seed_admins()


if __name__ == "__main__":
    main()
