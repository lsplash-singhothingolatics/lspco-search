# LSPMail link for LSPSO

## 1. Delete these from the lspco-search repo

They are Node files and belong in the **LSPMAIL** repo, not here:

```
app.js        →  LSPMAIL/public/js/app.js
partner.js    →  LSPMAIL/src/routes/partner.js
server.js     →  LSPMAIL/src/server.js
```

Also delete these two, replaced by the single file in this zip:

```
lspmail.py
mail.html          (the one sitting in the repo root)
```

## 2. Upload from this zip

```
lspmail_link.py          →  repo root, beside auth.py
templates/mail.html      →  inside the templates folder
```

On GitHub, upload `mail.html` while inside the `templates` folder, or it lands
at the root again and Flask will not find it.

## 3. Edit app.py — four lines

Near the top, beside the existing auth import:

```python
from lspmail_link import mail_blueprint, lspmail_auth
```

Below `app.register_blueprint(auth_blueprint)`:

```python
app.register_blueprint(mail_blueprint)
app.register_blueprint(lspmail_auth)
```

That is the whole change. Nothing else in app.py moves.

## 4. Add the sign-in button

In `templates/login.html`, beside the Google and GitHub buttons:

```html
<a class="provider" href="{{ url_for('lspmail_auth.start') }}">Continue with LSPMail</a>
```

## 5. Optional: a Mail tab on the results page

Wherever search results render:

```html
<a href="{{ url_for('mail.search_mail') }}{% if query %}?q={{ query }}{% endif %}">Mail</a>
```

---

## Environment

**LSPSO** (Render → lspso → Environment):

```
LSPMAIL_URL            https://lspmail.onrender.com
PARTNER_SECRET         <shared value>
LSPMAIL_CLIENT_ID      lspso
LSPMAIL_CLIENT_SECRET  <shared value, different from PARTNER_SECRET>
```

**LSPMAIL** (Render → lspmail → Environment):

```
PARTNER_SECRET         <same as LSPSO>
OAUTH_CLIENT_ID        lspso
OAUTH_CLIENT_SECRET    <same as LSPMAIL_CLIENT_SECRET>
OAUTH_CLIENT_NAME      LSPSO
OAUTH_REDIRECT_URIS    https://lspso.onrender.com/auth/lspmail/callback
```

Generate each with `openssl rand -hex 32`. The two secrets are different values.

`OAUTH_REDIRECT_URIS` is matched as an exact string — `https`, no trailing slash.

## LSPMAIL also needs

`oauth.js` in `src/routes/`, the updated `server.js`, `schema.sql`, `auth.js` and
`login.js` — all supplied separately. Without `oauth.js`, the sign-in button
leads nowhere; mail search still works.

---

## Checking it worked

| URL | Expected |
|---|---|
| `lspso.onrender.com/mail` | the Mail page, or a redirect to sign in |
| `lspmail.onrender.com/oauth/.well-known/openid-configuration` | three endpoints listed |

A 404 on `/mail` means the blueprint is not registered — step 3.
A 500 on `/mail` means `mail.html` is not inside `templates/` — step 2.

Every feature here switches itself off when unconfigured. LSPSO boots either way.
