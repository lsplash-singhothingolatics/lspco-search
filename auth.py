"""Sign in with Google, GitHub, or a one-time code sent by email (Resend)."""

import os
import secrets
from urllib.parse import urlencode

import requests
from flask import (
    Blueprint, redirect, render_template, request, session, url_for, flash,
)

import db

auth = Blueprint("auth", __name__)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "").strip()
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "").strip()
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
MAIL_FROM = os.environ.get("MAIL_FROM", "LSPSO <onboarding@resend.dev>").strip()

TIMEOUT = 12


def providers():
    return {
        "google": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        "github": bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET),
        "email": bool(RESEND_API_KEY),
    }


def current_user():
    uid = session.get("uid")
    return db.get_user(uid) if uid else None


def sign_in(user):
    session.clear()
    session["uid"] = user["id"]
    session.permanent = True


def safe_next():
    nxt = session.pop("next", None) or request.args.get("next")
    return nxt if nxt and nxt.startswith("/") else url_for("home")


# ------------------------------------------------------------------ pages

@auth.route("/login")
def login():
    if current_user():
        return redirect(url_for("home"))
    if request.args.get("next", "").startswith("/"):
        session["next"] = request.args["next"]
    return render_template("login.html", providers=providers())


@auth.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))


@auth.route("/account")
def account():
    user = current_user()
    if not user:
        return redirect(url_for("auth.login", next="/account"))
    return render_template("account.html", user=user)


# ------------------------------------------------------------------ google

@auth.route("/auth/google")
def google_start():
    if not providers()["google"]:
        flash("Google sign-in is not configured yet.")
        return redirect(url_for("auth.login"))
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": url_for("auth.google_callback", _external=True, _scheme="https"),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@auth.route("/auth/google/callback")
def google_callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        flash("Sign-in expired. Please try again.")
        return redirect(url_for("auth.login"))
    code = request.args.get("code")
    if not code:
        flash("Google sign-in was cancelled.")
        return redirect(url_for("auth.login"))

    try:
        token = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": url_for("auth.google_callback", _external=True, _scheme="https"),
                "grant_type": "authorization_code",
            },
            timeout=TIMEOUT,
        ).json()
        profile = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": "Bearer " + token["access_token"]},
            timeout=TIMEOUT,
        ).json()
    except Exception:
        flash("Could not reach Google. Please try again.")
        return redirect(url_for("auth.login"))

    if not profile.get("email"):
        flash("Google did not share an email address.")
        return redirect(url_for("auth.login"))

    sign_in(db.upsert_user(
        profile["email"], profile.get("name"), profile.get("picture"), "google"
    ))
    return redirect(safe_next())


# ------------------------------------------------------------------ github

@auth.route("/auth/github")
def github_start():
    if not providers()["github"]:
        flash("GitHub sign-in is not configured yet.")
        return redirect(url_for("auth.login"))
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    params = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": url_for("auth.github_callback", _external=True, _scheme="https"),
        "scope": "read:user user:email",
        "state": state,
    }
    return redirect("https://github.com/login/oauth/authorize?" + urlencode(params))


@auth.route("/auth/github/callback")
def github_callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        flash("Sign-in expired. Please try again.")
        return redirect(url_for("auth.login"))
    code = request.args.get("code")
    if not code:
        flash("GitHub sign-in was cancelled.")
        return redirect(url_for("auth.login"))

    try:
        token = requests.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": url_for("auth.github_callback", _external=True, _scheme="https"),
            },
            timeout=TIMEOUT,
        ).json()
        head = {
            "Authorization": "Bearer " + token["access_token"],
            "Accept": "application/vnd.github+json",
        }
        profile = requests.get("https://api.github.com/user", headers=head, timeout=TIMEOUT).json()
        email = profile.get("email")
        if not email:
            mails = requests.get(
                "https://api.github.com/user/emails", headers=head, timeout=TIMEOUT
            ).json()
            primary = [m for m in mails if m.get("primary") and m.get("verified")]
            email = (primary or mails or [{}])[0].get("email")
    except Exception:
        flash("Could not reach GitHub. Please try again.")
        return redirect(url_for("auth.login"))

    if not email:
        flash("GitHub did not share a verified email address.")
        return redirect(url_for("auth.login"))

    sign_in(db.upsert_user(
        email, profile.get("name") or profile.get("login"),
        profile.get("avatar_url"), "github",
    ))
    return redirect(safe_next())


# ------------------------------------------------------------------ email otp

def send_code_email(email, code):
    r = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": "Bearer " + RESEND_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "from": MAIL_FROM,
            "to": [email],
            "subject": f"{code} is your LSPSO sign-in code",
            "html": f"""
              <div style="font-family:-apple-system,Segoe UI,sans-serif;background:#f6f5f2;padding:40px 20px">
                <div style="max-width:420px;margin:0 auto;background:#fff;border:1px solid #e6e4de;border-radius:14px;padding:36px 32px;text-align:center">
                  <div style="font-size:26px;font-weight:600;letter-spacing:-0.03em;color:#17181b">LSPSO<span style="color:#24483c">.</span></div>
                  <p style="color:#6b6d73;font-size:14px;margin:20px 0 24px">Use this code to sign in. It expires in 10 minutes.</p>
                  <div style="font-size:34px;letter-spacing:0.34em;font-weight:600;color:#17181b;background:#f6f5f2;border-radius:10px;padding:18px 0;text-indent:0.34em">{code}</div>
                  <p style="color:#9a9ca1;font-size:12px;margin-top:24px">If you didn't request this, you can ignore this email.</p>
                </div>
              </div>
            """,
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()


@auth.route("/auth/email", methods=["POST"])
def email_start():
    email = (request.form.get("email") or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        flash("Enter a valid email address.")
        return redirect(url_for("auth.login"))
    if not RESEND_API_KEY:
        flash("Email sign-in is not configured yet.")
        return redirect(url_for("auth.login"))

    code, error = db.create_code(email)
    if error:
        flash(error)
        return redirect(url_for("auth.verify", email=email))

    try:
        send_code_email(email, code)
    except Exception:
        flash("The code could not be sent. Check the address and try again.")
        return redirect(url_for("auth.login"))

    session["pending_email"] = email
    return redirect(url_for("auth.verify"))


@auth.route("/auth/verify", methods=["GET", "POST"])
def verify():
    email = session.get("pending_email") or request.args.get("email", "")
    if not email:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        ok, error = db.verify_code(email, request.form.get("code"))
        if not ok:
            flash(error)
            return redirect(url_for("auth.verify"))
        session.pop("pending_email", None)
        sign_in(db.upsert_user(email, provider="email"))
        return redirect(safe_next())

    return render_template("verify.html", email=email)


@auth.route("/auth/resend", methods=["POST"])
def resend():
    email = session.get("pending_email")
    if not email:
        return redirect(url_for("auth.login"))
    code, error = db.create_code(email)
    if error:
        flash(error)
    else:
        try:
            send_code_email(email, code)
            flash("A new code is on its way.")
        except Exception:
            flash("The code could not be sent. Try again shortly.")
    return redirect(url_for("auth.verify"))
