"""Inheritance's toolbox: fixed Cypher and FalkorDB algorithms over the Kubernetes org graph.

Every answer carries provenance (the OWNERS file or KEP it came from). The brain answers
FROM people's public record, with attribution; it never answers AS them.
"""
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


def _q(query, params=None, timeout=60000):
    return graph().query(query, params or {}, timeout=timeout).result_set


def _person(handle):
    h = (handle or "").strip().lstrip("@").lower()
    r = _q("MATCH (p:Person {handle:$h}) RETURN p.handle, p.status", {"h": h})
    if not r:
        near = [x[0] for x in _q("MATCH (p:Person) WHERE p.handle CONTAINS $h RETURN p.handle LIMIT 8", {"h": h[:6]})]
        raise ToolError(f"No person '{handle}' in the Kubernetes records. Similar handles: {near or 'none'}")
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
            "policy": POLICY}


def who_approves(path: str):
    """Who must approve a change to this file or folder. Walks up the folder tree the way
    Kubernetes' OWNERS inheritance does, and cites each OWNERS file."""
    d = _nearest_dir(path)
    chain = _chain(d)
    by = _approvers(chain)
    levels = []
    for p in chain:
        appr = by[p]["APPROVES"]
        if not appr and not by[p]["EMERITUS_OF"]:
            continue
        levels.append(dict(owners_file=_src_dir(p),
                           approvers=sorted(h for h, s in appr),
                           reviewers=sorted(h for h, s in by[p]["REVIEWS"])[:12],
                           former_approvers=sorted(h for h, s in by[p]["EMERITUS_OF"])))
    nearest = next((l for l in levels if l["approvers"]), None)
    return {"path": path, "governed_by": _src_dir(d), "climbed": len(chain) - 1,
            "nearest_approvers": nearest, "all_levels": levels[:6]}


# ------------------------------------------------------------------ departures

def departure_impact(handle: str, also_leaving: list = None):
    """What leaves with a person (optionally together with others). For someone active: what
    would happen if they left today. For someone already emeritus: what they left behind."""
    h, status = _person(handle)
    gone = {h} | {_person(x)[0] for x in also_leaving or []}
    rel = "APPROVES" if status == "active" else "EMERITUS_OF"
    areas = [r[0] for r in _q(f"""MATCH (p:Person)-[:{rel}]->(d:Dir) WHERE p.handle IN $gone
                                  RETURN DISTINCT d.path""", {"gone": sorted(gone)})]
    results = []
    for a in areas:
        by = _approvers([a])
        local = sorted(x for x, s in by[a]["APPROVES"] if x not in gone and s == "active")
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
    return {"handle": h, "status": status, "view": verb, "leaving_together": sorted(gone - {h}),
            "areas": len(results), "areas_with_no_local_approver": len(dark), "areas_down_to_one": len(thin),
            "dark_areas": dark[:15], "thin_areas": thin[:10],
            "decisions": len(keps), "decisions_left_with_nobody_active": len(orphan), "examples_of_those_decisions": orphan[:12],
            "leadership_seats": teams, "policy": POLICY}


def heirs(handle: str, top: int = 5):
    """Who already carries this person's knowledge: active people ranked by how much code
    ownership and how many decisions they shared, with the evidence for each."""
    h, _ = _person(handle)
    rows = _q("""MATCH (p:Person {handle:$h})-[w:WORKED_WITH]-(x:Person {status:'active'})
                 RETURN x.handle, sum(w.weight) AS score ORDER BY score DESC LIMIT $k""", {"h": h, "k": max(1, min(int(top), 15))})
    out = []
    for x, score in rows:
        shared = [r[0] for r in _q("""MATCH (:Person {handle:$h})-[:APPROVES|REVIEWS|EMERITUS_OF]->(d:Dir)<-[:APPROVES|REVIEWS]-(:Person {handle:$x})
                                      RETURN DISTINCT d.path LIMIT 6""", {"h": h, "x": x})]
        holds = _q("""MATCH (:Person {handle:$h})-[:APPROVES|EMERITUS_OF]->(d:Dir)<-[:APPROVES]-(:Person {handle:$x})
                      RETURN count(DISTINCT d)""", {"h": h, "x": x})[0][0]
        decided = [r[0] for r in _q("""MATCH (:Person {handle:$h})-[:APPROVED|AUTHORED|REVIEWED]->(k:KEP)<-[:APPROVED|AUTHORED|REVIEWED]-(:Person {handle:$x})
                                       RETURN DISTINCT 'KEP-' + k.number + ' ' + k.title LIMIT 3""", {"h": h, "x": x})]
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
        g = graph()
        g.query("MATCH ()-[r:ACTIVE_WITH]->() DELETE r")
        try:
            # each active person links to their 3 closest collaborators; plain weight
            # thresholds let the handful of project-wide approvers glue everyone into one blob
            g.query("""MATCH (a:Person {status:'active'})-[w:WORKED_WITH]-(b:Person {status:'active'})
                       WITH a, b, w.weight AS wt ORDER BY wt DESC
                       WITH a, collect(b)[0..3] AS top UNWIND top AS b CREATE (a)-[:ACTIVE_WITH]->(b)""")
            rows = g.query("""CALL algo.labelPropagation({nodeLabels:['Person'], relationshipTypes:['ACTIVE_WITH'], maxIterations:30})
                              YIELD node, communityId RETURN node.handle, communityId""", timeout=120000).result_set
        finally:
            g.query("MATCH ()-[r:ACTIVE_WITH]->() DELETE r")
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
        g = graph()
        g.query("MATCH ()-[r:ACTIVE_WITH]->() DELETE r")
        try:
            g.query("""MATCH (a:Person {status:'active'})-[w:WORKED_WITH]-(b:Person {status:'active'})
                       WHERE w.weight >= 2 CREATE (a)-[:ACTIVE_WITH]->(b)""")
            rows = g.query("""CALL algo.betweenness({nodeLabels:['Person'], relationshipTypes:['ACTIVE_WITH'], samplingSize:100000, samplingSeed:7})
                              YIELD node, score RETURN node.handle, score ORDER BY score DESC LIMIT $k""",
                           {"k": max(1, min(int(top), 30))}, timeout=120000).result_set
        finally:
            g.query("MATCH ()-[r:ACTIVE_WITH]->() DELETE r")
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
        return {"topic": topic, "decisions": [], "message": f"No design decision mentions '{topic}' in its title."}
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
    return {"topic": topic, "decisions": out, "policy": POLICY}


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
    "who_approves": (who_approves, "Who must approve a change to a file or folder in kubernetes/kubernetes, following OWNERS inheritance up the tree, with the OWNERS files cited.", {"path": {"type": "string", "description": "e.g. pkg/kubelet/kuberuntime/kuberuntime_container.go"}}, ["path"]),
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
