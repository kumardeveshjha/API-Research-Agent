"""PASS 2: verification loops. Produces data/results_v2.json and data/verify_report.json.

Loop A1  literal check : does each evidence quote appear in the page text the agent saw? (catches invented evidence)
Loop A2  rule checks   : deterministic sanity rules the LLM cannot talk its way around
                           - an MCP claim needs an evidence quote that actually mentions MCP
                           - 'official' MCP needs evidence on a vendor-owned site (not a marketplace or blog)
                           - cli_only / none API type means access must be no_api
Loop A3  LLM judge     : independent model (different from research) - does the quote support the claim?
Loop A4  re-research   : for every app with flags, fetch more auth/pricing/MCP pages and re-extract with the
                           failed checks and strict definitions in the prompt, keep whichever version has fewer flags

Usage:
  python agent/verify.py --only 81,35,2     # quick test on a few app ids first (writes *_sample files)
  python agent/verify.py                    # all apps
"""
import argparse
import csv
import json
import os
import pathlib
import re
from urllib.parse import urlparse

from dotenv import load_dotenv

import composio_tools as ct
import llm

load_dotenv()
ROOT = pathlib.Path(__file__).resolve().parent.parent
JUDGE = os.getenv("VERIFY_MODEL", "gemini-3.8-pro")  # different model from research on purpose

RULES_HINT = (
    "\nDefinitions you MUST follow: existing_mcp=official only if a page owned by the vendor itself "
    "(its own docs, website or its own GitHub organisation) says an MCP server exists; community if only "
    "third parties (marketplaces, blogs, other people's GitHub repos) list one; none only if the pages "
    "explicitly say there is none; otherwise unclear. Every existing_mcp evidence quote must mention MCP. "
    "If api_type is cli_only or none, access must be no_api."
)

STOP = {"https", "http", "www", "com", "docs", "developer", "developers", "apis", "open", "source", "enterprise",
        "research", "server", "content", "suite", "platform", "help", "support", "contact", "sales", "email",
        "media", "native", "cloud", "commerce", "marketing", "messaging", "social", "data", "learn", "library",
        "document", "parsing", "notes", "meeting", "funnel", "builder", "intel", "company", "connect",
        "ecommerce", "stoplight", "stoplight"}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def clean_url(u: str) -> str:
    """Evidence URLs sometimes contain markdown link syntax like https://[x.com](https://x.com)/path."""
    u = re.sub(r"\[[^\]]*\]\((https?://[^)]*)\)", r"\1", u or "")
    u = re.sub(r"^https?://(?=https?://)", "", u)
    return u.strip()


def _build_text_index() -> dict[str, str]:
    """url -> longest text already on disk (search-result snippets and scraped pages). Avoids re-scraping."""
    idx: dict[str, str] = {}
    for p in (ROOT / "cache").glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for it in (d if isinstance(d, list) else [d]):
            if isinstance(it, dict) and isinstance(it.get("url"), str) and isinstance(it.get("text"), str):
                if len(it["text"]) > len(idx.get(it["url"], "")):
                    idx[it["url"]] = it["text"]
    return idx


TEXT_INDEX = _build_text_index()


def get_text(url: str) -> str:
    return TEXT_INDEX.get(url) or ct.scrape(url)


def page_texts(res: dict) -> dict[str, str]:
    return {u: norm(get_text(u)) for u in res.get("pages_fetched", [])}


# ---------- A1: literal check ----------
def literal_check(res: dict, texts: dict[str, str]) -> list[dict]:
    """Flags quotes found in NO fetched page. A quote found on a different fetched page than the cited one
    is not a hallucination: the evidence URL is repaired instead."""
    bad = []
    by_clean = {u.rstrip("/"): u for u in texts}
    for ev in res.get("evidence", []):
        q = norm(ev.get("quote", ""))
        if not q:
            bad.append({"field": ev.get("field"), "url": ev.get("url"), "why": "empty_quote"})
            continue
        cited = by_clean.get(clean_url(ev.get("url", "")).rstrip("/"))
        if cited and q in texts[cited]:
            ev["url"] = cited
            continue
        other = next((u for u, t in texts.items() if q in t), None)
        if other:
            ev["url"] = other
            continue
        bad.append({"field": ev.get("field"), "url": ev.get("url"), "why": "quote_not_found_in_any_fetched_page"})
    return bad


# ---------- A2: rule checks ----------
def vendor_tokens(app: dict) -> set[str]:
    text = f"{app.get('name', '')} {app.get('hint', '')}".lower()
    return set(re.findall(r"[a-z0-9]{4,}", text)) - STOP


def vendor_like(url: str, app: dict) -> bool:
    p = urlparse(clean_url(url))
    host = (p.netloc or "").lower()
    first = (p.path.strip("/").split("/") or [""])[0].lower()
    toks = vendor_tokens(app)
    if host.endswith("github.com"):
        return any(t in first for t in toks)       # github.com/<vendor-org>/...
    return any(t in host for t in toks)


def rule_checks(res: dict, app: dict) -> list[dict]:
    issues = []
    mcp_re = re.compile(r"\bmcp\b|model context protocol", re.I)
    ev_mcp = [e for e in res.get("evidence", []) if e.get("field") == "existing_mcp" and mcp_re.search(e.get("quote", ""))]
    claim = res.get("existing_mcp")
    if claim in ("official", "community") and not ev_mcp:
        issues.append({"field": "existing_mcp", "why": f"rule: '{claim}' MCP claimed but no evidence quote mentions MCP"})
    elif claim == "official" and not any(vendor_like(e.get("url", ""), app) for e in ev_mcp):
        issues.append({"field": "existing_mcp", "why": "rule: 'official' MCP claimed but the MCP evidence is not on a vendor-owned site"})
    if res.get("api_type") in ("cli_only", "none") and res.get("access") != "no_api":
        issues.append({"field": "access", "why": "rule: no web API (cli_only/none) so access should be no_api"})
    return issues


# ---------- A3: LLM judge ----------
def judge(res: dict) -> list[dict]:
    claims = {k: res.get(k) for k in ("auth_methods", "access", "api_type", "existing_mcp", "buildability")}
    prompt = (
        f"App: {res['name']}\nClaims: {json.dumps(claims)}\nEvidence: {json.dumps(res.get('evidence', []))}\n\n"
        "For each claim, decide if the evidence quotes actually support it. "
        'Return ONLY JSON: {"field_name": {"supported": true|false, "reason": "short"}} for each claim.'
    )
    raw = llm.complete("You are a strict fact-checker. Return JSON only.", prompt, JUDGE, max_tokens=2000)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        verdict = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return [{"field": "judge", "why": "judge_returned_unparseable_json"}]
    return [{"field": k, "why": v.get("reason", "")} for k, v in verdict.items()
            if isinstance(v, dict) and not v.get("supported", True)]


def all_issues(res: dict, app: dict) -> list[dict]:
    return literal_check(res, page_texts(res)) + rule_checks(res, app) + judge(res)


# ---------- A4: targeted re-research ----------
def re_research(app: dict, res: dict, issues: list[dict]) -> dict:
    import research as r1  # reuse the extractor
    extra_pages = {}
    for q in [f"{app['name']} API authentication OAuth API key",
              f"{app['name']} developer API access request pricing plan",
              f"{app['name']} official MCP server"]:
        for hit in ct.search(q, limit=2)[:2]:
            extra_pages[hit["url"]] = hit["text"] if len(hit["text"]) > 800 else ct.scrape(hit["url"])
    orig = {u: get_text(u) for u in res.get("pages_fetched", [])[:4]}
    pages = {**orig, **dict(list(extra_pages.items())[:4])}   # at most 8 pages: faster, fewer truncations
    pages = {u: t for u, t in pages.items() if t and len(t.strip()) >= 200 and not t.startswith("[FETCH FAILED")}
    TEXT_INDEX.update(pages)   # so the re-check finds this text without re-scraping
    hint_text = ("\nA previous attempt failed these checks: " + json.dumps(issues) +
                 "\nRe-read the text and fix ONLY what the text supports. Quotes must be verbatim." + RULES_HINT)
    new = r1.extract(app, pages, extra=hint_text).model_dump()
    new["id"] = res["id"]
    new["pages_fetched"] = list(pages.keys())
    new["re_researched"] = True
    return new


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated app ids, e.g. 81,35,2 (writes *_sample files)")
    a = ap.parse_args()

    apps = {int(x["id"]): x for x in csv.DictReader(open(ROOT / "data" / "apps.csv", encoding="utf-8"))}
    v1 = json.load(open(ROOT / "data" / "results_v1.json", encoding="utf-8"))
    if a.only:
        wanted = {int(x) for x in a.only.split(",")}
        v1 = [r for r in v1 if r["id"] in wanted]

    v2, report = [], []
    for res in v1:
        app = apps[res["id"]]
        if "error" in res:
            v2.append(res)
            report.append({"id": res["id"], "name": res["name"], "status": "error_carried_over"})
            print(f"[{res['id']:>3}] {res['name']}: error carried over")
            continue
        try:
            issues = all_issues(res, app)
        except Exception as e:  # noqa: BLE001
            res["needs_human"] = True
            v2.append(res)
            report.append({"id": res["id"], "name": res["name"], "status": f"check failed: {str(e)[:150]}"})
            print(f"[{res['id']:>3}] {res['name']}: check failed ({str(e)[:80]})")
            continue
        if issues:
            try:
                new = re_research(app, res, issues)
                issues_after = all_issues(new, app)
                final = new if len(issues_after) <= len(issues) else res
                final["verifier_flags_before"] = len(issues)
                final["verifier_flags_after"] = len(issues_after) if final is new else len(issues)
                if issues_after:
                    final["needs_human"] = True
                v2.append(final)
                report.append({"id": res["id"], "name": res["name"], "flags_before": len(issues),
                               "flags_after": len(issues_after), "issues": issues})
                print(f"[{res['id']:>3}] {res['name']}: {len(issues)} flags -> {len(issues_after)} after re-research", flush=True)
            except Exception as e:  # noqa: BLE001
                res["needs_human"] = True
                res["verifier_flags_before"] = len(issues)
                res["verifier_flags_after"] = len(issues)
                v2.append(res)
                report.append({"id": res["id"], "name": res["name"], "flags_before": len(issues), "flags_after": len(issues),
                               "issues": issues, "status": f"re-research failed: {str(e)[:150]}"})
                print(f"[{res['id']:>3}] {res['name']}: {len(issues)} flags; re-research failed ({str(e)[:80]})", flush=True)
        else:
            res["verifier_flags_before"] = 0
            v2.append(res)
            report.append({"id": res["id"], "name": res["name"], "flags_before": 0, "flags_after": 0})
            print(f"[{res['id']:>3}] {res['name']}: 0 flags", flush=True)

    suffix = "_sample" if a.only else ""
    (ROOT / "data" / f"results_v2{suffix}.json").write_text(json.dumps(v2, indent=2), encoding="utf-8")
    (ROOT / "data" / f"verify_report{suffix}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote results_v2{suffix}.json and verify_report{suffix}.json")