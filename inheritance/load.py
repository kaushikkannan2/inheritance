"""Load the Kubernetes project's public governance data into FalkorDB.

Sources (all public, Apache 2.0):
  kubernetes/kubernetes   every OWNERS file + OWNERS_ALIASES  -> who may approve which code
  kubernetes/community    sigs.yaml                          -> teams, leads, subprojects
  kubernetes/enhancements keps/**/kep.yaml                   -> design decisions and who made them

People are GitHub handles only. Names, employers and emails in sigs.yaml are never loaded.

  python -m inheritance.load --reset
"""
import argparse
import glob
import os
import re
from collections import defaultdict
from itertools import combinations

import yaml

from .db import graph

RAW = "data/raw"
BATCH = 2000


def _handle(x):
    h = str(x).strip().lstrip("@").lower() if x else ""
    return h if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,38}", h) else None  # GitHub handle rules; drops free text


def _batches(rows):
    for i in range(0, len(rows), BATCH):
        yield rows[i:i + BATCH]


# ------------------------------------------------------------------ parse

def parse_owners(root=f"{RAW}/k8s"):
    aliases = (yaml.safe_load(open(f"{root}/OWNERS_ALIASES")) or {}).get("aliases", {})

    def expand(lst):
        out = []
        for x in lst or []:
            x = str(x).strip()
            out += [_handle(y) for y in aliases[x]] if x in aliases else [_handle(x)]
        return [o for o in out if o]

    dirs = {}
    for p in glob.glob(f"{root}/**/OWNERS", recursive=True):
        try:
            d = yaml.safe_load(open(p)) or {}
        except yaml.YAMLError:
            continue
        if "filters" in d:  # per-file-pattern sections: union them
            appr, rev = [], []
            for f in (d.get("filters") or {}).values():
                appr += expand((f or {}).get("approvers"))
                rev += expand((f or {}).get("reviewers"))
        else:
            appr, rev = expand(d.get("approvers")), expand(d.get("reviewers"))
        path = os.path.dirname(p)[len(root):].lstrip("/") or "."
        dirs[path] = dict(
            approvers=sorted(set(appr)), reviewers=sorted(set(rev) - set(appr)),
            emeritus=sorted(set(expand(d.get("emeritus_approvers")) + expand(d.get("emeritus_reviewers")))),
            no_parent=bool((d.get("options") or {}).get("no_parent_owners")),
            labels=[str(l) for l in d.get("labels") or []])
    # parent = nearest ancestor directory that has its own OWNERS file
    for path in dirs:
        parent, cur = None, path
        while cur not in (".", ""):
            cur = os.path.dirname(cur) or "."
            if cur in dirs:
                parent = cur
                break
        dirs[path]["parent"] = parent
    return dirs


def parse_sigs(path=f"{RAW}/sigs.yaml"):
    s = yaml.safe_load(open(path))
    teams = []
    for kind in ("sigs", "workinggroups", "committees", "usergroups"):
        for g in s.get(kind) or []:
            lead = g.get("leadership") or {}
            roles = []
            for role in ("chairs", "tech_leads"):
                roles += [(_handle(p.get("github")), role[:-1]) for p in lead.get(role) or []]
            former = [_handle(p.get("github")) for p in lead.get("emeritus_leads") or []]
            subs = []
            for sp in g.get("subprojects") or []:
                owns = []
                for url in sp.get("owners") or []:
                    m = re.match(r"https://raw\.githubusercontent\.com/kubernetes/kubernetes/[^/]+/(.*)OWNERS$", url)
                    if m:
                        owns.append(m.group(1).rstrip("/") or ".")
                subs.append(dict(name=sp["name"], owns=owns))
            teams.append(dict(id=g["dir"], name=g["name"], kind=kind.rstrip("s"),
                              leads=[r for r in roles if r[0]], former=[f for f in former if f], subprojects=subs))
    return teams


def parse_keps(root=f"{RAW}/enhancements/keps"):
    keps = []
    for p in glob.glob(f"{root}/**/kep.yaml", recursive=True):
        try:
            d = yaml.safe_load(open(p))
        except (yaml.YAMLError, ValueError):  # a few files carry impossible dates
            continue
        if not isinstance(d, dict) or not d.get("title") or "NNNN" in p:
            continue
        m = re.search(r"/(\d+)-[^/]+/kep\.yaml$", p)
        num = d.get("kep-number") or (m and m.group(1))
        if not num:
            continue
        def ref(x):
            out = []
            for r in x if isinstance(x, list) else ([x] if x else []):
                mm = re.search(r"(\d{2,5})-", str(r))
                if mm:
                    out.append(str(int(mm.group(1))))
            return out
        keps.append(dict(
            number=str(int(num)), title=str(d["title"]).strip(), status=str(d.get("status") or "unknown").split("|")[0],
            stage=str(d.get("stage") or ""), milestone=str(d.get("latest-milestone") or ""),
            created=str(d.get("creation-date") or ""), owning=str(d.get("owning-sig") or ""),
            participating=[str(x) for x in d.get("participating-sigs") or []],
            authors=sorted({h for h in map(_handle, d.get("authors") or []) if h}),
            reviewers=sorted({h for h in map(_handle, d.get("reviewers") or []) if h}),
            approvers=sorted({h for h in map(_handle, d.get("approvers") or []) if h}),
            replaces=ref(d.get("replaces")), path=p.split("keps/", 1)[1].rsplit("/", 1)[0]))
    return keps


# ------------------------------------------------------------------ load

def load(reset=False):
    g = graph()
    if reset:
        try:
            g.delete()
        except Exception:
            pass
        g = graph()
    for label, prop in [("Person", "handle"), ("Dir", "path"), ("Team", "id"), ("KEP", "number"), ("Subproject", "id")]:
        try:
            g.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
        except Exception as e:
            if "already indexed" not in str(e):
                raise
    dirs, teams, keps = parse_owners(), parse_sigs(), parse_keps()

    # who is still around: anyone currently approving/reviewing code or leading a team
    active = {h for d in dirs.values() for h in d["approvers"] + d["reviewers"]}
    active |= {h for t in teams for h, _ in t["leads"]}
    marked = {h for d in dirs.values() for h in d["emeritus"]} | {h for t in teams for h in t["former"]}
    people = active | marked | {h for k in keps for h in k["authors"] + k["reviewers"] + k["approvers"]}

    def status(h):
        return "active" if h in active else ("emeritus" if h in marked else "unlisted")

    g.query("UNWIND $rows AS r MERGE (p:Person {handle:r.h}) SET p.status = r.s",
            {"rows": [dict(h=h, s=status(h)) for h in sorted(people)]})
    g.query("""UNWIND $rows AS r MERGE (d:Dir {path:r.path})
               SET d.no_parent_owners = r.no_parent, d.labels = r.labels, d.owners_file = r.file""",
            {"rows": [dict(path=p, no_parent=d["no_parent"], labels=d["labels"],
                           file=("" if p == "." else p + "/") + "OWNERS") for p, d in dirs.items()]})
    g.query("""UNWIND $rows AS r MATCH (c:Dir {path:r.c}), (p:Dir {path:r.p}) CREATE (c)-[:CHILD_OF]->(p)""",
            {"rows": [dict(c=p, p=d["parent"]) for p, d in dirs.items() if d["parent"]]})
    for rel, key in (("APPROVES", "approvers"), ("REVIEWS", "reviewers"), ("EMERITUS_OF", "emeritus")):
        rows = [dict(h=h, p=p) for p, d in dirs.items() for h in d[key]]
        for b in _batches(rows):
            g.query(f"""UNWIND $rows AS r MATCH (h:Person {{handle:r.h}}), (d:Dir {{path:r.p}})
                        CREATE (h)-[:{rel}]->(d)""", {"rows": b})

    g.query("UNWIND $rows AS r MERGE (t:Team {id:r.id}) SET t.name = r.name, t.kind = r.kind",
            {"rows": [dict(id=t["id"], name=t["name"], kind=t["kind"]) for t in teams]})
    g.query("""UNWIND $rows AS r MATCH (p:Person {handle:r.h}), (t:Team {id:r.t}) CREATE (p)-[:LEADS {role:r.role}]->(t)""",
            {"rows": [dict(h=h, t=t["id"], role=role) for t in teams for h, role in t["leads"]]})
    g.query("""UNWIND $rows AS r MATCH (p:Person {handle:r.h}), (t:Team {id:r.t}) CREATE (p)-[:LED]->(t)""",
            {"rows": [dict(h=h, t=t["id"]) for t in teams for h in t["former"]]})
    subs = [dict(id=f"{t['id']}/{s['name']}", name=s["name"], t=t["id"], owns=s["owns"]) for t in teams for s in t["subprojects"]]
    g.query("""UNWIND $rows AS r MATCH (t:Team {id:r.t})
               MERGE (s:Subproject {id:r.id}) SET s.name = r.name
               CREATE (s)-[:PART_OF]->(t)""", {"rows": subs})
    g.query("""UNWIND $rows AS r MATCH (s:Subproject {id:r.id}) UNWIND r.owns AS path
               MATCH (d:Dir {path:path}) CREATE (s)-[:OWNS]->(d)""", {"rows": subs})

    g.query("""UNWIND $rows AS r MERGE (k:KEP {number:r.number})
               SET k.title = r.title, k.status = r.status, k.stage = r.stage, k.milestone = r.milestone,
                   k.created = r.created, k.path = r.path""",
            {"rows": [{k: v for k, v in kp.items() if k in ("number", "title", "status", "stage", "milestone", "created", "path")} for kp in keps]})
    g.query("""UNWIND $rows AS r MATCH (k:KEP {number:r.n}), (t:Team {id:r.t}) CREATE (k)-[:OWNED_BY]->(t)""",
            {"rows": [dict(n=k["number"], t=k["owning"]) for k in keps if k["owning"]]})
    g.query("""UNWIND $rows AS r MATCH (k:KEP {number:r.n}), (t:Team {id:r.t}) CREATE (k)-[:INVOLVES]->(t)""",
            {"rows": [dict(n=k["number"], t=t) for k in keps for t in k["participating"] if t != k["owning"]]})
    for rel, key in (("AUTHORED", "authors"), ("REVIEWED", "reviewers"), ("APPROVED", "approvers")):
        rows = [dict(h=h, n=k["number"]) for k in keps for h in k[key]]
        for b in _batches(rows):
            g.query(f"""UNWIND $rows AS r MATCH (h:Person {{handle:r.h}}), (k:KEP {{number:r.n}})
                        CREATE (h)-[:{rel}]->(k)""", {"rows": b})
    g.query("""UNWIND $rows AS r MATCH (a:KEP {number:r.a}), (b:KEP {number:r.b}) CREATE (a)-[:REPLACES]->(b)""",
            {"rows": [dict(a=k["number"], b=r) for k in keps for r in k["replaces"] if r != k["number"]]})

    # WORKED_WITH: people who share code ownership or decisions. This is where knowledge
    # lives between people, and what the heir search and community detection run on.
    w = defaultdict(float)
    for d in dirs.values():
        crew = set(d["approvers"]) | set(d["reviewers"]) | set(d["emeritus"])
        for a, b in combinations(sorted(crew), 2):
            w[(a, b)] += 1.0
    for k in keps:
        crew = set(k["authors"]) | set(k["approvers"]) | set(k["reviewers"])
        for a, b in combinations(sorted(crew), 2):
            w[(a, b)] += 0.5
    rows = [dict(a=a, b=b, w=v) for (a, b), v in w.items()]
    for b in _batches(rows):
        g.query("""UNWIND $rows AS r MATCH (a:Person {handle:r.a}), (b:Person {handle:r.b})
                   CREATE (a)-[:WORKED_WITH {weight:r.w}]->(b)""", {"rows": b})
    try:
        g.query("CALL db.idx.fulltext.createNodeIndex('KEP', 'title')")
    except Exception as e:
        if "already" not in str(e).lower():
            raise
    return dict(dirs=len(dirs), people=len(people), active=len(active), departed=len(marked - active),
                teams=len(teams), subprojects=len(subs), keps=len(keps), worked_with=len(rows))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    print(load(ap.parse_args().reset))
