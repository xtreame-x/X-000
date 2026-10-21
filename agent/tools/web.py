"""Web tools (3): web_search, web_fetch, image_search.

Providers are chosen by available credentials so the tools work out of the box:

* search  -> Serper (``SERPER_API_KEY``) when set, otherwise DuckDuckGo HTML
* fetch   -> direct HTTP with redirects, size and time limits
* images  -> Wikimedia Commons (free, no key) and optionally Serper images

Every network failure is returned as a structured error, never an exception.
"""
from __future__ import annotations

import html as html_lib
import json
import re
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urlparse

import httpx

from agent.tools.base import ToolContext, ToolResult, tool

USER_AGENT = "OpenAgent/0.1 (+https://example.invalid/agent)"


def _client(ctx: ToolContext, *, follow_redirects: bool = True) -> httpx.Client:
    return httpx.Client(
        timeout=ctx.settings.request_timeout,
        follow_redirects=follow_redirects,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8"},
    )


def _net_error(tool_name: str, exc: Exception) -> ToolResult:
    return ToolResult.error(
        f"{tool_name} network error: {type(exc).__name__}: {exc}",
        error="network_error",
        tool=tool_name,
    )


# ---------------------------------------------------------------------------
# HTML -> text
# ---------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data.strip()
            return
        if self._skip_depth:
            return
        if data.strip():
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t\f\v]+", " ", raw)
        raw = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw)
        return raw.strip()


def html_to_text(markup: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(markup)
    return parser.title.strip(), parser.text()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
_DDG_RESULT = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.DOTALL,
)
_DDG_SNIPPET = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>', re.DOTALL)


def _strip_tags(value: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def _serper_search(ctx: ToolContext, query: str, max_results: int) -> list[dict]:
    key = ctx.require_secret("SERPER_API_KEY", "Serper")
    with _client(ctx) as client:
        response = client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            content=json.dumps({"q": query, "num": max_results}),
        )
        response.raise_for_status()
        payload = response.json()
    results = []
    for item in payload.get("organic", [])[:max_results]:
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
        )
    return results


def _duckduckgo_search(ctx: ToolContext, query: str, max_results: int) -> list[dict]:
    with _client(ctx) as client:
        response = client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        markup = response.text

    hrefs = list(_DDG_RESULT.finditer(markup))
    snippets = list(_DDG_SNIPPET.finditer(markup))
    results: list[dict] = []
    for index, match in enumerate(hrefs[:max_results]):
        url = html_lib.unescape(match.group("href"))
        redirect = re.search(r"uddg=([^&]+)", url)
        if redirect:
            from urllib.parse import unquote

            url = unquote(redirect.group(1))
        results.append(
            {
                "title": _strip_tags(match.group("title")),
                "url": url,
                "snippet": _strip_tags(snippets[index].group("snippet")) if index < len(snippets) else "",
            }
        )
    return results


@tool(group="Web and search", tags=("search", "google", "web", "lookup"))
def web_search(ctx: ToolContext, query: str, max_results: int = 8) -> ToolResult:
    """Search the web and return ranked results with titles, URLs and snippets.

    Uses the Serper API when SERPER_API_KEY is set, otherwise DuckDuckGo's
    HTML endpoint (no key required).

    Args:
        query: Search query.
        max_results: Maximum number of results to return (1-20).
    """
    if not query.strip():
        return ToolResult.error("query must not be empty", error="invalid_arguments")
    max_results = max(1, min(int(max_results), 20))
    provider = "serper" if ctx.settings.secret("SERPER_API_KEY") else "duckduckgo"
    try:
        results = (
            _serper_search(ctx, query, max_results)
            if provider == "serper"
            else _duckduckgo_search(ctx, query, max_results)
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the model
        return _net_error("web_search", exc)

    if not results:
        return ToolResult.text(f"No results for {query!r}.", provider=provider, results=[])
    body = "\n\n".join(
        f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}" for i, r in enumerate(results, 1)
    )
    return ToolResult.text(body, provider=provider, results=results, query=query)


@tool(group="Web and search", tags=("fetch", "url", "page", "read", "scrape"))
def web_fetch(ctx: ToolContext, url: str, max_chars: int = 20000) -> ToolResult:
    """Fetch a URL and return its readable text.

    HTML is reduced to title and body text (scripts, styles and navigation
    dropped); JSON is returned pretty-printed. Response size is capped.

    Args:
        url: Absolute http(s) URL.
        max_chars: Maximum characters of text to return.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ToolResult.error(f"unsupported URL scheme: {parsed.scheme!r}", error="bad_request")
    try:
        with _client(ctx) as client:
            response = client.get(url)
    except Exception as exc:  # noqa: BLE001
        return _net_error("web_fetch", exc)

    if response.status_code >= 400:
        return ToolResult.error(
            f"HTTP {response.status_code} fetching {url}",
            error="http_error",
            status=response.status_code,
        )

    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type in {"application/json", "application/ld+json"}:
        try:
            text = json.dumps(response.json(), indent=2, ensure_ascii=False)
        except ValueError:
            text = response.text
        title = ""
    elif content_type.startswith("text/html") or "<html" in response.text[:2000].lower():
        title, text = html_to_text(response.text)
    elif content_type.startswith("text/") or content_type == "":
        text, title = response.text, ""
    else:
        return ToolResult.error(
            f"unsupported content type {content_type!r} for {url}",
            error="unsupported_content",
            contentType=content_type,
        )

    truncated = len(text) > max_chars
    body = text[:max_chars]
    header = f"{title}\n{url}\n(HTTP {response.status_code}, {content_type or 'unknown'})"
    if truncated:
        header += f"\n[truncated at {max_chars} characters of {len(text)}]"
    return ToolResult.text(
        f"{header}\n\n{body}",
        url=str(response.url),
        status=response.status_code,
        title=title,
        contentType=content_type,
        truncated=truncated,
        characters=len(text),
    )


@tool(group="Web and search", tags=("image", "picture", "search", "photo"))
def image_search(
    ctx: ToolContext,
    query: str,
    count: int = 5,
    provider: Literal["wikimedia", "serper"] = "wikimedia",
) -> ToolResult:
    """Find images for a query and return their direct URLs.

    The default provider is Wikimedia Commons, which needs no API key and
    returns freely licensed media. Use provider='serper' with SERPER_API_KEY for
    general web images.

    Args:
        query: What to search for.
        count: Number of images to return (1-20).
        provider: Image source.
    """
    count = max(1, min(int(count), 20))
    if provider == "serper":
        key = ctx.require_secret("SERPER_API_KEY", "Serper")
        try:
            with _client(ctx) as client:
                response = client.post(
                    "https://google.serper.dev/images",
                    headers={"X-API-KEY": key, "Content-Type": "application/json"},
                    content=json.dumps({"q": query, "num": count}),
                )
                response.raise_for_status()
                items = response.json().get("images", [])[:count]
        except Exception as exc:  # noqa: BLE001
            return _net_error("image_search", exc)
        results = [
            {"title": item.get("title", ""), "url": item.get("imageUrl", ""),
             "thumbnail": item.get("thumbnailUrl", ""), "source": item.get("link", "")}
            for item in items
        ]
        return ToolResult.json(results, text="\n".join(f"- {r['url']}" for r in results))

    try:
        with _client(ctx) as client:
            response = client.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": f"filetype:bitmap {query}",
                    "gsrnamespace": 6,
                    "gsrlimit": count,
                    "prop": "imageinfo",
                    "iiprop": "url|extmetadata",
                    "iiurlwidth": 640,
                    "format": "json",
                },
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return _net_error("image_search", exc)

    pages = (payload.get("query") or {}).get("pages") or {}
    results = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        metadata = info.get("extmetadata") or {}
        results.append(
            {
                "title": page.get("title", "").replace("File:", ""),
                "url": info.get("url", ""),
                "thumbnail": info.get("thumburl", ""),
                "source": info.get("descriptionurl", ""),
                "license": (metadata.get("LicenseShortName") or {}).get("value", ""),
            }
        )
    if not results:
        return ToolResult.text(f"No images for {query!r}.", results=[], provider="wikimedia")
    return ToolResult.json(
        results,
        text="\n".join(f"- {r['title']} — {r['url']} ({r['license']})" for r in results),
        provider="wikimedia",
        query=query,
    )


__all__ = ["web_search", "web_fetch", "image_search", "html_to_text"]
