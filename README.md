# API Research Agent (Composio take-home)

An agent that researches 100 apps (auth, self-serve vs gated, API surface, MCP, buildability) using the **Composio SDK**
(Firecrawl search/scrape tools), extracts answers into a strict schema, verifies itself, and publishes a one-page case study.

## Pipeline
`research.py` (discover > fetch > extract) > `verify.py` (quote check, second-model judge, targeted re-research)
> human gold set (`data/gold.csv`) > `analyze.py` (patterns + accuracy) > `build_site.py` (site/index.html)

## Setup
```bash
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # add COMPOSIO_API_KEY and ANTHROPIC_API_KEY
```

## Run, in this order
```bash
python agent/composio_tools.py                          # 0. smoke test: ONE Composio search + scrape must work first
python agent/research.py --only Stripe,Slack,Notion,Sherlock,fanbasis   # 1. test on 5 apps, fix bugs
python agent/research.py                                # 2. all 100 (cached, safe to re-run)
cp data/results_v1.json data/results_v1_frozen.json     # 3. freeze v1 BEFORE verification
# 4. fill data/gold.csv by hand from the real docs, BEFORE reading the agent's answers
python agent/verify.py                                  # 5. loops -> results_v2.json
python agent/analyze.py                                 # 6. v1 vs v2 accuracy + patterns
python agent/build_site.py                              # 7. site/index.html + site/results.json
```
Deploy the `site/` folder (GitHub Pages / Netlify / Vercel). Edit `data/human_log.md` and `data/limitations.json` honestly first.

## Notes
- If `composio_tools.py` errors, only that file needs fixing (SDK calls / tool slugs): check docs.composio.dev quickstart.
- Fallback plain-HTTP scrapes are counted and should be disclosed on the page.
- Gold values: `auth_methods` separated with `|`; other fields use the enum values in `agent/schema.py`.
- Cost: roughly 3 searches + up to 6 scrapes + 1 LLM call per app, plus the verifier. Check your Composio balance before the full run.
