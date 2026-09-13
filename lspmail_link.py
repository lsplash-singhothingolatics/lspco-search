"""LSPMail link for LSPSO — two features in one module.

    1. /mail                     search your LSPMail from inside LSPSO
    2. /auth/lspmail             sign in with your LSPMail account

Add to app.py, just TWO lines each:

    from lspmail_link import mail_blueprint, lspmail_auth      # near the imports
    app.register_blueprint(mail_blueprint)                     # beside the others
    app.register_blueprint(lspmail_auth)

Environment (Render → LSPSO → Environment):

    LSPMAIL_URL            https://lspmail.onrender.com
    PARTNER_SECRET         same value as on LSPMail
    LSPMAIL_CLIENT_ID      lspso
    LSPMAIL_CLIENT_SECRET  same as OAUTH_CLIENT_SECRET on LSPMail

Anything left unset simply switches that feature off. Nothing crashes, and the
app still boots — a half-configured link must never take the search engine down.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

import requests
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import db
from auth import current_user, safe_next, sign_in

TIMEOUT = 10

LSPMAIL_URL = os.environ.get("LSPMAIL_URL", "").strip().rstrip("/")
PARTNER_SECRET = os.environ.get("PARTNER_SECRET", "").strip()
CLIENT_ID = os.environ.get("LSPMAIL_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("LSPMAIL_CLIENT_SECRET", "").strip()


# ===========================================================================
#  1. Mail search
# ===========================================================================

mail_blueprint = Blueprint("mail", __name__)


def search_configured() -> bool:
    return bool(LSPMAIL_URL and PARTNER_SECRET)


def _signed_call(path: str, payload: dict) -> dict:
    """Server-to-server, HMAC signed. The secret never reaches a browser."""
    raw = json.dumps(payload, separators=(",", ":"))
    ts = str(int(time.time()))
    sig = hmac.new(PARTNER_SECRET.encode(), f"{ts}.{raw}".encode(), hashlib.sha256).hexdigest()

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
    """Cheap probe for a nav badge. Never raises."""
    if not (search_configured() and user and user.get("email")):
        return {"linked": False}
    try:
        return _signed_call("/api/partner/status", {"email": user["email"]})
    except Exception:
        return {"linked": False}


@mail_blueprint.route("/mail")
def search_mail():
    user = current_user()
    if not user:
        return redirect(url_for("auth.login", next="/mail"))

    query = (request.args.get("q") or "").strip()
    state = {"linked": False, "messages": [], "unread": 0, "error": None, "query": query}

    if not search_configured():
        state["error"] = "Mail search is not switched on yet."
        return render_template("mail.html", user=user, mail_url=None, **state)

    try:
        data = _signed_call(
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

    return render_template("mail.html", user=user, mail_url=f"{LSPMAIL_URL}/app", **state)


# ===========================================================================
#  2. Sign in with LSPMail  (OAuth 2.0 + PKCE)
# ===========================================================================

lspmail_auth = Blueprint("lspmail_auth", __name__)


def oauth_enabled() -> bool:
    return bool(LSPMAIL_URL and CLIENT_ID and CLIENT_SECRET)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _callback_url() -> str:
    return url_for("lspmail_auth.callback", _external=True, _scheme="https")


@lspmail_auth.route("/auth/lspmail")
def start():
    if not oauth_enabled():
        flash("LSPMail sign-in is not configured yet.")
        return redirect(url_for("auth.login"))

    if request.args.get("next", "").startswith("/"):
        session["next"] = request.args["next"]

    state = secrets.token_urlsafe(24)
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())

    session["lspmail_state"] = state
    session["lspmail_verifier"] = verifier

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": _callback_url(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return redirect(f"{LSPMAIL_URL}/oauth/authorize?" + urlencode(params))


@lspmail_auth.route("/auth/lspmail/callback")
def callback():
    if not oauth_enabled():
        return redirect(url_for("auth.login"))

    # Pop both whatever happens, so a stale attempt can't be resumed.
    expected_state = session.pop("lspmail_state", None)
    verifier = session.pop("lspmail_verifier", None)

    if request.args.get("error"):
        flash("LSPMail sign-in was cancelled.")
        return redirect(url_for("auth.login"))

    if not expected_state or request.args.get("state") != expected_state:
        flash("That sign-in attempt expired. Try again.")
        return redirect(url_for("auth.login"))

    code = request.args.get("code")
    if not code:
        flash("LSPMail did not return a sign-in code.")
        return redirect(url_for("auth.login"))

    try:
        token_res = requests.post(
            f"{LSPMAIL_URL}/oauth/token",
            auth=(CLIENT_ID, CLIENT_SECRET),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _callback_url(),
                "code_verifier": verifier or "",
            },
            timeout=TIMEOUT,
        )
        token_res.raise_for_status()
        access_token = token_res.json().get("access_token")
        if not access_token:
            raise ValueError("no access token")

        info = requests.get(
            f"{LSPMAIL_URL}/oauth/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=TIMEOUT,
        )
        info.raise_for_status()
        profile = info.json()
    except Exception:
        flash("Could not complete LSPMail sign-in. Try again.")
        return redirect(url_for("auth.login"))

    email = (profile.get("email") or "").strip().lower()
    if not email:
        flash("That LSPMail account has no email address.")
        return redirect(url_for("auth.login"))

    user = db.upsert_user(
        email=email,
        name=profile.get("name"),
        avatar=profile.get("picture"),
        provider="lspmail",
    )
    sign_in(user)
    return redirect(safe_next())
