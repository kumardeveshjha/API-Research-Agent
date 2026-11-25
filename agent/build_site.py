"""Builds site/index.html by injecting REAL data into site/template.html. Run after analyze.py.
Nothing on the page is hand-typed: every number comes from data/*.json.
"""
import json
import pathlib
import shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent
D = ROOT / "data"


def j(name, default):
    p = D / name
    return json.load(open(p)) if p.exists() else default


payload = {
    "results": j("results_v2.json", j("results_v1.json", [])),
    "patterns": j("patterns.json", {}),
    "accuracy": j("accuracy.json", {}),
    "verify_report": j("verify_report.json", []),
    "limitations": j("limitations.json", {"items": []})["items"],
    "human_log_md": (D / "human_log.md").read_text() if (D / "human_log.md").exists() else "",
    "repo_url": "https://github.com/YOUR_USERNAME/api-research-agent",
}
tpl = (ROOT / "site" / "template.html").read_text(encoding="utf-8")
safe = json.dumps(payload).replace("</", "<\\/")
(ROOT / "site" / "index.html").write_text(tpl.replace("__DATA__", safe), encoding="utf-8")
shutil.copy(D / "results_v2.json" if (D / "results_v2.json").exists() else D / "results_v1.json",
            ROOT / "site" / "results.json")
print("Built site/index.html and site/results.json")
