"""Sign in with LSPMail — OAuth 2.0 client with PKCE.

Wiring (in app.py, next to the other blueprint registration):

    from lspmail_oauth import lspmail_auth
    app.register_blueprint(lspmail_auth)

Environment:

    LSPMAIL_URL            https://lspmail.onrender.com
    LSPMAIL_CLIENT_ID      lspso
    LSPMAIL_CLIENT_SECRET  the value set as OAUTH_CLIENT_SECRET on LSPMail

Register this callback on LSPMail's OAUTH_REDIRECT_URIS:

    https://lspso.onrender.com/auth/lspmail/callback
"""

import base64
import hashlib
import os
import secrets

import requests
from flask import Blueprint, flash, redirect, request, session, url_for

import db
from auth import sign_in, safe_next

lspmail_auth = Blueprint("lspmail_auth", __name__)

LSPMAIL_URL = os.environ.get("LSPMAIL_URL", "").strip().rstrip("/")
CLIENT_ID = os.environ.get("LSPMAIL_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("LSPMAIL_CLIENT_SECRET", "").strip()
TIMEOUT = 12


def enabled() -> bool:
    return bool(LSPMAIL_URL and CLIENT_ID and CLIENT_SECRET)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _callback_url() -> str:
    return url_for("lspmail_auth.callback", _external=True, _scheme="https")


@lspmail_auth.route("/auth/lspmail")
def start():
    if not enabled():
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
    return redirect(f"{LSPMAIL_URL}/oauth/authorize?" + requests.compat.urlencode(params))


@lspmail_auth.route("/auth/lspmail/callback")
def callback():
    if not enabled():
        return redirect(url_for("auth.login"))

    # Pop both regardless of outcome so a stale attempt can't be resumed.
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
