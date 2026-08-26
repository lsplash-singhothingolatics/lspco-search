"""
LSPSO — a small search engine you can host yourself.

Search backend order (first one configured wins):
  1. SERPER_API_KEY                  -> Google results via serper.dev
  2. GOOGLE_API_KEY + GOOGLE_CX      -> Google Programmable Search (100/day free)
  3. TAVILY_API_KEY                  -> Tavily search
  4. BRAVE_API_KEY                   -> Brave Search API
  5. nothing set                     -> DuckDuckGo HTML (free, may rate-limit)
"""

import os
import re
from datetime import timedelta
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, session

AUTH_ERROR = None
try:
    import db
    from auth import auth as auth_blueprint, current_user
    AUTH_ENABLED = True
except Exception as _exc:                      # missing file, missing package, bad DB url
    AUTH_ENABLED = False
    AUTH_ERROR = f"{type(_exc).__name__}: {_exc}"
    current_user = lambda: None

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
try:
    from legal import legal as legal_blueprint
    app_has_legal = True
except Exception as _lexc:
    app_has_legal = False

try:
    from viewer import viewer as viewer_blueprint, proxy_link
    app.register_blueprint(viewer_blueprint)
    VIEWER_ENABLED = True
except Exception as _vexc:
    VIEWER_ENABLED = False
    proxy_link = lambda u: u
    app.logger.error("In-site viewer unavailable: %s", _vexc)


@app.template_filter("via_lspso")
def via_lspso(url):
    """Route a result link through the in-site viewer."""
    return proxy_link(url) if VIEWER_ENABLED else url


if app_has_legal:
    app.register_blueprint(legal_blueprint)

if AUTH_ENABLED:
    app.register_blueprint(auth_blueprint)
    try:
        db.init_db()
    except Exception as exc:                   # never let a DB hiccup kill boot
        AUTH_ENABLED = False
        AUTH_ERROR = f"Database init failed: {exc}"
        app.logger.error(AUTH_ERROR)
else:
    app.logger.error("Accounts disabled -> %s", AUTH_ERROR)


@app.context_processor
def inject_user():
    """Makes `user` available inside every template."""
    try:
        return {"user": current_user(), "auth_on": AUTH_ENABLED}
    except Exception:
        return {"user": None, "auth_on": False}

SERPER_KEY = os.environ.get("SERPER_API_KEY", "").strip()
GOOGLE_KEY = os.environ.get("GOOGLE_API_KEY", "").strip()
GOOGLE_CX = os.environ.get("GOOGLE_CX", "").strip()
TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "").strip()
BRAVE_KEY = os.environ.get("BRAVE_API_KEY", "").strip()
TIMEOUT = 12
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
RESULTS_PER_PAGE = 20


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

NO_ENGINE_MESSAGE = (
    "The free search source is blocking this server, which is common on shared "
    "cloud hosting. Adding a search API key fixes it permanently \u2014 see /status."
)

TIME_MAP = {
    "day": {"google": "d1", "ddg": "d", "serper": "qdr:d", "brave": "pd"},
    "week": {"google": "w1", "ddg": "w", "serper": "qdr:w", "brave": "pw"},
    "month": {"google": "m1", "ddg": "m", "serper": "qdr:m", "brave": "pm"},
    "year": {"google": "y1", "ddg": "y", "serper": "qdr:y", "brave": "py"},
}

FILE_TYPES = ["pdf", "doc", "ppt", "xls"]

REGIONS = {
    "": "Worldwide",
    "in": "India",
    "us": "United States",
    "gb": "United Kingdom",
    "au": "Australia",
    "ca": "Canada",
}


def read_filters(args):
    """Pull the filter values out of the query string, ignoring anything unknown."""
    time = args.get("time", "")
    ftype = args.get("type", "")
    region = args.get("region", "")
    return {
        "time": time if time in TIME_MAP else "",
        "type": ftype if ftype in FILE_TYPES else "",
        "region": region if region in REGIONS else "",
        "safe": "off" if args.get("safe") == "off" else "on",
    }


def looks_like_site(q: str) -> str | None:
    """If the user typed a website (google.com, https://x.org/page), return the URL."""
    q = q.strip()
    if " " in q:
        return None
    if q.startswith(("http://", "https://")):
        return q
    if re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", q) and "." in q:
        return "https://" + q
    return None


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def unwrap_ddg(href: str) -> str:
    """DuckDuckGo wraps links as //duckduckgo.com/l/?uddg=<encoded>."""
    if "uddg=" in href:
        try:
            return unquote(parse_qs(urlparse(href).query)["uddg"][0])
        except Exception:
            pass
    if href.startswith("//"):
        return "https:" + href
    return href


# ----------------------------------------------------------------------------
# search backends
# ----------------------------------------------------------------------------

def search_serper(query: str, page: int, f):
    r = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"},
        json={
            "q": query,
            "num": RESULTS_PER_PAGE,
            "page": page,
            **({"tbs": TIME_MAP[f["time"]]["serper"]} if f["time"] else {}),
            **({"gl": f["region"]} if f["region"] else {}),
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()

    answer = None
    if data.get("answerBox"):
        box = data["answerBox"]
        answer = box.get("answer") or box.get("snippet")
    elif data.get("knowledgeGraph", {}).get("description"):
        answer = data["knowledgeGraph"]["description"]

    results = [
        {
            "title": it.get("title", ""),
            "url": it.get("link", ""),
            "snippet": it.get("snippet", ""),
            "domain": domain_of(it.get("link", "")),
        }
        for it in data.get("organic", [])
        if it.get("link")
    ]
    return results, answer


def search_google_cse(query: str, page: int, f):
    """Google Programmable Search — 100 free searches a day, max 10 per page."""
    r = requests.get(
        "https://www.googleapis.com/customsearch/v1",
        params={
            "key": GOOGLE_KEY,
            "cx": GOOGLE_CX,
            "q": query,
            "num": 10,
            "start": (page - 1) * 10 + 1,
            "safe": "active" if f["safe"] == "on" else "off",
            **({"dateRestrict": TIME_MAP[f["time"]]["google"]} if f["time"] else {}),
            **({"fileType": f["type"]} if f["type"] else {}),
            **({"gl": f["region"], "cr": "country" + f["region"].upper()} if f["region"] else {}),
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    results = [
        {
            "title": it.get("title", ""),
            "url": it.get("link", ""),
            "snippet": it.get("snippet", ""),
            "domain": domain_of(it.get("link", "")),
        }
        for it in data.get("items", [])
    ]
    return results, None


def search_tavily(query: str, page: int, f):
    r = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": TAVILY_KEY,
            "query": query,
            "max_results": RESULTS_PER_PAGE,
            "include_answer": True,
            **({"days": {"day": 1, "week": 7, "month": 30, "year": 365}[f["time"]],
                "topic": "news"} if f["time"] else {}),
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    results = [
        {
            "title": it.get("title", ""),
            "url": it.get("url", ""),
            "snippet": it.get("content", "")[:280],
            "domain": domain_of(it.get("url", "")),
        }
        for it in data.get("results", [])
    ]
    return results, data.get("answer")


def search_brave(query: str, page: int, f):
    r = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"X-Subscription-Token": BRAVE_KEY, "Accept": "application/json"},
        params={
            "q": query,
            "count": RESULTS_PER_PAGE,
            "offset": page - 1,
            "safesearch": "moderate" if f["safe"] == "on" else "off",
            **({"freshness": TIME_MAP[f["time"]]["brave"]} if f["time"] else {}),
            **({"country": f["region"]} if f["region"] else {}),
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    results = [
        {
            "title": it.get("title", ""),
            "url": it.get("url", ""),
            "snippet": re.sub(r"<[^>]+>", "", it.get("description", "")),
            "domain": domain_of(it.get("url", "")),
        }
        for it in data.get("web", {}).get("results", [])
    ]
    return results, None


def search_wikipedia(query: str, page: int, f):
    """Last-resort source. Wikipedia's API is open to cloud servers, so the site
    still returns something useful when no search key is configured."""
    r = requests.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query", "list": "search", "srsearch": query,
            "srlimit": RESULTS_PER_PAGE, "sroffset": (page - 1) * RESULTS_PER_PAGE,
            "format": "json", "srprop": "snippet",
        },
        headers={"User-Agent": "LSPSO/1.0 (search app)"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    hits = r.json().get("query", {}).get("search", [])
    results = []
    for h in hits:
        slug = h["title"].replace(" ", "_")
        results.append({
            "title": h["title"],
            "url": f"https://en.wikipedia.org/wiki/{quote_plus(slug)}",
            "snippet": re.sub(r"<[^>]+>", "", h.get("snippet", "")) + "…",
            "domain": "en.wikipedia.org",
        })
    return results, None


def search_duckduckgo(query: str, page: int, f):
    """DuckDuckGo has several front-ends. Cloud IPs get blocked on some but not
    others, so try each in turn and use the first that returns results."""
    offset = (page - 1) * 30
    data = {
        "q": query,
        "s": str(offset),
        "kl": (f["region"] + "-" + f["region"]) if f["region"] else "wt-wt",
        "kp": "1" if f["safe"] == "on" else "-2",
        **({"df": TIME_MAP[f["time"]]["ddg"]} if f["time"] else {}),
    }
    headers = {
        "User-Agent": UA,
        "Referer": "https://duckduckgo.com/",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    endpoints = [
        ("POST", "https://html.duckduckgo.com/html/"),
        ("POST", "https://lite.duckduckgo.com/lite/"),
        ("GET", "https://html.duckduckgo.com/html/"),
    ]

    last_error = None
    for method, url in endpoints:
        try:
            if method == "POST":
                r = requests.post(url, data=data, headers=headers, timeout=TIMEOUT)
            else:
                r = requests.get(url, params=data, headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            results = parse_ddg_html(r.text)
            if results:
                return results[:RESULTS_PER_PAGE], ddg_instant_answer(query)
            last_error = f"{url} returned no results"
        except Exception as exc:
            last_error = f"{url} -> {type(exc).__name__}: {exc}"
            continue

    app.logger.error("All DuckDuckGo endpoints failed. Last: %s", last_error)
    raise RuntimeError(last_error or "DuckDuckGo unavailable")


def parse_ddg_html(html_text):
    """Works for both the html/ and lite/ front-ends."""
    soup = BeautifulSoup(html_text, "html.parser")
    results = []

    for block in soup.select("div.result, div.web-result"):
        link = block.select_one("a.result__a")
        if not link:
            continue
        url = unwrap_ddg(link.get("href", ""))
        if not url.startswith("http"):
            continue
        snip = block.select_one(".result__snippet")
        results.append({
            "title": link.get_text(" ", strip=True),
            "url": url,
            "snippet": snip.get_text(" ", strip=True) if snip else "",
            "domain": domain_of(url),
        })

    if results:
        return results

    # lite/ uses a plain table layout
    for link in soup.select("a.result-link"):
        url = unwrap_ddg(link.get("href", ""))
        if not url.startswith("http"):
            continue
        row = link.find_parent("tr")
        snippet = ""
        if row and row.find_next_sibling("tr"):
            snippet = row.find_next_sibling("tr").get_text(" ", strip=True)
        results.append({
            "title": link.get_text(" ", strip=True),
            "url": url,
            "snippet": snippet,
            "domain": domain_of(url),
        })

    return results


def ddg_instant_answer(query: str):
    """Short factual answer for question-style queries."""
    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            headers={"User-Agent": UA},
            timeout=8,
        )
        data = r.json()
        text = data.get("AbstractText") or data.get("Answer") or ""
        if not text:
            for topic in data.get("RelatedTopics", [])[:1]:
                text = topic.get("Text", "")
        return text or None
    except Exception:
        return None


def run_search(query: str, page: int, f):
    """Returns (results, answer, error)."""
    try:
        if SERPER_KEY:
            results, answer = search_serper(query, page, f)
        elif GOOGLE_KEY and GOOGLE_CX:
            results, answer = search_google_cse(query, page, f)
        elif TAVILY_KEY:
            results, answer = search_tavily(query, page, f)
        elif BRAVE_KEY:
            results, answer = search_brave(query, page, f)
        else:
            try:
                results, answer = search_duckduckgo(query, page, f)
            except Exception:
                # cloud IPs are often blocked -> fall back to an open source
                results, answer = search_wikipedia(query, page, f)
        if answer is None:
            answer = ddg_instant_answer(query)
        if not results:
            return [], answer, "No pages matched that search. Try different words."
        return results, answer, None
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        return [], None, f"The search provider replied with error {code}. Wait a moment and search again."
    except requests.RequestException as e:
        app.logger.error("Search request failed: %s", e)
        return [], None, NO_ENGINE_MESSAGE
    except Exception as e:
        app.logger.error("Search failed: %s", e)
        return [], None, NO_ENGINE_MESSAGE


# ----------------------------------------------------------------------------
# routes
# ----------------------------------------------------------------------------

def engine_name():
    if SERPER_KEY:
        return "Google"
    if GOOGLE_KEY and GOOGLE_CX:
        return "Google"
    if TAVILY_KEY:
        return "Tavily"
    if BRAVE_KEY:
        return "Brave"
    return "DuckDuckGo"


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/search")
def search():
    query = (request.args.get("q") or "").strip()
    mode = request.args.get("mode", "web")
    site = (request.args.get("site") or "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    if not query and not site:
        return render_template("index.html")

    # searching inside one website
    effective = query
    if site:
        host = domain_of(looks_like_site(site) or "https://" + site) or site
        effective = f"site:{host} {query}".strip()

    direct = looks_like_site(query) if not site else None
    if mode == "ask" and query and not query.endswith("?"):
        effective = query  # question words already work as-is

    f = read_filters(request.args)

    # file type is a query operator on providers that don't take a parameter
    if f["type"] and not (GOOGLE_KEY and GOOGLE_CX):
        effective = f"{effective} filetype:{f['type']}"

    results, answer, error = run_search(effective, page, f)

    return render_template(
        "results.html",
        q=query,
        site=site,
        mode=mode,
        page=page,
        results=results,
        answer=answer if mode == "ask" or answer else None,
        error=error,
        direct=direct,
        count=len(results),
        engine=engine_name(),
        f=f,
        regions=REGIONS,
        file_types=FILE_TYPES,
    )


@app.errorhandler(500)
@app.errorhandler(Exception)
def server_error(e):
    """Log the real traceback. Never raise from inside here."""
    import traceback
    from werkzeug.exceptions import HTTPException

    if isinstance(e, HTTPException) and e.code != 500:
        return e                               # let 404 etc. behave normally

    tb = traceback.format_exc()
    app.logger.error("LSPSO error:\n%s", tb)
    show = app.debug or os.environ.get("SHOW_ERRORS") == "1"
    detail = tb if show else ""

    try:
        return render_template("error.html", detail=detail), 500
    except Exception:
        # templates missing or broken -> plain HTML so the cause is still visible
        body = "<pre>" + detail.replace("<", "&lt;") + "</pre>" if show else \
               "<p>Something broke. Try again.</p>"
        return (
            "<html><body style=\"font-family:sans-serif;padding:40px;max-width:800px\">"
            "<h2>LSPSO</h2>" + body +
            "<p><a href=\"/\">Back to search</a></p></body></html>",
            500,
        )


@app.route("/debug/search")
def debug_search():
    """Shows exactly which search sources work from this server."""
    q = request.args.get("q", "python")
    report = {}
    tests = [
        ("duckduckgo_html", "POST", "https://html.duckduckgo.com/html/"),
        ("duckduckgo_lite", "POST", "https://lite.duckduckgo.com/lite/"),
        ("wikipedia", "GET", "https://en.wikipedia.org/w/api.php"),
    ]
    for name, method, url in tests:
        try:
            if name == "wikipedia":
                r = requests.get(url, params={"action": "query", "list": "search",
                                              "srsearch": q, "format": "json"},
                                 headers={"User-Agent": "LSPSO/1.0"}, timeout=10)
            elif method == "POST":
                r = requests.post(url, data={"q": q}, headers={"User-Agent": UA}, timeout=10)
            report[name] = f"HTTP {r.status_code}" + (
                " (blocked - normal on cloud hosting)" if r.status_code == 403 else ""
            )
        except Exception as exc:
            report[name] = f"{type(exc).__name__}: {exc}"
    report["configured_engine"] = engine_name()
    report["fix"] = "Set GOOGLE_API_KEY + GOOGLE_CX for reliable results (100 free/day)."
    return report


@app.route("/status")
def status():
    """Open /status to see what is configured. No secrets are shown."""
    import sys
    tpl = os.path.join(app.root_path, "templates")
    return {
        "python": sys.version.split()[0],
        "templates_found": sorted(os.listdir(tpl)) if os.path.isdir(tpl) else "templates folder MISSING",
        "static_found": os.path.isfile(os.path.join(app.root_path, "static", "style.css")),
        "search_engine": engine_name(),
        "accounts_enabled": AUTH_ENABLED,
        "in_site_viewer": VIEWER_ENABLED,
        "accounts_problem": AUTH_ERROR,
        "secret_key_set": os.environ.get("SECRET_KEY") is not None,
        "google_ready": bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")),
        "github_ready": bool(os.environ.get("GITHUB_CLIENT_ID") and os.environ.get("GITHUB_CLIENT_SECRET")),
        "gitlab_ready": bool(os.environ.get("GITLAB_CLIENT_ID") and os.environ.get("GITLAB_CLIENT_SECRET")),
        "microsoft_ready": bool(os.environ.get("MICROSOFT_CLIENT_ID") and os.environ.get("MICROSOFT_CLIENT_SECRET")),
        "discord_ready": bool(os.environ.get("DISCORD_CLIENT_ID") and os.environ.get("DISCORD_CLIENT_SECRET")),
        "chatgpt_ready": bool(os.environ.get("CHATGPT_CLIENT_ID") and os.environ.get("CHATGPT_CLIENT_SECRET")),
        "yahoo_ready": bool(os.environ.get("YAHOO_CLIENT_ID") and os.environ.get("YAHOO_CLIENT_SECRET")),
        "resend_ready": bool(os.environ.get("RESEND_API_KEY")),
        "sms_ready": bool(
            (os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN")
             and os.environ.get("TWILIO_FROM_NUMBER"))
            or (os.environ.get("MSG91_AUTH_KEY") and os.environ.get("MSG91_TEMPLATE_ID"))
        ),
        "database": "postgres" if os.environ.get("DATABASE_URL") else "sqlite (wiped on redeploy)",
    }


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
