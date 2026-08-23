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

## Filters

The results page has a filter bar that works on every provider:

| Filter | Options |
|---|---|
| Time | Any time, past 24 hours, week, month, year |
| File type | Any, PDF, DOC, PPT, XLS |
| Region | Worldwide, India, US, UK, Australia, Canada |
| Safe search | On by default, can be turned off |

Filters stay applied when you turn the page or run a new search, and **Clear filters** resets them. To add another region, edit the `REGIONS` dictionary near the top of `app.py`.

## Notes

- Free Render services sleep after 15 minutes of no traffic. The first visit after that takes ~50 seconds to wake up.
- `/healthz` is a status endpoint used by Render to check the app is alive.
