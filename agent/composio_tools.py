"""Thin wrapper around Composio so the rest of the code never touches the SDK directly.

Run `python agent/composio_tools.py` as a smoke test. If the session setup errors, compare with
https://docs.composio.dev/docs/quickstart and fix ONLY this file.

v2 fixes (vs the first version):
  * SDK result objects are converted to plain dicts before parsing (the old parser skipped them).
  * Tool failures (successful=False / error) are printed instead of silently becoming empty text.
  * Empty searches and too-short scrapes are NEVER cached, so a bad run cannot poison later runs.
  * The plain-HTTP fallback strips HTML to text instead of passing raw <script>/<style> soup to the LLM.
"""
import concurrent.futures as cf
import hashlib
import json
import os
import pathlib
import re
import threading
import time

import requests
from dotenv import load_dotenv

load_dotenv()
CACHE = pathlib.Path(__file__).resolve().parent.parent / "cache"
CACHE.mkdir(exist_ok=True)

BACKEND = os.getenv("SEARCH_BACKEND", "firecrawl")   # "firecrawl" (needs connection) or "composio_search" (no auth)
TOOLKIT = "firecrawl" if BACKEND == "firecrawl" else "composio_search"
MIN_TEXT = 300                                        # below this, a scrape counts as "too little text"
_session = None
USED_FALLBACK = {"scrape_requests": 0}   # reported on the page for honesty


def _get_session():
    global _session
    if _session is not None:
        return _session
    from composio import Composio
    composio = Composio()  # reads COMPOSIO_API_KEY from env
    try:
        _session = composio.create(user_id="research-agent", toolkits=[TOOLKIT])
    except TypeError:
        _session = composio.sessions.create(user_id="research-agent", toolkits=[TOOLKIT])
    return _session


_POOL = cf.ThreadPoolExecutor(max_workers=8)
CALL_TIMEOUT = int(os.getenv("CALL_TIMEOUT", "60"))   # seconds per Composio call


def _plain(x):
    """Turn SDK objects (pydantic models etc.) into plain dicts/lists so they can be walked."""
    if hasattr(x, "model_dump"):
        try:
            return _plain(x.model_dump())
        except Exception:  # noqa: BLE001
            pass
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, (str, int, float, bool)) or x is None:
        return x
    if hasattr(x, "__dict__"):
        return _plain(vars(x))
    return str(x)


RPM = float(os.getenv("FIRECRAWL_RPM", "8"))   # max Firecrawl calls per minute, shared by ALL threads
_RL_LOCK = threading.Lock()
_NEXT_OK = [0.0]


def _throttle():
    """Space calls out globally so we stay under Firecrawl's requests-per-minute limit."""
    with _RL_LOCK:
        now = time.time()
        slot = max(now, _NEXT_OK[0])
        _NEXT_OK[0] = slot + 60.0 / RPM
    time.sleep(max(0.0, slot - now))


def _execute(slug: str, arguments: dict, attempts: int = 4):
    """Run one Composio tool call: throttled, with a timeout, retrying on rate limits.
    Returns a plain dict/list. Raises if the tool reports failure.
    Note: Composio reports success even when Firecrawl itself answered HTTP 429, hiding it in data.http_error."""
    for i in range(attempts):
        _throttle()
        t0 = time.time()
        fut = _POOL.submit(lambda: _get_session().execute(slug, arguments=arguments))
        try:
            res = fut.result(timeout=CALL_TIMEOUT)
        except cf.TimeoutError:
            print(f"    [composio] {slug} TIMED OUT after {CALL_TIMEOUT}s", flush=True)
            raise TimeoutError(f"{slug} timed out")
        res = _plain(res)
        if isinstance(res, dict) and res.get("successful") is False:
            err = str(res.get("error"))[:200]
            print(f"    [composio] {slug} FAILED in {time.time() - t0:.1f}s: {err}", flush=True)
            raise RuntimeError(f"{slug} failed: {err}")
        data = res.get("data") if isinstance(res, dict) else None
        if isinstance(data, dict) and data.get("http_error"):
            msg = str(data.get("http_error"))
            if "429" in msg and i < attempts - 1:
                wait = 20 * (i + 1)
                print(f"    [composio] {slug} rate limited (429). Waiting {wait}s, retry {i + 2}/{attempts}", flush=True)
                time.sleep(wait)
                continue
            raise RuntimeError(f"{slug} http error: {msg[:150]}")
        print(f"    [composio] {slug} ok in {time.time() - t0:.1f}s", flush=True)
        return res


def _key(*parts) -> pathlib.Path:
    return CACHE / (hashlib.sha1("|".join(parts).encode()).hexdigest() + ".json")


def _normalize_search(raw) -> list[dict]:
    """Composio result shapes vary. Walk the structure and pull anything with a url."""
    found = []

    def walk(x):
        if isinstance(x, dict):
            url = x.get("url") or x.get("link")
            if isinstance(url, str) and url.startswith("http"):
                text = x.get("markdown") or x.get("content") or x.get("description") or x.get("snippet") or ""
                found.append({"url": url, "title": x.get("title", "") or "", "text": text if isinstance(text, str) else ""})
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(_plain(raw))
    seen, uniq = set(), []
    for f in found:
        if f["url"] not in seen:
            seen.add(f["url"])
            uniq.append(f)
    return uniq


def _longest_text(x, best: str = "") -> str:
    """Find the longest markdown/content/text string anywhere in a result (scrape results have no per-item url)."""
    x = _plain(x)
    if isinstance(x, dict):
        for k, v in x.items():
            if k in ("markdown", "content", "text", "html") and isinstance(v, str) and len(v) > len(best):
                best = v
            best = _longest_text(v, best)
    elif isinstance(x, list):
        for v in x:
            best = _longest_text(v, best)
    return best


def _html_to_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style", "noscript", "svg"]):
            t.decompose()
        return soup.get_text(" ", strip=True)
    except ImportError:
        html = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", html)
        return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html)).strip()


def search(query: str, limit: int = 5) -> list[dict]:
    """Returns [{'url','title','text'}]. Cached on disk (only non-empty results)."""
    p = _key("search", query)
    if p.exists():
        return json.loads(p.read_text())
    try:
        if BACKEND == "firecrawl":
            raw = _execute("FIRECRAWL_SEARCH", {"query": query, "limit": limit})
        else:
            raw = _execute("COMPOSIO_SEARCH_WEB", {"query": query})
    except Exception as e:  # noqa: BLE001
        print(f"    [search] failed for '{query}': {str(e)[:150]}", flush=True)
        return []
    out = _normalize_search(raw)
    if out:
        p.write_text(json.dumps(out))
    else:
        print(f"    [search] 0 results parsed for '{query}'. Raw start: {str(raw)[:300]}", flush=True)
    return out


def scrape(url: str) -> str:
    """Page text as markdown. Tries Composio Firecrawl first, then plain HTTP (logged as fallback)."""
    p = _key("scrape", url)
    if p.exists():
        return json.loads(p.read_text())["text"]
    text = ""
    used_fallback = False
    print(f"    [scrape] {url[:90]}", flush=True)
    try:
        if BACKEND == "firecrawl":
            raw = _execute("FIRECRAWL_SCRAPE", {"url": url})
        else:  # argument names not confirmed in docs: try the likely ones
            try:
                raw = _execute("COMPOSIO_SEARCH_FETCH_URL_CONTENT", {"url": url})
            except Exception:  # noqa: BLE001
                raw = _execute("COMPOSIO_SEARCH_FETCH_URL_CONTENT", {"urls": [url]})
        text = _longest_text(raw)
        if len(text.strip()) < MIN_TEXT:
            print(f"    [scrape] parsed only {len(text)} chars. Raw start: {str(raw)[:300]}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"  composio scrape failed for {url}: {str(e)[:150]}", flush=True)
    if len(text.strip()) < MIN_TEXT:
        try:
            USED_FALLBACK["scrape_requests"] += 1
            used_fallback = True
            print("    [scrape] Composio returned too little text, trying plain HTTP", flush=True)
            r = requests.get(url, timeout=(5, 15), headers={"User-Agent": "Mozilla/5.0"})
            text = _html_to_text(r.text)
        except Exception as e:  # noqa: BLE001
            text = f"[FETCH FAILED: {e}]"
    text = text[:60000]
    if len(text.strip()) >= MIN_TEXT and not text.startswith("[FETCH FAILED") and not used_fallback:
        p.write_text(json.dumps({"url": url, "text": text}))   # never cache failures or near-empty pages
    return text


if __name__ == "__main__":
    print("Smoke test: searching via Composio...")
    hits = search("Stripe API authentication")
    print(f"{len(hits)} results")
    for h in hits[:3]:
        print("-", h["url"], f"({len(h['text'])} chars of snippet)")
    if hits:
        print("Scrape test:", len(scrape(hits[0]["url"])), "chars")