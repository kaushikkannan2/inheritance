"""Prove the graph is doing the work. No LLM needed.

  python scripts/prove_graph.py            # writes docs/proof.json

1. Integrity: the loaded graph has every node and edge type, with the expected counts.
2. Correctness: for every file in kubernetes/kubernetes, the graph's answer to "who approves
   this?" matches an independent walk over the raw OWNERS files on disk.
3. Depth: how far up the folder tree each file's answer lives (why this is a traversal).
4. Ablation: delete one relationship type at a time, on a copy of the graph, and re-ask the
   headline questions. Every answer depends on specific edges; remove them and it changes.
5. Grounding: the agent's check catches names that did not come from the graph.
6. Receipts: what FalkorDB does for each tool (queries, traversals, algorithms, time).
"""
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from inheritance import db, receipt, tools  # noqa: E402

ROOT = "data/raw/k8s"
EXPECT_NODES = ["Person", "Dir", "Team", "Subproject", "KEP"]
EXPECT_EDGES = ["CHILD_OF", "APPROVES", "REVIEWS", "EMERITUS_OF", "LEADS", "LED", "PART_OF", "OWNS",
                "OWNED_BY", "INVOLVES", "AUTHORED", "REVIEWED", "APPROVED", "REPLACES", "WORKED_WITH"]
ABLATE = ["CHILD_OF", "APPROVES", "EMERITUS_OF", "WORKED_WITH", "AUTHORED", "APPROVED", "LEADS", "OWNS"]
COPY = "inheritance_ablation"


def step(t):
    print(f"\n== {t}", flush=True)


# ------------------------------------------------------------------ 1. integrity

def integrity():
    nodes = dict(tools._q("MATCH (n) RETURN labels(n)[0], count(*)"))
    edges = dict(tools._q("MATCH ()-[r]->() RETURN type(r), count(*)"))
    on_disk = sum(1 for d, _, fs in os.walk(ROOT) if "OWNERS" in fs and ".git" not in d.split(os.sep))
    bad = {k: nodes.get(k) for k in EXPECT_NODES if not nodes.get(k)}
    if nodes.get("Dir") != on_disk:
        bad["Dir"] = f"{nodes.get('Dir')} in graph vs {on_disk} OWNERS files on disk"
    missing = [e for e in EXPECT_EDGES if not edges.get(e)]
    ok = not bad and not missing
    print(f"nodes {sum(nodes.values()):,}  edges {sum(edges.values()):,}  OWNERS files on disk {on_disk} = Dir nodes {nodes.get('Dir')}  "
          + ("OK" if ok else f"MISMATCH {bad} missing {missing}"))
    if not ok:
        sys.exit("Graph is incomplete. Run: python -m inheritance.load --reset")
    return dict(nodes=nodes, edges=edges, owners_files_on_disk=on_disk)


# ------------------------------------------------------------------ 2+3. correctness and depth

def _walker():
    """An independent reading of Kubernetes OWNERS semantics, straight from disk."""
    aliases = (yaml.safe_load(open(f"{ROOT}/OWNERS_ALIASES")) or {}).get("aliases", {})
    cache = {}

    def expand(lst):
        out = set()
        for x in lst or []:
            x = str(x).strip()
            if x not in aliases and re.search(r"-(approvers|reviewers|leads|maintainers|owners)$", x):
                continue  # an alias that is never defined names no one
            for y in aliases.get(x, [x]):
                out.add(str(y).strip().lstrip("@").lower())
        return out

    def owners(d):
        if d not in cache:
            f = os.path.join(ROOT, "" if d == "." else d, "OWNERS")
            cache[d] = None
            if os.path.exists(f):
                try:
                    y = yaml.safe_load(open(f)) or {}
                except yaml.YAMLError:
                    y = None
                if y is not None:
                    if "filters" in y:
                        appr = set().union(*[expand((v or {}).get("approvers")) for v in (y["filters"] or {}).values()] or [set()])
                    else:
                        appr = expand(y.get("approvers"))
                    cache[d] = (appr, bool((y.get("options") or {}).get("no_parent_owners")))
        return cache[d]

    def resolve(file_path):
        """(owners dir, approvers, folder hops, OWNERS files passed that list no approvers)."""
        d = os.path.dirname(file_path) or "."
        hops = skipped = 0
        while True:
            o = owners(d)
            if o:
                appr, stop = o
                if appr:
                    return d, appr, hops, skipped
                skipped += 1
                if stop:
                    return None, set(), hops, skipped
            if d == ".":
                return None, set(), hops, skipped
            d = os.path.dirname(d) or "."
            hops += 1
    return resolve


def correctness():
    files = subprocess.run(["git", "-C", ROOT, "ls-tree", "-r", "--name-only", "HEAD"],
                           capture_output=True, text=True, check=True).stdout.split()
    resolve = _walker()
    by_dir = defaultdict(list)
    for f in files:
        by_dir[os.path.dirname(f) or "."].append(f)
    agree, disagree, hops, through = 0, [], Counter(), 0
    t = time.perf_counter()
    for d, fs in by_dir.items():
        want_dir, want, h, skipped = resolve(fs[0])
        through += len(fs) if skipped else 0
        got = tools.who_approves(fs[0])["nearest_approvers"]
        got_set = set(got["approvers"]) if got else set()
        got_dir = got["owners_file"].replace("kubernetes/kubernetes/", "").rsplit("OWNERS", 1)[0].rstrip("/") or "." if got else None
        if got_set == want and got_dir == want_dir:
            agree += len(fs)
        else:
            disagree.append(dict(file=fs[0], files=len(fs), walker=[want_dir, sorted(want)[:5]], graph=[got_dir, sorted(got_set)[:5]]))
        hops[h] += len(fs)
    secs = time.perf_counter() - t
    total = len(files)
    print(f"{total:,} files in {len(by_dir):,} folders: {agree:,} agree ({agree / total:.2%}), "
          f"{sum(x['files'] for x in disagree):,} differ   [{secs:.0f}s]")
    for x in disagree[:5]:
        print("   differs:", x)
    up = sum(n for h, n in hops.items() if h > 0)
    print(f"depth: {hops[0]:,} files answered in their own folder, {up:,} ({up / total:.0%}) need to climb; "
          f"deepest climb {max(hops)} levels; {through:,} files sit under an OWNERS file with no approvers, "
          f"so their answer comes from walking CHILD_OF edges to a parent")
    return dict(files=total, folders=len(by_dir), agree=agree, agree_pct=round(agree / total * 100, 2),
                differ=disagree[:20], hops={str(k): v for k, v in sorted(hops.items())},
                need_climb=up, need_climb_pct=round(up / total * 100, 1), via_child_of=through, seconds=round(secs, 1))


# ------------------------------------------------------------------ 4. ablation

def fingerprint():
    """The headline answers from the demo, reduced to comparable values."""
    out = {}

    def run(name, fn):
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = f"ERROR: {type(e).__name__}"

    run("who approves kubelet code", lambda: (lambda r: [r["nearest_approvers"]["owners_file"] if r["nearest_approvers"] else None,
                                                          len((r["nearest_approvers"] or {}).get("approvers", []))])(
        tools.who_approves("pkg/kubelet/kuberuntime/kuberuntime_container.go")))
    run("who approves core API validation", lambda: (lambda r: [r["nearest_approvers"]["owners_file"] if r["nearest_approvers"] else None,
                                                                 len((r["nearest_approvers"] or {}).get("approvers", []))])(
        tools.who_approves("pkg/apis/core/validation/validation.go")))
    run("what lavalamp left", lambda: (lambda r: [r["areas"], r["decisions"]])(tools.departure_impact("lavalamp")))
    run("lavalamp's heirs", lambda: [h["heir"] for h in tools.heirs("lavalamp", 3)["heirs"]])
    run("if thockin left", lambda: (lambda r: [r["areas"], r["areas_with_no_local_approver"], r["areas_down_to_one"],
                                               r["decisions_left_with_nobody_active"], r["leadership_seats"],
                                               [(d["falls_back_to"] or {}).get("owners_file") for d in r["dark_areas"]]])(tools.departure_impact("thockin")))
    run("who can explain sidecars", lambda: (tools.why("sidecar containers", 1)["decisions"] or [{}])[0].get("can_still_explain"))
    run("single points of failure", lambda: (lambda r: [r["areas_with_one_active_approver"], r["largest"][0]["sub_areas"] if r["largest"] else None])(
        tools.single_points_of_failure(1)))
    run("orphaned decisions", lambda: tools.orphaned_knowledge(1)["with_nobody_active_left"])
    run("shadow teams", lambda: (lambda r: [r["communities"], (r["cross_team_groups"] or [{}])[0].get("size")])(tools.shadow_teams()))
    run("keystones", lambda: [k["handle"] for k in tools.keystone_people(3)["keystones"]])
    return out


def ablation():
    base = fingerprint()
    real_graph = tools.graph
    rows = {}
    try:
        tools.graph = lambda: db.graph(COPY)
        for rel in ABLATE:
            redis = db.graph().execute_command
            try:
                redis("GRAPH.DELETE", COPY)
            except Exception:
                pass
            redis("GRAPH.COPY", db.GRAPH, COPY)
            db.graph(COPY).query(f"MATCH ()-[r:{rel}]->() DELETE r", timeout=120000)
            fp = fingerprint()
            changed = [k for k in base if fp[k] != base[k]]
            rows[rel] = dict(changed=changed, answers=fp)
            print(f"without {rel:<12} {len(changed)}/{len(base)} answers change: {', '.join(changed) or '-'}")
    finally:
        tools.graph = real_graph
        try:
            db.graph().execute_command("GRAPH.DELETE", COPY)
        except Exception:
            pass
    return dict(baseline=base, without=rows, questions=list(base))


# ------------------------------------------------------------------ 5. grounding

def grounding():
    outs = [json.dumps(tools.call(n, a)) for n, a in [("whois", {"handle": "lavalamp"}), ("heirs", {"handle": "lavalamp", "top": 5})]]
    true = "lavalamp is emeritus on pkg/registry; deads2k and liggitt carry it. lavalamp authored KEP-4020."
    false = "lavalamp handed pkg/scheduler to bgrant0607 after KEP-9999."
    a, b = receipt.grounding(true, outs), receipt.grounding(false, outs)
    print(f"grounded answer: {a['score']} pass   invented answer: {b['score']}, flagged "
          f"{b['people']['not_from_graph'] + b['keps']['not_from_graph'] + b['paths']['not_from_graph']}")
    assert a["all_grounded"] and not b["all_grounded"]
    saved = []
    for f in sorted(os.listdir("out/receipts")) if os.path.isdir("out/receipts") else []:
        r = json.load(open(f"out/receipts/{f}"))
        saved.append(dict(question=r["question"], graph=r["graph"], score=r["grounding"]["score"],
                          queries=r["falkordb"]["queries"], flagged={k: r["grounding"][k]["not_from_graph"] for k in ("people", "keps", "paths")}))
    if saved:
        print(f"{len(saved)} saved agent receipts:")
        for s in saved:
            print(f"   {'graph' if s['graph'] else 'NO GRAPH'}  {s['score']:>6}  {s['question'][:70]}")
    return dict(fixture_true=a, fixture_false=b, agent_runs=saved)


# ------------------------------------------------------------------ 6. receipts per tool

def receipts():
    calls = [("who_approves", {"path": "pkg/kubelet/kuberuntime/kuberuntime_container.go"}),
             ("departure_impact", {"handle": "thockin"}),
             ("departure_impact", {"handle": "thockin", "also_leaving": ["liggitt", "deads2k"]}),
             ("heirs", {"handle": "lavalamp"}), ("why", {"topic": "sidecar containers"}),
             ("single_points_of_failure", {}), ("orphaned_knowledge", {}),
             ("shadow_teams", {}), ("keystone_people", {})]
    out = []
    for name, args in calls:
        start = len(tools.LEDGER)
        tools.call(name, args)
        led = tools.LEDGER[start:]
        k = Counter(x["kind"] for x in led)
        out.append(dict(tool=name, args=args, queries=len(led), by_kind=dict(k), rows=sum(x["rows"] for x in led),
                        ms=round(sum(x["ms"] for x in led), 1)))
        print(f"{name:<26} {len(led):>4} queries  {sum(x['ms'] for x in led):>7.0f} ms  {dict(k)}")
    return out


def incremental():
    """Load the graph as of Aug 20, replay every ownership PR since, compare with a fresh load."""
    from inheritance import load, sync
    load.load(reset=True, as_of="2026-08-20")
    applied = sync.sync(log=lambda *_: None)
    rep = sync.verify()
    ok = all(v["equal"] for v in rep.values())
    print(f"replayed {len(applied)} merged PRs from 2026-08-20; synced graph equals a fresh load: "
          + ("YES" if ok else "NO") + "  (" + ", ".join(f"{k} {v['rows']:,}" for k, v in rep.items()) + ")")
    return dict(prs=[dict(pr=a["pr"], date=a["date"], title=a["title"], folders=len(a["changes"])) for a in applied],
                checks={k: dict(rows=v["rows"], equal=v["equal"]) for k, v in rep.items()}, equal=ok)


if __name__ == "__main__":
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    step("0. keeping current: replay real ownership changes, compare with a fresh load"); res["incremental"] = incremental()
    step("1. integrity"); res["integrity"] = integrity()
    step("2-3. correctness against the raw OWNERS files, and depth"); res["correctness"] = correctness()
    step("4. ablation: remove one relationship type, re-ask the headline questions"); res["ablation"] = ablation()
    step("5. grounding check"); res["grounding"] = grounding()
    step("6. receipts: what FalkorDB does per tool"); res["receipts"] = receipts()
    os.makedirs("docs", exist_ok=True)
    json.dump(res, open("docs/proof.json", "w"), indent=2, default=str)
    print("\nwrote docs/proof.json")
