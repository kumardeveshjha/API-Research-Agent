
import json
import pathlib

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
D = ROOT / "data"
FIELDS = ["auth_methods", "access", "api_type", "existing_mcp", "buildability"]


def load(name):
    p = D / name
    return json.load(open(p)) if p.exists() else []


def val(x, f):
    if f == "auth_methods":
        return frozenset(x) if isinstance(x, list) else frozenset(str(x or "").split("|")) - {""}
    return str(x or "").strip()


def accuracy(results, gold):
    by_id = {r["id"]: r for r in results if "error" not in r}
    per = {f: [0, 0] for f in FIELDS}
    misses = []
    for _, g in gold.iterrows():
        r = by_id.get(int(g["id"]))
        for f in FIELDS:
            if pd.isna(g[f]) or str(g[f]).strip() == "":
                continue
            per[f][1] += 1
            if r is None:
                misses.append({"app": g["name"], "field": f, "gold": str(g[f]), "agent": "ERROR/none"})
                continue
            ok = val(r[f], f) == val(str(g[f]), f)
            per[f][0] += ok
            if not ok:
                got = sorted(r[f]) if isinstance(r[f], list) else r[f]
                misses.append({"app": g["name"], "field": f, "gold": str(g[f]), "agent": got})
    tot_ok = sum(v[0] for v in per.values())
    tot = sum(v[1] for v in per.values())
    return {"per_field": {f: {"correct": c, "total": t, "pct": round(100 * c / t, 1) if t else None}
                          for f, (c, t) in per.items()},
            "overall": {"correct": tot_ok, "total": tot, "pct": round(100 * tot_ok / tot, 1) if tot else None},
            "misses": misses}


def patterns(results):
    df = pd.DataFrame([r for r in results if "error" not in r])
    n = len(df)
    auth = df.explode("auth_methods")["auth_methods"].value_counts().to_dict()
    p = {
        "n_analyzed": n,
        "n_errors": len(results) - n,
        "auth_counts": auth,
        "auth_pct_of_apps": {k: round(100 * v / n, 1) for k, v in auth.items()},
        "access_counts": df["access"].value_counts().to_dict(),
        "access_by_category": pd.crosstab(df["category"], df["access"]).to_dict("index"),
        "auth_by_category": df.explode("auth_methods").pipe(
            lambda d: pd.crosstab(d["category"], d["auth_methods"])).to_dict("index"),
        "blockers": df["main_blocker"].value_counts().to_dict(),
        "buildability": df["buildability"].value_counts().to_dict(),
        "mcp": df["existing_mcp"].value_counts().to_dict(),
        "easy_wins": df[(df["access"].isin(["self_serve_free", "trial"])) & (df["api_type"].isin(["rest", "graphql", "both"]))
                        & (df["buildability"] == "ready")]["name"].tolist(),
        "needs_outreach": df[df["access"].isin(["partner_gated", "approval_required"])]["name"].tolist(),
        "not_buildable": df[(df["buildability"] == "blocked") | (df["api_type"].isin(["cli_only", "none"]))]["name"].tolist(),
        "needs_human_flagged": df[df.get("needs_human", False) == True]["name"].tolist(),  # noqa: E712
        "low_confidence": df[df["confidence"] == "low"]["name"].tolist(),
    }
    return p


if __name__ == "__main__":
    v1, v2 = load("results_v1.json"), load("results_v2.json")
    gold = pd.read_csv(D / "gold.csv")
    acc = {"v1": accuracy(v1, gold) if v1 else None, "v2": accuracy(v2, gold) if v2 else None}
    (D / "accuracy.json").write_text(json.dumps(acc, indent=2, default=str))
    final = v2 or v1
    (D / "patterns.json").write_text(json.dumps(patterns(final), indent=2))
    for k in ("v1", "v2"):
        if acc[k]:
            print(k, "overall:", acc[k]["overall"])
            for f, s in acc[k]["per_field"].items():
                print("   ", f, s)
    print("Wrote accuracy.json and patterns.json")
