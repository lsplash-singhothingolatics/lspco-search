# LSPSO ← → LSPMail

Two features, both optional and independent:

1. **Your mail** — a Mail tab in LSPSO that searches the signed-in person's LSPMail.
2. **Sign in with LSPMail** — LSPMail as a sign-in provider alongside Google and GitHub.

These are add-on files. Drop them into your existing LSPSO repo; nothing is overwritten.

---

## Files

```
lspmail.py              → project root, beside auth.py
lspmail_oauth.py        → project root, beside auth.py
templates/mail.html     → templates/
```

## Register both blueprints

In `app.py`, next to where the auth blueprint is registered:

```python
from lspmail import mail as mail_blueprint
from lspmail_oauth import lspmail_auth

app.register_blueprint(mail_blueprint)
app.register_blueprint(lspmail_auth)
```

## Add the sign-in button

In `templates/login.html`, beside the Google and GitHub buttons:

```html
<a class="provider" href="{{ url_for('lspmail_auth.start') }}">
  <span>Continue with LSPMail</span>
</a>
```

If you want it to hide when unconfigured, pass the flag through from the view:

```python
from lspmail_oauth import enabled as lspmail_enabled
# in the login route: providers=providers() | {"lspmail": lspmail_enabled()}
```

## Optional: a Mail tab on the results page

In whichever template renders search results:

```html
<a href="{{ url_for('mail.search_mail') }}{% if query %}?q={{ query }}{% endif %}">Mail</a>
```

---

## Environment — LSPSO

```
LSPMAIL_URL            https://lspmail.onrender.com
PARTNER_SECRET         <same value as on LSPMail>
LSPMAIL_CLIENT_ID      lspso
LSPMAIL_CLIENT_SECRET  <same as OAUTH_CLIENT_SECRET on LSPMail>
```

## Environment — LSPMail

```
PARTNER_SECRET         <same value as on LSPSO>
OAUTH_CLIENT_ID        lspso
OAUTH_CLIENT_SECRET    <same as LSPMAIL_CLIENT_SECRET on LSPSO>
OAUTH_CLIENT_NAME      LSPSO
OAUTH_REDIRECT_URIS    https://lspso.onrender.com/auth/lspmail/callback
```

Generate each secret with `openssl rand -hex 32`. `PARTNER_SECRET` and the OAuth
secret are different values — don't reuse one for both.

`OAUTH_REDIRECT_URIS` is matched as an exact string. No trailing slash, `https`
not `http`, and it must equal the callback LSPSO actually sends.

---

## How the two differ

**Mail search** is server-to-server. LSPSO signs each request with
`PARTNER_SECRET` and names the email address it's asking about. LSPMail answers
only for an address it holds as **verified**, and returns subjects and snippets
only — never message bodies or attachments. Signatures carry a timestamp, so a
captured request stops working after five minutes.

**Sign in with LSPMail** is normal OAuth 2.0 with PKCE. The person sees a consent
screen on LSPMail, approves, and LSPSO receives a code it exchanges for a token.
Codes are single-use and expire in five minutes.

The two are independent. Mail search matches on email address, so it already
works for anyone signed into both with the same address. Sign-in makes that
guaranteed rather than coincidental.

## Checking it works

```bash
curl https://lspmail.onrender.com/oauth/.well-known/openid-configuration
```

That should list the authorize, token and userinfo endpoints. If it 404s,
`oauth.js` hasn't deployed yet.
