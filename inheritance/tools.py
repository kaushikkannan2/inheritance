"""Inheritance's toolbox: fixed Cypher and FalkorDB algorithms over the Kubernetes org graph.

Every answer carries provenance (the OWNERS file or KEP it came from). The brain answers
FROM people's public record, with attribution; it never answers AS them.
"""
import os
import re
import subprocess
import threading
import time
import uuid
from collections import defaultdict

from .db import graph

MAX_UP = 25            # deepest OWNERS chain to climb
_proj_lock = threading.Lock()
POLICY = ("Answers come from public project records and are attributed to the people in them. "
          "Inheritance never imitates a person or speaks in their voice.")


class ToolError(Exception):
    """Bad arguments; the message goes back to the model so it can retry."""


LEDGER = []   # every query this process sent to FalkorDB; the agent turns it into a receipt


def _kind(query):
    q = " ".join(query.split())
    if "CALL algo." in q:
        return "algorithm"
    if "db.idx.fulltext" in q:
        return "fulltext"
    if re.search(r"\[[^\]]*\*[^\]]*\]", q):
        return "traversal"          # variable-length path, e.g. -[:CHILD_OF*0..25]->
    if re.search(r"\b(CREATE|DELETE|SET)\b", q):
        return "write"
    return "match"


def _q(query, params=None, timeout=60000):
    t = time.perf_counter()
    rows = graph().query(query, params or {}, timeout=timeout).result_set
    LEDGER.append(dict(kind=_kind(query), rows=len(rows), ms=round((time.perf_counter() - t) * 1000, 2),
                       cypher=" ".join(query.split())[:160]))
    return rows


def _person(handle):
    h = (handle or "").strip().lstrip("@").lower()
    r = _q("MATCH (p:Person {handle:$h}) RETURN p.handle, p.status", {"h": h})
    if not r:
        near = [x[0] for x in _q("MATCH (p:Person) WHERE p.handle CONTAINS $h RETURN p.handle LIMIT 8", {"h": h[:6]})]
        raise ToolError(f"Unknown entity: no person '{handle}' in the Kubernetes records. Similar handles: {near or 'none'}")
    return r[0][0], r[0][1]


def _src_dir(path):
    return f"kubernetes/kubernetes/{'' if path == '.' else path + '/'}OWNERS"


def _src_kep(path):
    return f"kubernetes/enhancements/keps/{path}/kep.yaml"


def _nearest_dir(path):
    p = (path or "").strip().strip("/") or "."
    r = _q("""MATCH (d:Dir) WHERE d.path = $p OR $p STARTS WITH (d.path + '/') OR d.path = '.'
              RETURN d.path ORDER BY size(d.path) DESC LIMIT 1""", {"p": p})
    return r[0][0]


def _chain(dpath):
    """The OWNERS files that govern a directory: itself, then ancestors, stopping after
    the first one that sets no_parent_owners."""
    rows = _q(f"""MATCH p = (d:Dir {{path:$p}})-[:CHILD_OF*0..{MAX_UP}]->(a:Dir)
                  RETURN a.path, a.no_parent_owners, length(p) ORDER BY length(p)""", {"p": dpath})
    out = []
    for path, nop, _ in rows:
        out.append(path)
        if nop:
            break
    return out


def _approvers(paths):
    rows = _q("""MATCH (x:Person)-[r:APPROVES|REVIEWS|EMERITUS_OF]->(d:Dir) WHERE d.path IN $ps
                 RETURN d.path, type(r), x.handle, x.status""", {"ps": paths})
    by = defaultdict(lambda: defaultdict(list))
    for path, rel, h, st in rows:
        by[path][rel].append((h, st))
    return by


# ------------------------------------------------------------------ lookup

def find_person(query: str):
    """Look up GitHub handles in the project records (validation before other tools)."""
    q = (query or "").strip().lstrip("@").lower()
    if len(q) < 2:
        raise ToolError("query must be at least 2 characters")
    rows = _q("MATCH (p:Person) WHERE p.handle CONTAINS $q RETURN p.handle, p.status ORDER BY size(p.handle) LIMIT 10", {"q": q})
    return [dict(handle=h, status=s) for h, s in rows] or {"message": f"No handle matching '{query}'."}


def whois(handle: str):
    """What a person owns, leads and decided, and whether they are still active."""
    h, status = _person(handle)
    approves = [r[0] for r in _q("MATCH (:Person {handle:$h})-[:APPROVES]->(d) RETURN d.path ORDER BY d.path", {"h": h})]
    emer = [r[0] for r in _q("MATCH (:Person {handle:$h})-[:EMERITUS_OF]->(d) RETURN d.path ORDER BY d.path", {"h": h})]
    leads = _q("MATCH (:Person {handle:$h})-[r:LEADS]->(t) RETURN t.name, r.role", {"h": h})
    led = [r[0] for r in _q("MATCH (:Person {handle:$h})-[:LED]->(t) RETURN t.name", {"h": h})]
    keps = _q("""MATCH (:Person {handle:$h})-[r:AUTHORED|APPROVED]->(k:KEP)
                 RETURN k.number, k.title, type(r), k.status ORDER BY toInteger(k.number) DESC LIMIT 15""", {"h": h})
    return {"handle": h, "status": status,
            "approves": approves[:25], "approves_count": len(approves),
            "emeritus_of": emer[:25], "emeritus_count": len(emer),
            "leads": [dict(team=t, role=r) for t, r in leads], "formerly_led": led,
            "decisions": [dict(kep=n, title=t, role=r.lower(), status=s) for n, t, r, s in keps],
            "ownership_changes": [dict(pr=pr, date=d, what=w, source=u) for pr, d, w, u in _q(
                """MATCH (c:Change)-[a:AFFECTED]->(:Person {handle:$h})
                   RETURN c.pr, c.date, collect(a.how + ' ' + a.path)[0..6], c.url ORDER BY c.date DESC LIMIT 5""", {"h": h})],
            "as_of": _as_of(), "policy": POLICY}


_tree = None


def _repo_paths():
    """(files, folders) in the pinned kubernetes/kubernetes checkout, from the git tree (no blobs)."""
    global _tree
    if _tree is None:
        files = set(subprocess.run(["git", "-C", "data/raw/k8s", "ls-tree", "-r", "--name-only", "HEAD"],
                                   capture_output=True, text=True).stdout.split())
        folders = set()
        for f in files:
            d = os.path.dirname(f)
            while d and d not in folders:
                folders.add(d)
                d = os.path.dirname(d)
        _tree = (files, folders)
    return _tree


def _as_of():
    r = _q("MATCH (s:Source {repo:'kubernetes/kubernetes'}) RETURN s.sha, s.date")
    return dict(commit=r[0][0][:11], date=r[0][1]) if r else None


def _last_change(paths):
    r = _q("""MATCH (c:Change)-[:TOUCHED]->(d:Dir) WHERE d.path IN $ps
              RETURN c.pr, c.date, c.title, c.url, d.path ORDER BY c.date DESC LIMIT 1""", {"ps": paths})
    return dict(pr=r[0][0], date=r[0][1], title=r[0][2], url=r[0][3], folder=r[0][4]) if r else None


def who_approves(path: str, max_climb: int = None):
    """Who must approve a change to this file or folder. Walks up the folder tree the way
    Kubernetes' OWNERS inheritance does, and cites each OWNERS file. max_climb=0 looks only
    at the file's own folder (the shallow answer)."""
    p = (path or "").strip().strip("/")
    files, folders = _repo_paths()
    if files and p and p not in files and p not in folders:
        return {"path": path, "outcome": "not_in_repo",
                "message": f"'{path}' is not a file or folder in kubernetes/kubernetes at {(_as_of() or {}).get('commit')}. Nothing to answer from."}
    d = _nearest_dir(path)
    chain = _chain(d)
    if max_climb is not None:  # count OWNERS levels above the file's own folder
        own = (p if p in folders else os.path.dirname(p)) or "."
        own = own if _q("MATCH (x:Dir {path:$p}) RETURN count(x)", {"p": own})[0][0] else None
        chain = ([] if own is None else [own]) + [c for c in chain if c != own]
        chain = chain[: int(max_climb) + (1 if own else 0)]
    by = _approvers(chain)
    levels = []
    for c in chain:
        appr = by[c]["APPROVES"]
        if not appr and not by[c]["EMERITUS_OF"]:
            continue
        levels.append(dict(owners_file=_src_dir(c),
                           approvers=sorted(h for h, s in appr),
                           reviewers=sorted(h for h, s in by[c]["REVIEWS"])[:12],
                           former_approvers=sorted(h for h, s in by[c]["EMERITUS_OF"])))
    nearest = next((l for l in levels if l["approvers"]), None)
    return {"path": path, "outcome": "answered" if nearest else "no_approvers_within_reach",
            "governed_by": _src_dir(d) if chain else None, "climbed": max(0, len(chain) - 1),
            "max_climb": max_climb, "nearest_approvers": nearest, "all_levels": levels[:6],
            "last_ownership_change": _last_change(chain) if chain else None, "as_of": _as_of()}


def recent_changes(handle: str = None, path: str = None, limit: int = 10):
    """Ownership changes the graph has absorbed since it was loaded: merged PRs that added or
    removed approvers and reviewers, moved people to emeritus, or created/deleted OWNERS files."""
    n = max(1, min(int(limit), 30))
    if handle:
        h, _ = _person(handle)
        rows = _q("""MATCH (c:Change)-[a:AFFECTED]->(:Person {handle:$h})
                     RETURN c.pr, c.date, c.title, c.url, collect(a.how + ' ' + a.path) ORDER BY c.date DESC LIMIT $n""", {"h": h, "n": n})
    elif path:
        p = path.strip().strip("/")
        rows = _q("""MATCH (c:Change) WHERE any(f IN c.files WHERE f STARTS WITH $p)
                     OPTIONAL MATCH (c)-[a:AFFECTED]->(x:Person)
                     RETURN c.pr, c.date, c.title, c.url, collect(x.handle + ': ' + a.how + ' ' + a.path) ORDER BY c.date DESC LIMIT $n""", {"p": p, "n": n})
    else:
        rows = _q("""MATCH (c:Change) OPTIONAL MATCH (c)-[a:AFFECTED]->(x:Person)
                     RETURN c.pr, c.date, c.title, c.url, collect(x.handle + ': ' + a.how + ' ' + a.path) ORDER BY c.date DESC LIMIT $n""", {"n": n})
    out = [dict(pr=pr, date=d, title=t, source=u, what=sorted(set(w))[:12], more=max(0, len(set(w)) - 12)) for pr, d, t, u, w in rows]
    return {"outcome": "answered" if out else "none_recorded", "as_of": _as_of(), "changes": out,
            "note": None if out else "No ownership changes have been synced into the graph since it was loaded."}


# ------------------------------------------------------------------ departures

def departure_impact(handle: str, also_leaving: list = None):
    """What leaves with a person (optionally together with others). For someone active: what
    would happen if they left today. For someone already emeritus: what they left behind."""
    h, status = _person(handle)
    gone = {h} | {_person(x)[0] for x in also_leaving or []}
    rel = "APPROVES" if status == "active" else "EMERITUS_OF"
    areas = [r[0] for r in _q(f"""MATCH (p:Person)-[:{rel}]->(d:Dir) WHERE p.handle IN $gone
                                  RETURN DISTINCT d.path""", {"gone": sorted(gone)})]
    left = dict(_q("""MATCH (d:Dir) WHERE d.path IN $areas
                      OPTIONAL MATCH (x:Person {status:'active'})-[:APPROVES]->(d) WHERE NOT x.handle IN $gone
                      RETURN d.path, collect(x.handle)""", {"areas": areas, "gone": sorted(gone)}))
    results = []
    for a in areas:
        local = sorted(left.get(a) or [])
        fallback = None
        if not local:
            for p in _chain(a)[1:]:
                up = sorted(x for x, s in _approvers([p])[p]["APPROVES"] if x not in gone and s == "active")
                if up:
                    fallback = dict(owners_file=_src_dir(p), approvers=up)
                    break
        results.append(dict(area=a, owners_file=_src_dir(a), remaining_local_approvers=local, falls_back_to=fallback))
    results.sort(key=lambda r: (len(r["remaining_local_approvers"]), r["area"]))
    dark = [r for r in results if not r["remaining_local_approvers"]]
    thin = [r for r in results if len(r["remaining_local_approvers"]) == 1]
    keps = _q("""MATCH (g:Person)-[:APPROVED|AUTHORED]->(k:KEP) WHERE g.handle IN $gone
                 WITH DISTINCT k
                 OPTIONAL MATCH (o:Person)-[:APPROVED|AUTHORED]->(k) WHERE NOT o.handle IN $gone AND o.status = 'active'
                 WITH k, collect(DISTINCT o.handle) AS still_here
                 RETURN k.number, k.title, k.status, k.path, still_here""", {"h": h, "gone": sorted(gone)})
    orphan = [dict(kep=n, title=t, status=s, source=_src_kep(p)) for n, t, s, p, still in keps if not still]
    teams = [r[0] for r in _q("MATCH (p:Person)-[:LEADS]->(t) WHERE p.handle IN $gone RETURN DISTINCT t.name", {"gone": sorted(gone)})]
    verb = "if they left today" if status == "active" else "what they left behind"
    # departures the graph absorbed live: areas they were removed from by a synced PR
    stepped = _q("""MATCH (c:Change)-[a:AFFECTED]->(:Person {handle:$h})
                    WHERE a.how IN ['removed_approver', 'moved_to_emeritus']
                    OPTIONAL MATCH (d:Dir {path:a.path})
                    OPTIONAL MATCH (x:Person {status:'active'})-[:APPROVES]->(d)
                    RETURN c.pr, c.date, c.title, c.url, a.path, d IS NOT NULL, collect(x.handle)
                    ORDER BY c.date, a.path""", {"h": h})
    stepped_back = None
    if stepped:
        pr, date, title, url = stepped[-1][:4]
        live = [dict(area=p, now_approved_by=sorted(ap)) for *_, p, exists, ap in stepped if exists]
        live.sort(key=lambda r: (len(r["now_approved_by"]), r["area"]))
        stepped_back = dict(pr=pr, date=date, title=title, source=url, areas=len(live),
                            now_without_local_approver=sum(1 for r in live if not r["now_approved_by"]),
                            now_down_to_one=sum(1 for r in live if len(r["now_approved_by"]) == 1),
                            thinnest=live[:8])
    return {"handle": h, "status": status, "view": verb, "leaving_together": sorted(gone - {h}),
            "areas": len(results), "areas_with_no_local_approver": len(dark), "areas_down_to_one": len(thin),
            "dark_areas": dark[:15], "thin_areas": thin[:10],
            "decisions": len(keps), "decisions_left_with_nobody_active": len(orphan), "examples_of_those_decisions": orphan[:12],
            "leadership_seats": teams, "stepped_back_recently": stepped_back, "as_of": _as_of(), "policy": POLICY}


def heirs(handle: str, top: int = 5):
    """Who already carries this person's knowledge: active people ranked by how much code
    ownership and how many decisions they shared, with the evidence for each."""
    h, _ = _person(handle)
    rows = _q("""MATCH (p:Person {handle:$h})-[w:WORKED_WITH]-(x:Person {status:'active'})
                 RETURN x.handle, sum(w.weight) AS score ORDER BY score DESC, x.handle LIMIT $k""", {"h": h, "k": max(1, min(int(top), 15))})
    out = []
    for x, score in rows:
        shared = [r[0] for r in _q("""MATCH (:Person {handle:$h})-[:APPROVES|REVIEWS|EMERITUS_OF]->(d:Dir)<-[:APPROVES|REVIEWS]-(:Person {handle:$x})
                                      RETURN DISTINCT d.path ORDER BY d.path LIMIT 6""", {"h": h, "x": x})]
        holds = _q("""MATCH (:Person {handle:$h})-[:APPROVES|EMERITUS_OF]->(d:Dir)<-[:APPROVES]-(:Person {handle:$x})
                      RETURN count(DISTINCT d)""", {"h": h, "x": x})[0][0]
        decided = [r[0] for r in _q("""MATCH (:Person {handle:$h})-[:APPROVED|AUTHORED|REVIEWED]->(k:KEP)<-[:APPROVED|AUTHORED|REVIEWED]-(:Person {handle:$x})
                                       WITH DISTINCT k ORDER BY toInteger(k.number) DESC
                                       RETURN 'KEP-' + k.number + ' ' + k.title LIMIT 3""", {"h": h, "x": x})]
        out.append(dict(heir=x, score=round(score, 1), already_approves_their_areas=holds,
                        shared_areas=[_src_dir(p) for p in shared], shared_decisions=decided))
    return {"handle": h, "heirs": out, "method": "sum of shared OWNERS entries (1 each) and shared KEPs (0.5 each) on WORKED_WITH edges",
            "policy": POLICY}


def orphaned_knowledge(limit: int = 15):
    """Implemented decisions where nobody among their authors and approvers is still active."""
    rows = _q("""MATCH (k:KEP) WHERE k.status = 'implemented'
                 MATCH (p:Person)-[:AUTHORED|APPROVED]->(k)
                 WITH k, collect(p.handle) AS people, collect(p.status) AS st
                 WHERE NOT 'active' IN st
                 OPTIONAL MATCH (k)-[:OWNED_BY]->(t:Team)
                 RETURN k.number, k.title, t.name, people, k.path ORDER BY toInteger(k.number) LIMIT $n""",
              {"n": max(1, min(int(limit), 50))})
    total = _q("""MATCH (k:KEP) WHERE k.status = 'implemented'
                  MATCH (p:Person)-[:AUTHORED|APPROVED]->(k)
                  WITH k, collect(p.status) AS st WHERE NOT 'active' IN st RETURN count(k)""")[0][0]
    impl = _q("MATCH (k:KEP {status:'implemented'}) RETURN count(k)")[0][0]
    return {"implemented_decisions": impl, "with_nobody_active_left": total,
            "examples": [dict(kep=n, title=t, team=team, people=p, source=_src_kep(path)) for n, t, team, p, path in rows],
            "note": "'Active' = currently listed as an approver, reviewer or lead. People who only appear on KEPs count as not active."}


def single_points_of_failure(top: int = 15):
    """Code areas whose own OWNERS file has exactly one active approver, largest first."""
    rows = _q(f"""MATCH (x:Person {{status:'active'}})-[:APPROVES]->(d:Dir)
                  WITH d, collect(x.handle) AS ap WHERE size(ap) = 1
                  OPTIONAL MATCH (c:Dir)-[:CHILD_OF*1..{MAX_UP}]->(d)
                  RETURN d.path, ap[0], count(DISTINCT c) AS below ORDER BY below DESC, d.path LIMIT $k""",
              {"k": max(1, min(int(top), 50))})
    total = _q("""MATCH (x:Person {status:'active'})-[:APPROVES]->(d:Dir)
                  WITH d, count(x) AS n WHERE n = 1 RETURN count(d)""")[0][0]
    return {"areas_with_one_active_approver": total,
            "largest": [dict(area=p, only_approver=a, sub_areas=b, owners_file=_src_dir(p)) for p, a, b in rows]}


# ------------------------------------------------------------------ algorithms

def _team_of_dirs():
    """Each OWNERS directory's team: the team whose subproject owns it or its nearest ancestor."""
    owned = dict(_q("""MATCH (s:Subproject)-[:OWNS]->(d:Dir), (s)-[:PART_OF]->(t:Team) RETURN d.path, t.name"""))
    team = {}
    for (path,) in _q("MATCH (d:Dir) RETURN d.path"):
        for p in _chain(path):
            if p in owned and p != ".":
                team[path] = owned[p]
                break
    return team


def shadow_teams(min_size: int = 5, top: int = 6):
    """Community detection (algo.labelPropagation) on who actually works with whom among
    active people, compared with the official teams. Shows the groups that cut across them."""
    with _proj_lock:
        _q("MATCH ()-[r:ACTIVE_WITH]->() DELETE r")
        try:
            # each active person links to their 3 closest collaborators; plain weight
            # thresholds let the handful of project-wide approvers glue everyone into one blob
            _q("""MATCH (a:Person {status:'active'})-[w:WORKED_WITH]-(b:Person {status:'active'})
                       WITH a, b, w.weight AS wt ORDER BY wt DESC
                       WITH a, collect(b)[0..3] AS top UNWIND top AS b CREATE (a)-[:ACTIVE_WITH]->(b)""")
            rows = _q("""CALL algo.labelPropagation({nodeLabels:['Person'], relationshipTypes:['ACTIVE_WITH'], maxIterations:30})
                              YIELD node, communityId RETURN node.handle, communityId""", timeout=120000)
        finally:
            _q("MATCH ()-[r:ACTIVE_WITH]->() DELETE r")
    dir_team = _team_of_dirs()
    person_teams = defaultdict(lambda: defaultdict(int))
    for h, p in _q("MATCH (x:Person {status:'active'})-[:APPROVES|REVIEWS]->(d:Dir) RETURN x.handle, d.path"):
        if p in dir_team:
            person_teams[h][dir_team[p]] += 1
    comms = defaultdict(list)
    for h, c in rows:
        comms[c].append(h)
    out = []
    for c, members in comms.items():
        if len(members) < max(2, int(min_size)):
            continue
        tally = defaultdict(int)
        for m in members:
            if person_teams[m]:
                tally[max(person_teams[m], key=person_teams[m].get)] += 1
        teams = sorted(tally.items(), key=lambda kv: -kv[1])
        out.append(dict(size=len(members), official_teams_spanned=len(teams), teams=teams[:5], members=sorted(members)[:14]))
    out.sort(key=lambda c: (-c["official_teams_spanned"], -c["size"]))
    return {"communities": len(out), "cross_team_groups": [c for c in out if c["official_teams_spanned"] >= 2][:int(top)],
            "method": "algo.labelPropagation over each active person's 3 strongest WORKED_WITH links; each member's official team = the team owning most of the code they approve or review"}


def keystone_people(top: int = 10):
    """Betweenness centrality on WORKED_WITH among active people: the people who connect
    groups that would otherwise not talk."""
    with _proj_lock:
        _q("MATCH ()-[r:ACTIVE_WITH]->() DELETE r")
        try:
            _q("""MATCH (a:Person {status:'active'})-[w:WORKED_WITH]-(b:Person {status:'active'})
                       WHERE w.weight >= 2 CREATE (a)-[:ACTIVE_WITH]->(b)""")
            rows = _q("""CALL algo.betweenness({nodeLabels:['Person'], relationshipTypes:['ACTIVE_WITH'], samplingSize:100000, samplingSeed:7})
                              YIELD node, score RETURN node.handle, score ORDER BY score DESC LIMIT $k""",
                           {"k": max(1, min(int(top), 30))}, timeout=120000)
        finally:
            _q("MATCH ()-[r:ACTIVE_WITH]->() DELETE r")
    return {"keystones": [dict(handle=h, betweenness=round(s, 1)) for h, s in rows],
            "method": "algo.betweenness over WORKED_WITH (weight >= 2) among active people"}


# ------------------------------------------------------------------ decisions

def why(topic: str, limit: int = 5):
    """Trace a feature or topic to the design decisions behind it: owning team, authors,
    approvers, what it replaced, and who can still explain it."""
    t = (topic or "").strip()
    if len(t) < 3:
        raise ToolError("topic must be at least 3 characters")
    try:
        rows = _q("CALL db.idx.fulltext.queryNodes('KEP', $q) YIELD node, score RETURN node.number, score ORDER BY score DESC LIMIT $n",
                  {"q": t, "n": max(1, min(int(limit), 10))})
    except Exception:
        rows = []
    if not rows:
        rows = _q("MATCH (k:KEP) WHERE toLower(k.title) CONTAINS toLower($q) RETURN k.number, 1.0 LIMIT $n", {"q": t, "n": int(limit)})
    if not rows:
        return {"topic": topic, "outcome": "not_in_graph", "decisions": [],
                "message": f"Not in the graph: no Kubernetes design decision (KEP) matches '{topic}'. This is a gap in the records, not evidence that nothing exists."}
    out = []
    for n, _ in rows:
        k = _q("""MATCH (k:KEP {number:$n}) OPTIONAL MATCH (k)-[:OWNED_BY]->(t:Team)
                  RETURN k.title, k.status, k.stage, k.milestone, k.created, k.path, t.name""", {"n": n})[0]
        ppl = _q("MATCH (p:Person)-[r:AUTHORED|APPROVED]->(:KEP {number:$n}) RETURN p.handle, p.status, type(r)", {"n": n})
        rep = [f"KEP-{r[0]} {r[1]}" for r in _q("MATCH (:KEP {number:$n})-[:REPLACES]->(o:KEP) RETURN o.number, o.title", {"n": n})]
        active = sorted({p for p, s, _ in ppl if s == "active"})
        out.append(dict(kep=n, title=k[0], status=k[1], stage=k[2], latest_milestone=k[3], created=k[4], team=k[6],
                        authors=sorted({p for p, s, r in ppl if r == "AUTHORED"}),
                        approvers=sorted({p for p, s, r in ppl if r == "APPROVED"}),
                        replaces=rep, can_still_explain=active, source=_src_kep(k[5])))
    return {"topic": topic, "outcome": "answered", "decisions": out, "policy": POLICY}


# ------------------------------------------------------------------ memory

def record_scenario(handle: str, note: str):
    """Save a departure scenario and its conclusion so later sessions can recall it."""
    h, status = _person(handle)
    imp = departure_impact(h)
    sid = f"scenario-{uuid.uuid4().hex[:8]}"
    _q("""MATCH (p:Person {handle:$h})
          CREATE (s:Scenario {id:$id, at:$at, note:$note, dark:$dark, thin:$thin, orphan:$orphan})-[:ABOUT]->(p)""",
       {"h": h, "id": sid, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": note[:2000],
        "dark": imp["areas_with_no_local_approver"], "thin": imp["areas_down_to_one"],
        "orphan": imp["decisions_left_with_nobody_active"]})
    return {"saved": sid, "about": h, "dark_areas": imp["areas_with_no_local_approver"]}


def recall(handle: str = None):
    """Scenarios saved in earlier sessions, optionally for one person."""
    if handle:
        h, _ = _person(handle)
        rows = _q("MATCH (s:Scenario)-[:ABOUT]->(p:Person {handle:$h}) RETURN s.id, s.at, p.handle, s.note, s.dark, s.thin ORDER BY s.at DESC LIMIT 10", {"h": h})
    else:
        rows = _q("MATCH (s:Scenario)-[:ABOUT]->(p:Person) RETURN s.id, s.at, p.handle, s.note, s.dark, s.thin ORDER BY s.at DESC LIMIT 10")
    return [dict(id=i, at=a, about=p, note=n, dark_areas=d, thin_areas=t) for i, a, p, n, d, t in rows]


# ------------------------------------------------------------------ registry

_H = {"type": "string", "description": "GitHub handle, e.g. thockin"}
TOOLS = {
    "find_person": (find_person, "Find GitHub handles in the Kubernetes records. Use before other tools when unsure of a handle.", {"query": {"type": "string"}}, ["query"]),
    "whois": (whois, "What a person owns, leads and decided, and whether they are still active.", {"handle": _H}, ["handle"]),
    "who_approves": (who_approves, "Who must approve a change to a file or folder in kubernetes/kubernetes, following OWNERS inheritance up the tree, with the OWNERS files and the latest ownership change cited. max_climb=0 checks only the file's own folder.", {"path": {"type": "string", "description": "e.g. pkg/kubelet/kuberuntime/kuberuntime_container.go"}, "max_climb": {"type": "integer"}}, ["path"]),
    "recent_changes": (recent_changes, "Ownership changes (merged PRs) synced into the graph: who was added, removed or moved to emeritus, and where. Filter by handle or path.", {"handle": _H, "path": {"type": "string"}, "limit": {"type": "integer"}}, []),
    "departure_impact": (departure_impact, "What leaves with a person: code areas left with no or one local approver, decisions with nobody active left, leadership seats. For an emeritus person it shows what they left behind. also_leaving models a group departure (e.g. a whole team or vendor).", {"handle": _H, "also_leaving": {"type": "array", "items": {"type": "string"}}}, ["handle"]),
    "heirs": (heirs, "Who already carries a person's knowledge, ranked, with shared areas and decisions as evidence.", {"handle": _H, "top": {"type": "integer"}}, ["handle"]),
    "orphaned_knowledge": (orphaned_knowledge, "Implemented design decisions whose authors and approvers are no longer active.", {"limit": {"type": "integer"}}, []),
    "single_points_of_failure": (single_points_of_failure, "Code areas whose own OWNERS file has exactly one active approver.", {"top": {"type": "integer"}}, []),
    "shadow_teams": (shadow_teams, "Community detection on who actually works together, compared with official teams.", {"min_size": {"type": "integer"}, "top": {"type": "integer"}}, []),
    "keystone_people": (keystone_people, "Betweenness centrality: people who connect groups that otherwise would not talk.", {"top": {"type": "integer"}}, []),
    "why": (why, "Trace a feature or topic to the design decisions (KEPs) behind it, who made them and who can still explain them.", {"topic": {"type": "string"}, "limit": {"type": "integer"}}, ["topic"]),
    "record_scenario": (record_scenario, "Save a departure scenario and conclusion to memory.", {"handle": _H, "note": {"type": "string"}}, ["handle", "note"]),
    "recall": (recall, "Recall saved departure scenarios.", {"handle": _H}, []),
}


def tool_specs():
    return [{"name": n, "description": d, "input_schema": {"type": "object", "properties": p, "required": r}}
            for n, (_, d, p, r) in TOOLS.items()]


def call(name, args):
    if name not in TOOLS:
        raise ToolError(f"unknown tool {name}")
    return TOOLS[name][0](**(args or {}))
