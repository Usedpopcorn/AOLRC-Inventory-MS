"""Repo-native validation entrypoint for local and agent workflows.

This script keeps the most common non-destructive checks in one place:
- app boot smoke
- template compilation through the real Flask/Jinja environment
- a couple of lightweight HTTP route checks
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("FLASK_ENV", "development")
os.environ.setdefault("SECRET_KEY", "local-validation-secret")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app import create_app, db  # noqa: E402


def validate_templates(app) -> None:
    template_root = ROOT / "templates"
    for path in sorted(template_root.rglob("*.html")):
        template_name = path.relative_to(template_root).as_posix()
        app.jinja_env.get_template(template_name)
    print(f"[OK] Compiled {len(list(template_root.rglob('*.html')))} Jinja templates")


def validate_http_smoke(app) -> None:
    with app.app_context():
        db.drop_all()
        db.create_all()

    with app.test_client() as client:
        health = client.get("/healthz")
        if health.status_code != 200:
            raise RuntimeError(f"/healthz returned {health.status_code}")

        login = client.get("/login")
        if login.status_code != 200:
            raise RuntimeError(f"/login returned {login.status_code}")

        dashboard = client.get("/dashboard")
        if dashboard.status_code != 302:
            raise RuntimeError(f"/dashboard returned {dashboard.status_code} instead of 302")

    print("[OK] App boot smoke passed")
    print("[OK] HTTP smoke checks passed")


def main() -> int:
    app = create_app()
    validate_templates(app)
    validate_http_smoke(app)
    print("[OK] Repo validation completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
