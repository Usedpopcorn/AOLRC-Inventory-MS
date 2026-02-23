from flask import Blueprint, render_template, request, redirect, url_for, flash
from app.models import Venue
from app import db

main_bp = Blueprint("main", __name__)

@main_bp.route("/")
def home():
    return redirect(url_for("main.venues"))

@main_bp.route("/venues", methods=["GET", "POST"])
def venues():
    if request.method == "POST":
        name = request.form.get("name", "").strip()

        if not name:
            flash("Venue name is required.", "error")
            return redirect(url_for("main.venues"))

        # Prevent duplicates
        exists = Venue.query.filter_by(name=name).first()
        if exists:
            flash("That venue already exists.", "error")
            return redirect(url_for("main.venues"))

        v = Venue(name=name, is_core=False, active=True)
        db.session.add(v)
        db.session.commit()

        flash("Venue added!", "success")
        return redirect(url_for("main.venues"))

    # GET request: show list of venues
    venues = Venue.query.order_by(Venue.name.asc()).all()
    return render_template("venues/list.html", venues=venues)