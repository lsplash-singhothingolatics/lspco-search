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

import db
from auth import auth as auth_blueprint, current_user

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
app.register_blueprint(auth_blueprint)

try:
    db.init_db()
except Exception as exc:                       # never let a DB hiccup kill boot
    app.logger.error("Database init failed: %s", exc)


@app.context_processor
def inject_user():
    """Makes `user` available inside every template."""
    try:
        return {"user": current_user()}
    except Exception:
        return {"user": None}

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


def search_duckduckgo(query: str, page: int, f):
    offset = (page - 1) * 30
    r = requests.post(
        "https://html.duckduckgo.com/html/",
        data={
            "q": query,
            "s": str(offset),
            "kl": (f["region"] + "-" + f["region"]) if f["region"] else "wt-wt",
            "kp": "1" if f["safe"] == "on" else "-2",
            **({"df": TIME_MAP[f["time"]]["ddg"]} if f["time"] else {}),
        },
        headers={"User-Agent": UA, "Referer": "https://duckduckgo.com/"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    results = []
    for block in soup.select("div.result, div.web-result"):
        link = block.select_one("a.result__a")
        if not link:
            continue
        url = unwrap_ddg(link.get("href", ""))
        if not url.startswith("http"):
            continue
        snip = block.select_one(".result__snippet")
        results.append(
            {
                "title": link.get_text(" ", strip=True),
                "url": url,
                "snippet": snip.get_text(" ", strip=True) if snip else "",
                "domain": domain_of(url),
            }
        )
        if len(results) >= RESULTS_PER_PAGE:
            break

    return results, ddg_instant_answer(query)


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
            results, answer = search_duckduckgo(query, page, f)
        if answer is None:
            answer = ddg_instant_answer(query)
        if not results:
            return [], answer, "No pages matched that search. Try different words."
        return results, answer, None
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        return [], None, f"The search provider replied with error {code}. Wait a moment and search again."
    except requests.RequestException:
        return [], None, "Could not reach the search provider. Check the connection and try again."


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
    """Log the real traceback and show a readable page instead of a blank 500."""
    import traceback
    tb = traceback.format_exc()
    app.logger.error("LSPSO error:\n%s", tb)
    detail = tb if app.debug or os.environ.get("SHOW_ERRORS") == "1" else ""
    return render_template("error.html", detail=detail), 500


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
