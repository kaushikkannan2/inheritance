"""Load the Kubernetes project's public governance data into FalkorDB.

Sources (all public, Apache 2.0):
  kubernetes/kubernetes   every OWNERS file + OWNERS_ALIASES  -> who may approve which code
  kubernetes/community    sigs.yaml                          -> teams, leads, subprojects
  kubernetes/enhancements keps/**/kep.yaml                   -> design decisions and who made them
  kubernetes/enhancements keps/**/README.md                  -> the design documents: summary, motivation, alternatives

People are GitHub handles only. Names, employers and emails in sigs.yaml are never loaded.

  python -m inheritance.load --reset
  python -m inheritance.load --reset --as-of 2026-08-20   # then: python -m inheritance.sync
"""
import argparse
import glob
import os
import re
import subprocess
from collections import defaultdict
from itertools import combinations

import yaml

from .db import graph

RAW = "data/raw"
BATCH = 2000


def _handle(x):
    h = str(x).strip().lstrip("@").lower() if x else ""
    if re.search(r"-(approvers|reviewers|leads|maintainers|owners)$", h):
        return None  # an alias name that was never defined, not a person
    return h if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,38}", h) else None  # GitHub handle rules; drops free text


def _batches(rows):
    for i in range(0, len(rows), BATCH):
        yield rows[i:i + BATCH]


# ------------------------------------------------------------------ parse

def parse_aliases(text):
    return (yaml.safe_load(text or "") or {}).get("aliases", {}) or {}


def parse_owners_text(text, aliases):
    """One OWNERS file -> who approves, reviews and stepped away. None if unparseable."""
    def expand(lst):
        out = []
        for x in lst or []:
            x = str(x).strip()
            out += [_handle(y) for y in aliases[x]] if x in aliases else [_handle(x)]
        return [o for o in out if o]
    try:
        d = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(d, dict):
        return None
    if "filters" in d:  # per-file-pattern sections: union them
        appr, rev = [], []
        for f in (d.get("filters") or {}).values():
            appr += expand((f or {}).get("approvers"))
            rev += expand((f or {}).get("reviewers"))
    else:
        appr, rev = expand(d.get("approvers")), expand(d.get("reviewers"))
    return dict(
        approvers=sorted(set(appr)), reviewers=sorted(set(rev) - set(appr)),
        emeritus=sorted(set(expand(d.get("emeritus_approvers")) + expand(d.get("emeritus_reviewers")))),
        no_parent=bool((d.get("options") or {}).get("no_parent_owners")),
        labels=[str(l) for l in d.get("labels") or []])


def nearest_parent(path, existing):
    """The nearest ancestor folder that has its own OWNERS file."""
    cur = path
    while cur not in (".", ""):
        cur = os.path.dirname(cur) or "."
        if cur in existing:
            return cur
    return None


def parse_owners(root=f"{RAW}/k8s"):
    aliases = parse_aliases(open(f"{root}/OWNERS_ALIASES").read())
    dirs = {}
    files = [os.path.join(d, "OWNERS") for d, sub, fs in os.walk(root) if "OWNERS" in fs and ".git" not in d.split(os.sep)]
    for p in files:  # os.walk, not glob: glob skips hidden folders such as .github
        spec = parse_owners_text(open(p).read(), aliases)
        if spec is None:
            continue
        dirs[os.path.dirname(p)[len(root):].lstrip("/") or "."] = spec
    for path in dirs:
        dirs[path]["parent"] = nearest_parent(path, dirs)
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


def _section(md, names, limit=1500):
    """Text of the first '## <name>' section (including its ### subsections), cleaned and trimmed."""
    for name in names:
        m = re.search(rf"^##\s+{name}\b.*?$(.*?)(?=^##\s|\Z)", md, re.M | re.S | re.I)
        if m:
            t = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S)
            t = re.sub(r"\n{3,}", "\n\n", t).strip()
            if t:
                return t[:limit] + ("…" if len(t) > limit else "")
    return ""


def parse_docs(root=f"{RAW}/enhancements/keps"):
    """Each KEP's design document (README.md): the summary, the motivation and the
    alternatives considered. This is the 'why' behind a decision, in the authors' words."""
    docs = []
    for p in glob.glob(f"{root}/**/README.md", recursive=True):
        if "NNNN" in p or not os.path.exists(os.path.join(os.path.dirname(p), "kep.yaml")):
            continue
        m = re.search(r"/(\d+)-[^/]+/README\.md$", p)
        if not m:
            continue
        md = open(p, errors="replace").read()
        docs.append(dict(kep=str(int(m.group(1))), path=p.split("keps/", 1)[1],
                         summary=_section(md, ["Summary"]), motivation=_section(md, ["Motivation"]),
                         alternatives=_section(md, ["Alternatives", "Alternatives Considered"])))
    return docs


# ------------------------------------------------------------------ load

def _git(*args, root=f"{RAW}/k8s"):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=True).stdout.strip()


def load(reset=False, name=None, as_of=None):
    """Build the graph. as_of (YYYY-MM-DD) loads the OWNERS files as they were on that date;
    inheritance.sync can then replay every ownership change since."""
    if as_of:
        here = _git("rev-parse", "HEAD")
        rev = _git("rev-list", "-1", "--first-parent", f"--before={as_of}T23:59:59Z", here)
        if not rev:
            raise SystemExit(f"No commit on or before {as_of} in data/raw/k8s; fetch more history (scripts/fetch_data.sh)")
        _git("checkout", "-q", rev)
        try:
            return load(reset, name)
        finally:
            _git("checkout", "-q", here)
    g = graph(name) if name else graph()
    if reset:
        try:
            g.delete()
        except Exception:
            pass
        g = graph(name) if name else graph()
    for label, prop in [("Person", "handle"), ("Dir", "path"), ("Team", "id"), ("KEP", "number"), ("Subproject", "id"), ("Change", "sha")]:
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
    # design documents: the reasoning behind each decision
    keps_sha = _git("rev-parse", "HEAD", root=f"{RAW}/enhancements")
    docs = [d for d in parse_docs() if d["summary"] or d["motivation"]]
    for b in _batches(docs):
        g.query("""UNWIND $rows AS r MATCH (k:KEP {number:r.kep})
                   CREATE (d:Document {kep:r.kep, path:r.path, summary:r.summary, motivation:r.motivation,
                                       alternatives:r.alternatives, url:r.url})-[:DESCRIBES]->(k)""",
                {"rows": [dict(d, url=f"https://github.com/kubernetes/enhancements/blob/{keps_sha[:12]}/keps/{d['path']}") for d in b]})
    try:
        g.query("CALL db.idx.fulltext.createNodeIndex('Document', 'summary', 'motivation', 'alternatives')")
    except Exception as e:
        if "already" not in str(e).lower():
            raise
    sha = _git("rev-parse", "HEAD")
    g.query("MERGE (s:Source {repo:'kubernetes/kubernetes'}) SET s.sha = $sha, s.date = $date, s.loaded = $date",
            {"sha": sha, "date": _git("log", "-1", "--format=%cs", sha)})
    try:
        g.query("CALL db.idx.fulltext.createNodeIndex('KEP', 'title')")
    except Exception as e:
        if "already" not in str(e).lower():
            raise
    return dict(dirs=len(dirs), people=len(people), active=len(active), departed=len(marked - active),
                teams=len(teams), subprojects=len(subs), keps=len(keps), documents=len(docs), worked_with=len(rows))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--as-of", help="load OWNERS files as they were on this date (YYYY-MM-DD)")
    a = ap.parse_args()
    print(load(a.reset, as_of=a.as_of))
