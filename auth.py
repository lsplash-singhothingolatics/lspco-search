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
GITLAB_CLIENT_ID = os.environ.get("GITLAB_CLIENT_ID", "").strip()
GITLAB_CLIENT_SECRET = os.environ.get("GITLAB_CLIENT_SECRET", "").strip()
GITLAB_HOST = os.environ.get("GITLAB_HOST", "https://gitlab.com").rstrip("/")
MS_CLIENT_ID = os.environ.get("MICROSOFT_CLIENT_ID", "").strip()
MS_CLIENT_SECRET = os.environ.get("MICROSOFT_CLIENT_SECRET", "").strip()
MS_TENANT = os.environ.get("MICROSOFT_TENANT", "common").strip()
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "").strip()
# Sign in with ChatGPT is partner-gated: these stay empty until OpenAI issues
# credentials, and the button stays hidden until then.
CHATGPT_CLIENT_ID = os.environ.get("CHATGPT_CLIENT_ID", "").strip()
CHATGPT_CLIENT_SECRET = os.environ.get("CHATGPT_CLIENT_SECRET", "").strip()
CHATGPT_ISSUER = os.environ.get("CHATGPT_ISSUER", "https://auth.openai.com").rstrip("/")
YAHOO_CLIENT_ID = os.environ.get("YAHOO_CLIENT_ID", "").strip()
YAHOO_CLIENT_SECRET = os.environ.get("YAHOO_CLIENT_SECRET", "").strip()
YAHOO_ISSUER = "https://api.login.yahoo.com"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()

# SMS: whichever of these is configured gets used
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_FROM = os.environ.get("TWILIO_FROM_NUMBER", "").strip()
MSG91_KEY = os.environ.get("MSG91_AUTH_KEY", "").strip()
MSG91_TEMPLATE = os.environ.get("MSG91_TEMPLATE_ID", "").strip()
MSG91_SENDER = os.environ.get("MSG91_SENDER_ID", "LSPSO").strip()
MAIL_FROM = os.environ.get("MAIL_FROM", "LSPSO <onboarding@resend.dev>").strip()

TIMEOUT = 12


def providers():
    return {
        "google": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        "github": bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET),
        "gitlab": bool(GITLAB_CLIENT_ID and GITLAB_CLIENT_SECRET),
        "microsoft": bool(MS_CLIENT_ID and MS_CLIENT_SECRET),
        "discord": bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET),
        "chatgpt": bool(CHATGPT_CLIENT_ID and CHATGPT_CLIENT_SECRET),
        "yahoo": bool(YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET),
        "email": bool(RESEND_API_KEY),
        "phone": bool((TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM)
                      or (MSG91_KEY and MSG91_TEMPLATE)),
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


# ------------------------------------------------------------------ gitlab

@auth.route("/auth/gitlab")
def gitlab_start():
    if not providers()["gitlab"]:
        flash("GitLab sign-in is not configured yet.")
        return redirect(url_for("auth.login"))
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    params = {
        "client_id": GITLAB_CLIENT_ID,
        "redirect_uri": url_for("auth.gitlab_callback", _external=True, _scheme="https"),
        "response_type": "code",
        "scope": "read_user",
        "state": state,
    }
    return redirect(f"{GITLAB_HOST}/oauth/authorize?" + urlencode(params))


@auth.route("/auth/gitlab/callback")
def gitlab_callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        flash("Sign-in expired. Please try again.")
        return redirect(url_for("auth.login"))
    code = request.args.get("code")
    if not code:
        flash("GitLab sign-in was cancelled.")
        return redirect(url_for("auth.login"))

    try:
        token = requests.post(
            f"{GITLAB_HOST}/oauth/token",
            data={
                "client_id": GITLAB_CLIENT_ID,
                "client_secret": GITLAB_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": url_for("auth.gitlab_callback", _external=True, _scheme="https"),
            },
            timeout=TIMEOUT,
        ).json()
        profile = requests.get(
            f"{GITLAB_HOST}/api/v4/user",
            headers={"Authorization": "Bearer " + token["access_token"]},
            timeout=TIMEOUT,
        ).json()
    except Exception:
        flash("Could not reach GitLab. Please try again.")
        return redirect(url_for("auth.login"))

    if not profile.get("email"):
        flash("GitLab did not share an email address.")
        return redirect(url_for("auth.login"))

    sign_in(db.upsert_user(
        profile["email"],
        profile.get("name") or profile.get("username"),
        profile.get("avatar_url"),
        "gitlab",
    ))
    return redirect(safe_next())


# ------------------------------------------------------------------ microsoft

MS_BASE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0"


@auth.route("/auth/microsoft")
def microsoft_start():
    if not providers()["microsoft"]:
        flash("Microsoft sign-in is not configured yet.")
        return redirect(url_for("auth.login"))
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    params = {
        "client_id": MS_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": url_for("auth.microsoft_callback", _external=True, _scheme="https"),
        "response_mode": "query",
        "scope": "openid email profile User.Read",
        "state": state,
        "prompt": "select_account",
    }
    return redirect(MS_BASE.format(tenant=MS_TENANT) + "/authorize?" + urlencode(params))


@auth.route("/auth/microsoft/callback")
def microsoft_callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        flash("Sign-in expired. Please try again.")
        return redirect(url_for("auth.login"))
    code = request.args.get("code")
    if not code:
        flash("Microsoft sign-in was cancelled.")
        return redirect(url_for("auth.login"))

    try:
        token = requests.post(
            MS_BASE.format(tenant=MS_TENANT) + "/token",
            data={
                "client_id": MS_CLIENT_ID,
                "client_secret": MS_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": url_for("auth.microsoft_callback", _external=True, _scheme="https"),
                "scope": "openid email profile User.Read",
            },
            timeout=TIMEOUT,
        ).json()
        profile = requests.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": "Bearer " + token["access_token"]},
            timeout=TIMEOUT,
        ).json()
    except Exception:
        flash("Could not reach Microsoft. Please try again.")
        return redirect(url_for("auth.login"))

    # work accounts use mail, personal accounts use userPrincipalName
    email = profile.get("mail") or profile.get("userPrincipalName")
    if not email or "@" not in email:
        flash("Microsoft did not share an email address.")
        return redirect(url_for("auth.login"))

    sign_in(db.upsert_user(
        email, profile.get("displayName"), None, "microsoft"
    ))
    return redirect(safe_next())


# ------------------------------------------------------------------ discord

@auth.route("/auth/discord")
def discord_start():
    if not providers()["discord"]:
        flash("Discord sign-in is not configured yet.")
        return redirect(url_for("auth.login"))
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": url_for("auth.discord_callback", _external=True, _scheme="https"),
        "response_type": "code",
        "scope": "identify email",
        "state": state,
        "prompt": "consent",
    }
    return redirect("https://discord.com/oauth2/authorize?" + urlencode(params))


@auth.route("/auth/discord/callback")
def discord_callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        flash("Sign-in expired. Please try again.")
        return redirect(url_for("auth.login"))
    code = request.args.get("code")
    if not code:
        flash("Discord sign-in was cancelled.")
        return redirect(url_for("auth.login"))

    try:
        token = requests.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": url_for("auth.discord_callback", _external=True, _scheme="https"),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT,
        ).json()
        profile = requests.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": "Bearer " + token["access_token"]},
            timeout=TIMEOUT,
        ).json()
    except Exception:
        flash("Could not reach Discord. Please try again.")
        return redirect(url_for("auth.login"))

    if not profile.get("email"):
        flash("Discord did not share a verified email address.")
        return redirect(url_for("auth.login"))

    avatar = None
    if profile.get("avatar"):
        avatar = (
            f"https://cdn.discordapp.com/avatars/{profile['id']}/{profile['avatar']}.png"
        )

    sign_in(db.upsert_user(
        profile["email"],
        profile.get("global_name") or profile.get("username"),
        avatar,
        "discord",
    ))
    return redirect(safe_next())


# ------------------------------------------------------------------ chatgpt
#
# "Sign in with ChatGPT" is an OpenID Connect provider, but OpenAI grants
# credentials to approved partners rather than through a public console.
# Endpoints are read from the issuer's discovery document so this keeps
# working if OpenAI changes them.

_oidc_cache = {}


def oidc_config(issuer):
    if issuer not in _oidc_cache:
        r = requests.get(issuer + "/.well-known/openid-configuration", timeout=TIMEOUT)
        r.raise_for_status()
        _oidc_cache[issuer] = r.json()
    return _oidc_cache[issuer]


@auth.route("/auth/chatgpt")
def chatgpt_start():
    if not providers()["chatgpt"]:
        flash("Sign in with ChatGPT is not enabled for this site yet.")
        return redirect(url_for("auth.login"))
    try:
        conf = oidc_config(CHATGPT_ISSUER)
    except Exception:
        flash("Could not reach OpenAI. Please try again.")
        return redirect(url_for("auth.login"))

    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    params = {
        "client_id": CHATGPT_CLIENT_ID,
        "redirect_uri": url_for("auth.chatgpt_callback", _external=True, _scheme="https"),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
    }
    return redirect(conf["authorization_endpoint"] + "?" + urlencode(params))


@auth.route("/auth/chatgpt/callback")
def chatgpt_callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        flash("Sign-in expired. Please try again.")
        return redirect(url_for("auth.login"))
    code = request.args.get("code")
    if not code:
        flash("ChatGPT sign-in was cancelled.")
        return redirect(url_for("auth.login"))

    try:
        conf = oidc_config(CHATGPT_ISSUER)
        token = requests.post(
            conf["token_endpoint"],
            data={
                "client_id": CHATGPT_CLIENT_ID,
                "client_secret": CHATGPT_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": url_for("auth.chatgpt_callback", _external=True, _scheme="https"),
            },
            timeout=TIMEOUT,
        ).json()
        profile = requests.get(
            conf["userinfo_endpoint"],
            headers={"Authorization": "Bearer " + token["access_token"]},
            timeout=TIMEOUT,
        ).json()
    except Exception:
        flash("Could not complete ChatGPT sign-in. Please try again.")
        return redirect(url_for("auth.login"))

    if not profile.get("email"):
        flash("ChatGPT did not share an email address.")
        return redirect(url_for("auth.login"))

    sign_in(db.upsert_user(
        profile["email"], profile.get("name"), profile.get("picture"), "chatgpt"
    ))
    return redirect(safe_next())


# ------------------------------------------------------------------ yahoo
#
# Yahoo is an OpenID Connect provider. Its token endpoint expects the client
# credentials as HTTP Basic auth rather than form fields.

@auth.route("/auth/yahoo")
def yahoo_start():
    if not providers()["yahoo"]:
        flash("Yahoo sign-in is not configured yet.")
        return redirect(url_for("auth.login"))
    try:
        conf = oidc_config(YAHOO_ISSUER)
        authorize = conf["authorization_endpoint"]
    except Exception:
        authorize = "https://api.login.yahoo.com/oauth2/request_auth"

    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    params = {
        "client_id": YAHOO_CLIENT_ID,
        "redirect_uri": url_for("auth.yahoo_callback", _external=True, _scheme="https"),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
    }
    return redirect(authorize + "?" + urlencode(params))


@auth.route("/auth/yahoo/callback")
def yahoo_callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        flash("Sign-in expired. Please try again.")
        return redirect(url_for("auth.login"))
    code = request.args.get("code")
    if not code:
        flash("Yahoo sign-in was cancelled.")
        return redirect(url_for("auth.login"))

    try:
        try:
            conf = oidc_config(YAHOO_ISSUER)
            token_url = conf["token_endpoint"]
            userinfo_url = conf["userinfo_endpoint"]
        except Exception:
            token_url = "https://api.login.yahoo.com/oauth2/get_token"
            userinfo_url = "https://api.login.yahoo.com/openid/v1/userinfo"

        token = requests.post(
            token_url,
            auth=(YAHOO_CLIENT_ID, YAHOO_CLIENT_SECRET),   # Yahoo wants Basic auth
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": url_for("auth.yahoo_callback", _external=True, _scheme="https"),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT,
        ).json()

        profile = requests.get(
            userinfo_url,
            headers={"Authorization": "Bearer " + token["access_token"]},
            timeout=TIMEOUT,
        ).json()
    except Exception:
        flash("Could not complete Yahoo sign-in. Please try again.")
        return redirect(url_for("auth.login"))

    email = profile.get("email")
    if not email:
        flash("Yahoo did not share an email address.")
        return redirect(url_for("auth.login"))

    picture = profile.get("picture")
    if isinstance(picture, dict):
        picture = picture.get("data", [{}])[0].get("image_url")

    sign_in(db.upsert_user(
        email,
        profile.get("name") or profile.get("given_name"),
        picture if isinstance(picture, str) else None,
        "yahoo",
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


# ------------------------------------------------------------------ sms otp

def send_code_sms(phone, code):
    """Sends via MSG91 if configured (cheaper in India), otherwise Twilio."""
    text = f"{code} is your LSPSO sign-in code. It expires in 10 minutes."

    if MSG91_KEY and MSG91_TEMPLATE:
        r = requests.post(
            "https://control.msg91.com/api/v5/flow/",
            headers={"authkey": MSG91_KEY, "Content-Type": "application/json"},
            json={
                "template_id": MSG91_TEMPLATE,
                "sender": MSG91_SENDER,
                "short_url": "0",
                "recipients": [{"mobiles": phone.lstrip("+"), "otp": code}],
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        if str(r.json().get("type", "")).lower() == "error":
            raise RuntimeError(r.json().get("message", "MSG91 rejected the request"))
        return

    if TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM:
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
            auth=(TWILIO_SID, TWILIO_TOKEN),
            data={"To": phone, "From": TWILIO_FROM, "Body": text},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return

    raise RuntimeError("No SMS provider configured")


@auth.route("/auth/phone", methods=["POST"])
def phone_start():
    if not providers()["phone"]:
        flash("Phone sign-in is not configured yet.")
        return redirect(url_for("auth.login"))

    phone = db.normalise_phone(request.form.get("phone"))
    if not phone or len(phone) < 8:
        flash("Enter your number with the country code, for example +91 98765 43210.")
        return redirect(url_for("auth.login"))

    code, error = db.create_code(phone)
    if error:
        flash(error)
        return redirect(url_for("auth.verify"))

    try:
        send_code_sms(phone, code)
    except Exception:
        flash("The text message could not be sent. Check the number and try again.")
        return redirect(url_for("auth.login"))

    session["pending_id"] = phone
    session["pending_channel"] = "phone"
    session.pop("pending_email", None)
    return redirect(url_for("auth.verify"))


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

    session["pending_id"] = email
    session["pending_channel"] = "email"
    return redirect(url_for("auth.verify"))


@auth.route("/auth/verify", methods=["GET", "POST"])
def verify():
    identifier = session.get("pending_id") or request.args.get("email", "")
    channel = session.get("pending_channel", "email")
    if not identifier:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        ok, error = db.verify_code(identifier, request.form.get("code"))
        if not ok:
            flash(error)
            return redirect(url_for("auth.verify"))
        session.pop("pending_id", None)
        session.pop("pending_channel", None)
        if channel == "phone":
            sign_in(db.upsert_phone_user(identifier))
        else:
            sign_in(db.upsert_user(identifier, provider="email"))
        return redirect(safe_next())

    return render_template("verify.html", email=identifier, channel=channel)


@auth.route("/auth/resend", methods=["POST"])
def resend():
    identifier = session.get("pending_id")
    channel = session.get("pending_channel", "email")
    if not identifier:
        return redirect(url_for("auth.login"))
    code, error = db.create_code(identifier)
    if error:
        flash(error)
    else:
        try:
            if channel == "phone":
                send_code_sms(identifier, code)
            else:
                send_code_email(identifier, code)
            flash("A new code is on its way.")
        except Exception:
            flash("The code could not be sent. Try again shortly.")
    return redirect(url_for("auth.verify"))
