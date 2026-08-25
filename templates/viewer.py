"""Opens an external page inside LSPSO instead of sending the visitor away.

Fetches the page server-side, strips scripts, and rewrites links so they keep
flowing through LSPSO. Not a full browser: pages that build themselves with
JavaScript will look bare, so the viewer always offers the original link.
"""

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse, quote_plus

import requests
from bs4 import BeautifulSoup
from flask import Blueprint, render_template, request, redirect

viewer = Blueprint("viewer", __name__)

TIMEOUT = 14
MAX_BYTES = 3_000_000
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# tags removed entirely before display
STRIP_TAGS = ["script", "noscript", "iframe", "object", "embed", "form", "svg use"]


def is_public_url(url: str) -> bool:
    """Blocks internal addresses so the viewer can't be used to probe the network."""
    try:
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        for info in socket.getaddrinfo(parts.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                return False
        return True
    except Exception:
        return False


def proxy_link(url: str) -> str:
    return "/open?url=" + quote_plus(url)


def clean_document(html_text: str, base_url: str):
    soup = BeautifulSoup(html_text, "html.parser")

    for selector in STRIP_TAGS:
        for tag in soup.select(selector):
            tag.decompose()

    # drop inline event handlers
    for tag in soup.find_all(True):
        for attr in [a for a in tag.attrs if a.lower().startswith("on")]:
            del tag[attr]

    # images and stylesheets need absolute addresses to load
    for tag in soup.find_all(["img", "source"]):
        for attr in ("src", "srcset", "data-src"):
            if tag.get(attr):
                tag[attr] = urljoin(base_url, tag[attr].split()[0])
    for tag in soup.find_all("link", href=True):
        tag["href"] = urljoin(base_url, tag["href"])

    # links keep the visitor inside LSPSO
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        if absolute.startswith(("http://", "https://")):
            tag["href"] = proxy_link(absolute)
            tag["target"] = "_self"

    title = soup.title.get_text(strip=True) if soup.title else urlparse(base_url).netloc
    body = soup.body or soup
    return title, str(body)


@viewer.route("/open")
def open_page():
    url = (request.args.get("url") or "").strip()
    if not url:
        return redirect("/")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if not is_public_url(url):
        return render_template(
            "viewer.html", url=url, domain=urlparse(url).netloc,
            title="Cannot open", content=None,
            error="That address can't be opened here.",
        ), 400

    try:
        r = requests.get(
            url,
            headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=TIMEOUT,
            stream=True,
        )
        content_type = r.headers.get("Content-Type", "")

        # send non-HTML straight to the source (PDFs, images, downloads)
        if "text/html" not in content_type:
            return redirect(url)

        raw = r.raw.read(MAX_BYTES, decode_content=True)
        r.close()
        html_text = raw.decode(r.encoding or "utf-8", errors="replace")
        title, content = clean_document(html_text, r.url)
        error = None
        if r.status_code >= 400:
            error = f"The site returned an error ({r.status_code})."
    except Exception:
        title, content = urlparse(url).netloc, None
        error = (
            "This page could not be loaded here. Some sites block servers from "
            "reading them. Use the original link instead."
        )

    return render_template(
        "viewer.html",
        url=url,
        domain=urlparse(url).netloc.replace("www.", ""),
        title=title,
        content=content,
        error=error,
    )
