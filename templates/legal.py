"""Terms of Service and Privacy Policy pages.

These are plain-language starting documents, not legal advice. Have a lawyer
review them before relying on them for anything that matters.
"""

import os
from datetime import date

from flask import Blueprint, render_template

legal = Blueprint("legal", __name__)

SITE_NAME = os.environ.get("SITE_NAME", "LSPSO")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "").strip()
UPDATED = os.environ.get("LEGAL_UPDATED", date.today().strftime("%d %B %Y"))


@legal.route("/terms")
def terms():
    return render_template(
        "legal.html",
        page="terms",
        site=SITE_NAME,
        contact=CONTACT_EMAIL,
        updated=UPDATED,
    )


@legal.route("/privacy")
def privacy():
    return render_template(
        "legal.html",
        page="privacy",
        site=SITE_NAME,
        contact=CONTACT_EMAIL,
        updated=UPDATED,
    )
