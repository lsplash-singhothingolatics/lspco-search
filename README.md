# LSPSO

A small search engine you can host yourself. Search the web, ask a question, or search inside one website.

## Files

| File | What it does |
|---|---|
| `app.py` | Flask server + search logic |
| `templates/index.html` | Home page |
| `templates/results.html` | Results page |
| `static/style.css` | All styling |
| `requirements.txt` | Python packages |
| `render.yaml` | Render deployment config |
| `Procfile` | Start command |

## Run it on your own computer first

```bash
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000

## Deploy on Render

1. Put these files in a GitHub repository (all files at the top level, not inside a folder).
2. Go to **render.com → New → Web Service** and connect that repository.
3. Fill in the settings:
   - **Language:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT`
   - **Instance Type:** Free
4. Click **Create Web Service**. In 2–3 minutes your site is live at `https://lspso.onrender.com`.

Because `render.yaml` is included, you can also use **New → Blueprint** and Render fills in every setting for you.

## Making results more reliable (recommended)

With no configuration, LSPSO reads DuckDuckGo. That is free but shared cloud servers get rate-limited, so some searches return an error.

Add **one** of these in Render: your service -> **Environment** -> **Add Environment Variable**. LSPSO picks up whichever is present, no code change needed.

| Provider | Free tier | Variables to add |
|---|---|---|
| Google Programmable Search | 100 searches/day, no card | `GOOGLE_API_KEY` and `GOOGLE_CX` |
| Tavily | 1,000/month, no card | `TAVILY_API_KEY` |
| Brave Search API | 2,000/month, card required | `BRAVE_API_KEY` |
| Serper | 2,500 total | `SERPER_API_KEY` |

### Google Programmable Search setup

1. Go to programmablesearchengine.google.com -> **Add**. Name it LSPSO, choose **Search the entire web**, create it.
2. Open the engine, copy the **Search engine ID** -> that is your `GOOGLE_CX`.
3. Go to console.cloud.google.com -> create a project -> **APIs & Services** -> **Library** -> enable **Custom Search API**.
4. **Credentials** -> **Create credentials** -> **API key** -> copy it -> that is your `GOOGLE_API_KEY`.
5. Add both variables in Render and save.

## In-site viewer

Clicking a result opens the page **inside LSPSO** at `/open?url=...` rather than sending the visitor to another site. The page is fetched server-side, scripts and tracking are stripped, and the content is restyled to match LSPSO. Links inside the page keep flowing through the viewer.

A bar across the top shows which site is being read, with **Back** and **Original** buttons. Anything that isn't HTML — PDFs, images, downloads — redirects to the source instead.

Limits worth knowing: sites built entirely with JavaScript (single-page apps, most social networks) render bare, and some sites block server-side readers. The **Original** button always works, so nothing becomes a dead end.

Internal and private addresses are refused, so the viewer can't be used to reach anything on the host network.

## Filters

The results page has a filter bar that works on every provider:

| Filter | Options |
|---|---|
| Time | Any time, past 24 hours, week, month, year |
| File type | Any, PDF, DOC, PPT, XLS |
| Region | Worldwide, India, US, UK, Australia, Canada |
| Safe search | On by default, can be turned off |

Filters stay applied when you turn the page or run a new search, and **Clear filters** resets them. To add another region, edit the `REGIONS` dictionary near the top of `app.py`.

## Accounts

Sign in with Google, GitHub, or a 6-digit code emailed by Resend. Add these in Render -> **Environment**:

| Key | Where to get it |
|---|---|
| `SECRET_KEY` | Any long random string. Signs the session cookie — required. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | console.cloud.google.com -> Credentials -> OAuth client ID (Web application) |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | github.com/settings/developers -> New OAuth App |
| `GITLAB_CLIENT_ID` / `GITLAB_CLIENT_SECRET` | gitlab.com -> Preferences -> Applications |
| `GITLAB_HOST` | Optional. Only for self-hosted GitLab. Defaults to `https://gitlab.com`. |
| `MICROSOFT_CLIENT_ID` / `MICROSOFT_CLIENT_SECRET` | portal.azure.com -> App registrations -> New registration |
| `MICROSOFT_TENANT` | Optional. Defaults to `common` (any Microsoft account). Use your tenant ID to restrict to one organisation. |
| `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` | discord.com/developers/applications -> New Application -> OAuth2 |
| `CHATGPT_CLIENT_ID` / `CHATGPT_CLIENT_SECRET` | Partner-only. OpenAI issues these on request; there is no public console. Button stays hidden until both are set. |
| `YAHOO_CLIENT_ID` / `YAHOO_CLIENT_SECRET` | developer.yahoo.com/apps/create -> Web Application, OpenID Connect Permissions: Profiles + Email |
| `RESEND_API_KEY` | resend.com -> API Keys |
| `MAIL_FROM` | Optional. Defaults to `LSPSO <onboarding@resend.dev>`. |

Whichever keys you add, those buttons appear on the sign-in page. The others stay hidden.

### Redirect URLs (must match exactly)

- Google -> Authorised redirect URI: `https://YOUR-APP.onrender.com/auth/google/callback`
- GitHub -> Authorization callback URL: `https://YOUR-APP.onrender.com/auth/github/callback`
- GitLab -> Redirect URI: `https://YOUR-APP.onrender.com/auth/gitlab/callback` (scope: `read_user`)
- Microsoft -> Redirect URI (platform: Web): `https://YOUR-APP.onrender.com/auth/microsoft/callback`
- Discord -> OAuth2 Redirect: `https://YOUR-APP.onrender.com/auth/discord/callback` (scopes: `identify`, `email`)
- ChatGPT -> Redirect URI: `https://YOUR-APP.onrender.com/auth/chatgpt/callback`
- Yahoo -> Redirect URI: `https://YOUR-APP.onrender.com/auth/yahoo/callback` (must be HTTPS; Yahoo rejects localhost)

### Where accounts are stored

By default a SQLite file, which **Render's free plan erases on every deploy**. For accounts that survive, create a Render Postgres database, add its Internal Database URL as `DATABASE_URL`, and add this line to `requirements.txt`:

```
psycopg[binary]==3.2.1
```

The app detects the driver and switches over automatically. Leave that line out while you are on SQLite — the older `psycopg2-binary` has no prebuilt wheel for Python 3.13+ and will fail the build.

### Security built in

Codes are 6 digits, hashed before storage, expire in 10 minutes, allow 5 attempts, and can only be requested once a minute per address. OAuth uses a `state` token to block cross-site request forgery.

## When something breaks

Open **`https://YOUR-APP.onrender.com/status`**. It reports, without revealing any secrets:

- which templates were actually found (catches missing uploads)
- whether accounts loaded, and the exact error if not
- which sign-in providers and search engine are configured
- the Python version in use

Search keeps working even if accounts fail to load — the sign-in button simply disappears.

To see a full traceback in the browser, set `SHOW_ERRORS=1` in Render. Remove it once fixed.

## Terms and Privacy

`/terms` and `/privacy` are served from `legal.py` + `templates/legal.html`. The terms include an acceptable-use section banning violent content, extremism, weapons instructions, child sexual abuse material, harassment and hate, self-harm content, illegal activity, and non-consensual intimate imagery.

Optional environment variables:

| Key | Effect |
|---|---|
| `CONTACT_EMAIL` | Shown as the contact address on both pages. Recommended. |
| `SITE_NAME` | Defaults to `LSPSO`. |
| `LEGAL_UPDATED` | Fixes the "last updated" date. Defaults to today. |

These are a plain-language starting point, not legal advice. Have a lawyer review them before relying on them.

## Notes

- Free Render services sleep after 15 minutes of no traffic. The first visit after that takes ~50 seconds to wake up.
- `/healthz` is a status endpoint used by Render to check the app is alive.
