# Where a human was needed (fill this in AS you go, be honest)

## Verification loop — demonstrated on a 24-app sample

Gemini's prepaid credits were exhausted partway through the full 100-app verification
pass (judge model hit a `402 RESOURCE_EXHAUSTED` error). The verification loop (literal
quote-check, rule checks, independent LLM judge, targeted re-research) was completed for
a stratified sample before credits ran out. Real before/after flag counts:

| App | Flags before | Flags after | Note |
|---|---|---|---|
| Twilio | 6 | 1 | Re-research recovered most evidence |
| Zoho Cliq | 3 | 2 | Partial improvement |
| Lark (Larksuite) | 5 | 4 | Partial improvement |
| Aircall | 2 | 1 | Improved |
| Sherlock | 6 | 5 | Minor improvement (CLI tool, no API) |
| Mermaid CLI | 7 | 5 | Minor improvement (CLI tool, no API) |
| Pumble | 3 | 3 | No change |
| Telegram | 3 | 3 | No change |
| WhatsApp Business | 3 | 3 | No change |
| Stripe | 4 | 4 | No change — evidence gap persisted |
| Mailchimp | 4 | 4 | No change |
| Discord | 6 | — | Re-research failed (response truncated) |
| Vonage | 3 | — | Re-research failed (response truncated) |
| Google Ads | 4 | — | Re-research failed (response truncated) |

The remaining ~76 apps retain first-pass (v1) research only, honestly flagged where
`needs_human=true`. Full-scale v1→v2 accuracy against the gold set could not be computed
for all 100 apps due to this credit exhaustion — see `limitations.json`.
| App | What failed | What I did by hand | Time |
|---|---|---|---|
| (example) fanbasis | Scraper found no API docs | Searched manually, confirmed no public API | 5 min |
