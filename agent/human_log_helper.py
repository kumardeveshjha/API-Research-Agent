"""One-off helper: prints a markdown table of every app that still needs a human look.
Usage: python agent/human_log_helper.py
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
r = json.load(open(ROOT / "data" / "results_v1.json", encoding="utf-8"))
flagged = [x for x in r if x.get("needs_human") or "error" in x]

for x in flagged:
    reason = x.get("error", "") or f"{x.get('verifier_flags_after', '?')} unresolved verifier flags"
    reason = reason.replace("\n", " ")[:80]
    print(f"| {x['name']} | {reason} | | |")

print(f"\n({len(flagged)} apps need a human look)")