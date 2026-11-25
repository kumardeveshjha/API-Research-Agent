"""PASS 1: Research agent. Discover -> Fetch -> Extract -> Save.

Usage:
    python agent/research.py --only Stripe --workers 1 --fresh
    python agent/research.py --only Stripe,Slack,Notion --workers 2
    python agent/research.py --apps data/apps.csv --workers 5
"""

import argparse
import faulthandler
import sys
import csv
import json
import os
import pathlib
import re
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

from schema import AppResult
import composio_tools as ct
import llm

load_dotenv()

ROOT = pathlib.Path(__file__).resolve().parent.parent

MODEL = os.getenv("RESEARCH_MODEL", "gemini-3.8-flash")
MAX_PAGES = int(os.getenv("MAX_PAGES", "6"))
MAX_CHARS = int(os.getenv("MAX_CHARS", "5000"))

QUERIES = [
    "{name} API documentation authentication",
    "{name} API pricing access developer account",
    "{name} MCP server",
]

SYSTEM = """You are a careful API researcher for Composio, which builds agent toolkits for apps.

Answer ONLY from the page text provided.
If the text does not say, use 'unclear'. Never guess from memory.

Every claim in auth_methods, access, api_type, existing_mcp,
and buildability needs an evidence entry whose quote is copied
EXACTLY (verbatim, under 25 words) from the provided text, with its URL.

Rules:
- access: self_serve_free only if a developer can get credentials
  without paying or contacting anyone.
- Apps that are CLIs/libraries without a web API:
  api_type=cli_only, buildability=blocked or ready_with_caveats.
- Set needs_human=true when pages were empty, contradictory,
  or the app has weak documentation.
- buildability=ready only if there is a documented public API
  AND credentials are obtainable by a developer.

Return ONLY one JSON object matching this JSON schema, no prose:
"""


def discover_and_fetch(app: dict) -> dict[str, str]:
    print(
        f"[{app['id']:>3}] {app['name']}: discovering + fetching pages...",
        flush=True
    )

    pages: dict[str, str] = {}
    hint = app.get("hint", "")

    if hint.startswith(("http", "www")) or "." in hint.split(" ")[0]:
        url = hint.split(" ")[0]
        url = url if url.startswith("http") else "https://" + url
        pages[url] = ct.scrape(url)

    for query in QUERIES:
        hits = ct.search(query.format(name=app["name"]), limit=3)

        for hit in hits[:2]:
            url = hit.get("url")

            if not url or url in pages or len(pages) >= MAX_PAGES:
                continue

            text = hit.get("text", "")

            pages[url] = (
                text if len(text) > 800 else ct.scrape(url)
            )

    return pages


def _extract_json(raw: str) -> str:
    """Extract the first complete JSON object from raw LLM output.

    The LLM sometimes truncates at max_tokens mid-object, leaving no closing '}'.
    Strategy:
      1. Try the full string as-is (fast path, works when output is clean).
      2. Find the first '{', then walk forward counting braces to find the
         matching '}' — this handles leading/trailing prose and partial truncation
         by taking the largest complete object we can find.
      3. If still no match, raise a clear error with the first 300 chars for debugging.
    """
    # Fast path: whole string is valid JSON
    stripped = raw.strip()
    try:
        json.loads(stripped)
        return stripped
    except json.JSONDecodeError:
        pass

    # Find the outermost { ... } by brace-counting
    start = raw.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in LLM response. Response starts: {raw[:300]}")

    depth = 0
    in_string = False
    escape_next = False
    end = -1

    for i, ch in enumerate(raw[start:], start=start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break

    if end == -1:
        # Truncated — take everything from '{' and try to close it
        # by appending minimal closing braces (last-resort recovery)
        fragment = raw[start:]
        # Count unclosed braces outside strings (rough count)
        rough_depth = 0
        in_s = False
        esc = False
        for ch in fragment:
            if esc:
                esc = False
                continue
            if ch == "\\" and in_s:
                esc = True
                continue
            if ch == '"':
                in_s = not in_s
                continue
            if in_s:
                continue
            if ch == "{":
                rough_depth += 1
            elif ch == "}":
                rough_depth -= 1
        # Strip trailing incomplete field (last comma or partial key)
        # and close the object
        trimmed = fragment.rstrip().rstrip(",")
        # Remove any dangling incomplete string at the end
        trimmed = re.sub(r',\s*"[^"]*$', '', trimmed)
        padded = trimmed + ("}" * max(rough_depth, 1))
        try:
            json.loads(padded)
            return padded
        except json.JSONDecodeError:
            raise ValueError(
                f"LLM response was truncated and could not be recovered. "
                f"Response starts: {raw[:300]}"
            )

    return raw[start:end + 1]

FIELD_GUIDE = """
Return a JSON object with EXACTLY these fields (no extras, no omissions):

{
  "name": "<app name>",
  "category": "<category>",
  "one_liner": "<one sentence what this app does>",
  "auth_methods": ["<one or more of: oauth2, api_key, basic, bearer_token, jwt, bot_token, none, other>"],
  "access": "<one of: self_serve_free, trial, paid_required, approval_required, partner_gated, no_api, unclear>",
  "api_type": "<one of: rest, graphql, both, cli_only, sdk_only, none, unclear>",
  "api_breadth": "<one of: small, medium, large, unclear>",
  "endpoint_estimate": "<e.g. '~50 endpoints' or null>",
  "existing_mcp": "<one of: official, community, none, unclear>",
  "mcp_url": "<url string or null>",
  "buildability": "<one of: ready, ready_with_caveats, blocked>",
  "main_blocker": "<one of: none, paid_plan, partner_approval, contact_sales, no_public_api, poor_docs, other>",
  "webhooks": "<one of: yes, no, unclear>",
  "openapi_spec": "<one of: yes, no, unclear>",
  "rate_limits_documented": "<one of: yes, no, unclear>",
  "sandbox_or_test_mode": "<one of: yes, no, unclear>",
  "evidence": [
    {"field": "<field name>", "url": "<url>", "quote": "<exact verbatim quote, max 15 words>"},
    {"field": "...", "url": "...", "quote": "..."}
  ],
  "confidence": "<one of: high, med, low>",
  "needs_human": false,
  "notes": ""
}

IMPORTANT: Provide ONE evidence quote for EACH of these 5 fields if the text supports it: auth_methods, access, api_type, existing_mcp, buildability. Up to 5 entries total. Keep every quote under 12 words.
"""

def extract(app: dict, pages: dict[str, str], extra: str = "") -> AppResult:
    if not pages:
        raise ValueError("No source pages were successfully fetched.")

    source_text = "\n\n".join(
        f"=== URL: {url} ===\n{text[:MAX_CHARS]}"
        for url, text in pages.items()
    )

    raw = llm.complete(
        SYSTEM + extra + FIELD_GUIDE,
        f"App: {app['name']}\nCategory: {app['category']}\n\n{source_text}",
        MODEL,
        max_tokens=8000
    )

    json_str = _extract_json(raw)
    return AppResult.model_validate_json(json_str)


def run_one(app: dict, fresh: bool = False) -> dict:
    out = ROOT / "cache" / f"result_v1_{app['id']}.json"

    if out.exists() and not fresh:
        cached = json.loads(out.read_text(encoding="utf-8"))

        if "error" not in cached:
            print(
                f"[{app['id']:>3}] {app['name']}: using cached result",
                flush=True
            )
            return cached

        print(
            f"[{app['id']:>3}] {app['name']}: previous attempt failed; retrying",
            flush=True
        )

    if fresh:
        print(
            f"[{app['id']:>3}] {app['name']}: bypassing app-result cache",
            flush=True
        )

    try:
        pages = discover_and_fetch(app)
        if len(pages) < 2:
            raise ValueError(f"Only {len(pages)} usable page(s) fetched; not enough evidence")

        print(
            f"[{app['id']:>3}] {app['name']}: "
            f"{len(pages)} pages fetched, asking LLM...",
            flush=True
        )

        result = extract(app, pages).model_dump()

        result["id"] = int(app["id"])
        result["pages_fetched"] = list(pages.keys())

    except Exception as error:
        result = {
            "id": int(app["id"]),
            "name": app["name"],
            "category": app["category"],
            "error": str(error),
            "needs_human": True
        }

    if "error" not in result:
        out.parent.mkdir(parents=True, exist_ok=True)

        out.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    else:
        print(
            f"[{app['id']:>3}] {app['name']}: FAILED -> {result['error']}",
            flush=True
        )

    status = (
        "ERROR " + result["error"][:80]
        if "error" in result
        else result["access"] + " / " + result["buildability"]
    )

    print(
        f"[{app['id']:>3}] {app['name']}: {status}",
        flush=True
    )

    return result


def main():
    faulthandler.dump_traceback_later(
        90,
        repeat=True,
        file=sys.stderr
    )

    parser = argparse.ArgumentParser(
        description="Research app APIs for Composio toolkit opportunities."
    )

    parser.add_argument(
        "--apps",
        default=str(ROOT / "data" / "apps.csv")
    )

    parser.add_argument(
        "--only",
        help="Comma-separated app names"
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=5,
        help="Number of apps to research concurrently"
    )

    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Bypass existing app-result cache"
    )

    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers must be at least 1")

    with open(args.apps, encoding="utf-8", newline="") as file:
        apps = list(csv.DictReader(file))

    if args.only:
        wanted = {
            name.strip().lower()
            for name in args.only.split(",")
        }

        apps = [
            app for app in apps
            if app["name"].strip().lower() in wanted
        ]

        if not apps:
            parser.error("No matching app names found in the CSV.")

    print(f"Apps selected: {len(apps)}")
    print(f"Workers: {args.workers}")
    print(f"Fresh mode: {args.fresh}")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = list(
            executor.map(
                lambda app: run_one(app, fresh=args.fresh),
                apps
            )
        )

    destination = ROOT / "data" / (
        "results_v1_sample.json" if args.only else "results_v1.json"
    )

    destination.parent.mkdir(parents=True, exist_ok=True)

    destination.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    errors = [
        result for result in results
        if "error" in result
    ]

    print(
        f"\nSaved {len(results)} -> {destination} "
        f"({len(errors)} errors, log them in data/human_log.md)"
    )

    print(
        "Scrape fallbacks used (plain HTTP, not Composio):",
        ct.USED_FALLBACK["scrape_requests"]
    )


if __name__ == "__main__":
    main()