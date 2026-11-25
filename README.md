# API Research Agent — 100 Apps, Researched and Verified by an Agent

**Live case study:** 
**What this is:** An autonomous research agent built on the [Composio SDK](https://composio.dev) that researched 100 real-world apps across 10 categories — CRM, support, communications, marketing, ecommerce, data/SEO, dev tools, productivity, fintech, and AI/media — and answered, for each one: how do you authenticate, is access self-serve or gated, what's the API surface, does it already have an MCP server, and is it buildable as a Composio toolkit today?

The agent verifies its own work through a 4-stage loop and is checked against a 25-app hand-built gold set. Every claim is backed by a verbatim quote and source URL — nothing is answered from memory.

## The pipeline

```
1. Discover  → Composio Firecrawl search: docs, auth, pricing, MCP pages per app
2. Fetch     → Pages scraped to text, cached to disk for audit
3. Extract   → LLM fills a strict schema, answers only from text, quotes evidence
4. Verify    → Quote-match check, rule checks, independent model judges, re-research on failure
5. Gold check→ Human hand-checks a stratified 25-app sample
6. Analyze   → Accuracy + pattern clustering, page built from the data
```

## Stack

- **Composio SDK** — Firecrawl search/scrape toolkit for live web research
- **Google Gemini** (`google-genai`) — extraction + independent-judge verification
- **Pydantic** — strict schema with enums, so results can be clustered and measured
- **Plain HTML/CSS/JS** — the case study page itself, no build step, data-driven from a single embedded JSON block

## Setup

```bash
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # add COMPOSIO_API_KEY and GEMINI_API_KEY
```

## Run, in this order

```bash
python agent/composio_tools.py                         # 0. smoke test: one search + scrape must work
python agent/research.py --only Stripe,Slack,Notion     # 1. test on a few apps first
python agent/research.py                                # 2. all 100 apps (cached, safe to re-run)
cp data/results_v1.json data/results_v1_frozen.json     # 3. freeze v1 before verification
# 4. fill data/gold.csv by hand from real docs, BEFORE reading the agent's answers
python agent/verify.py                                  # 5. verification loop -> results_v2.json
python agent/analyze.py                                 # 6. v1 vs v2 accuracy + pattern clustering
python agent/build_site.py                               # 7. site/index.html + site/results.json
```

Deploy the `site/` folder (GitHub Pages, Netlify, or Vercel). Fill `data/human_log.md` and `data/limitations.json` honestly before deploying — the case study page surfaces both directly.

## What's honestly disclosed on the page

- **Human log** — every app that needed manual intervention, and why
- **Verification** — field-level accuracy against the hand-checked gold set, and the real before/after flag counts from the verification loop
- **Limitations** — including that the full re-research/verification loop was completed on a stratified sample rather than all 100 apps, due to LLM provider credit limits encountered during the run

## Project structure

```
agent/
  composio_tools.py   # Composio SDK wrapper (search, scrape, caching, timeouts)
  llm.py              # Gemini wrapper (retries, backoff, timeout handling)
  schema.py            # strict Pydantic schema for every app result
  research.py          # pass 1: discover -> fetch -> extract -> save
  verify.py            # pass 2: quote check, rule checks, judge, re-research
  analyze.py           # accuracy + pattern clustering
  build_site.py        # injects data into site/template.html
data/
  apps.csv             # the 100 researched apps
  gold.csv             # 25-app hand-verified ground truth
  human_log.md         # honest log of manual interventions
  limitations.json     # honest disclosure of known gaps
site/
  template.html        # the case study page (data-driven, no build step)
```

## Notes

- Fallback plain-HTTP scrapes (when Composio's Firecrawl tool returns too little text) are counted and disclosed on the page.
- Cost: roughly 3 searches + up to 6 scrapes + 1 LLM call per app researched, plus the verification loop. Check your Composio and LLM provider balance before a full run.
