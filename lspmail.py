"""Your mail — searches the signed-in person's LSPMail from inside LSPSO.

Wiring (in app.py):

    from lspmail import mail as mail_blueprint, mail_status
    app.register_blueprint(mail_blueprint)

Environment:

    LSPMAIL_URL       https://lspmail.onrender.com
    PARTNER_SECRET    the same value set on LSPMail

LSPMail only answers for an address it holds as verified, so a person who has
no LSPMail account simply sees an invitation instead of results.
"""

import hashlib
import hmac
import json
import os
import time

import requests
from flask import Blueprint, redirect, render_template, request, url_for

from auth import current_user

mail = Blueprint("mail", __name__)

LSPMAIL_URL = os.environ.get("LSPMAIL_URL", "").strip().rstrip("/")
PARTNER_SECRET = os.environ.get("PARTNER_SECRET", "").strip()
TIMEOUT = 10


def configured() -> bool:
    return bool(LSPMAIL_URL and PARTNER_SECRET)


def _call(path: str, payload: dict) -> dict:
    """Signed server-to-server call. The secret never reaches the browser."""
    raw = json.dumps(payload, separators=(",", ":"))
    ts = str(int(time.time()))
    sig = hmac.new(
        PARTNER_SECRET.encode(),
        f"{ts}.{raw}".encode(),
        hashlib.sha256,
    ).hexdigest()

    r = requests.post(
        f"{LSPMAIL_URL}{path}",
        data=raw,
        headers={
            "Content-Type": "application/json",
            "X-LSP-Timestamp": ts,
            "X-LSP-Signature": sig,
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def mail_status(user) -> dict:
    """Cheap probe for the nav bar. Never raises — a dead mail server must not
    take the search engine down with it."""
    if not (configured() and user and user.get("email")):
        return {"linked": False}
    try:
        return _call("/api/partner/status", {"email": user["email"]})
    except Exception:
        return {"linked": False}


@mail.route("/mail")
def search_mail():
    user = current_user()
    if not user:
        return redirect(url_for("auth.login", next="/mail"))

    query = (request.args.get("q") or "").strip()
    state = {"linked": False, "messages": [], "unread": 0, "error": None, "query": query}

    if not configured():
        state["error"] = "Mail search is not switched on yet."
        return render_template("mail.html", user=user, **state)

    try:
        data = _call(
            "/api/partner/search",
            {"email": user["email"], "query": query, "limit": 25},
        )
        state.update(
            linked=data.get("linked", False),
            messages=data.get("messages", []),
            unread=data.get("unread", 0),
        )
    except requests.Timeout:
        state["error"] = "LSPMail did not answer in time. Try again."
    except Exception:
        state["error"] = "Could not reach LSPMail just now."

    return render_template(
        "mail.html",
        user=user,
        mail_url=f"{LSPMAIL_URL}/app",
        **state,
    )
