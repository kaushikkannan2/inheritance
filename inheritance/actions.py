"""Actions: the agent does work, not just answers.

  draft_handoff        writes a hand-off note and a ready-to-apply OWNERS patch for a departure
  route_question       sends a question about code or a topic to the right active expert
  flag_stale_records   finds places where the project's own records disagree

Every action is also written back to the graph (:Handoff, :Question, :Flag), so later
sessions can see what was proposed, and every line cites the record it came from.
"""
import difflib
import os
import re
import time
import uuid

from . import tools
from .tools import POLICY, ToolError, _as_of, _person, _q, _src_dir, _src_kep

OUT = "out/handoffs"
K8S = "data/raw/k8s"


def _candidates(area, gone, leaver):
    """Active people who could take over approving an area, with the reason."""
    reviewers = [r[0] for r in _q("""MATCH (r:Person {status:'active'})-[:REVIEWS]->(:Dir {path:$p}) WHERE NOT r.handle IN $gone
                                       RETURN r.handle""", {"p": area, "gone": sorted(gone)})]
    current = [r[0] for r in _q("MATCH (c:Person)-[:APPROVES]->(:Dir {path:$p}) RETURN c.handle", {"p": area})]
    above = []
    for up in tools._chain(area)[1:]:  # the nearest parent folder whose approvers would inherit this area
        above = [r[0] for r in _q("""MATCH (a:Person {status:'active'})-[:APPROVES]->(:Dir {path:$p}) WHERE NOT a.handle IN $gone
                                     RETURN a.handle""", {"p": up, "gone": sorted(gone)})]
        if above:
            break
    ww = dict(_q("""MATCH (:Person {handle:$h})-[w:WORKED_WITH]-(x:Person {status:'active'}) RETURN x.handle, sum(w.weight)""", {"h": leaver}))
    score = {}
    for h in set(reviewers) | set(above):
        if h in current or h in gone:
            continue
        why = []
        s = 0.0
        if h in reviewers:
            s += 3
            why.append(f"already reviews this area ({_src_dir(area)})")
        if h in above:
            s += 2
            why.append("approves the nearest parent folder")
        if ww.get(h):
            s += min(ww[h], 30) / 10
            why.append(f"worked with {leaver} (shared weight {ww[h]:g})")
        score[h] = (s, why)
    return sorted(([h, round(s, 1), why] for h, (s, why) in score.items()), key=lambda x: (-x[1], x[0]))[:3]


def _patch_owners(area, add):
    """Unified diff adding `add` to the approvers list of one OWNERS file (None if not patchable)."""
    rel = ("" if area == "." else area + "/") + "OWNERS"
    path = os.path.join(K8S, rel)
    if not os.path.exists(path):
        return None
    lines = open(path).read().splitlines(keepends=True)
    if any(l.startswith("filters:") for l in lines):
        return None  # per-file sections: leave to a human
    new = list(lines)
    try:
        i = next(k for k, l in enumerate(new) if re.match(r"^approvers:\s*$", l))
    except StopIteration:
        at = next((k for k, l in enumerate(new) if re.match(r"^(reviewers|labels|emeritus_approvers|options):", l)), len(new))
        new[at:at] = ["approvers:\n", f"  - {add}\n"]
    else:
        j, indent = i + 1, "  "
        while j < len(new) and re.match(r"^\s+-\s", new[j]):
            indent = re.match(r"^(\s+)-", new[j]).group(1)
            j += 1
        new.insert(j, f"{indent}- {add}\n")
    return "".join(difflib.unified_diff(lines, new, f"a/{rel}", f"b/{rel}"))


def draft_handoff(handle: str, also_leaving: list = None, write: bool = True):
    """Prepare a departure: for every area that would be left with no or one approver, propose
    a successor with evidence, list the decisions that need a new owner, and write a hand-off
    note plus an OWNERS patch the project could apply."""
    h, status = _person(handle)
    gone = {h} | {_person(x)[0] for x in also_leaving or []}
    imp = tools.departure_impact(h, sorted(gone - {h}))
    at_risk = imp["dark_areas"] + imp["thin_areas"]
    plan, patches = [], []
    for a in at_risk:
        cands = _candidates(a["area"], gone, h)
        pick = cands[0] if cands else None
        diff = _patch_owners(a["area"], pick[0]) if pick else None
        if diff:
            patches.append(diff)
        plan.append(dict(area=a["area"], owners_file=a["owners_file"], left=a["remaining_local_approvers"],
                         proposed=pick[0] if pick else None, because=pick[2] if pick else [],
                         alternatives=[c[0] for c in cands[1:]], patch="ready" if diff else ("needs a human: per-file filters" if pick else "no candidate in the records")))
    heirs = tools.heirs(h, 3)["heirs"]
    decisions = [dict(kep=d["kep"], title=d["title"], source=d["source"]) for d in imp["examples_of_those_decisions"]]
    note = None
    if write:
        os.makedirs(OUT, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = f"{OUT}/{h}{'-group' if len(gone) > 1 else ''}-{stamp}"
        md = [f"# Hand-off: {', '.join(sorted(gone))}", "",
              f"Prepared by Inheritance from Kubernetes public records as of commit {(_as_of() or {}).get('commit')}. "
              "Every line cites the OWNERS file or KEP it comes from. This is a proposal for the people involved, not a decision.", "",
              f"**Scope:** {imp['areas']} code areas, {imp['decisions']} design decisions, leadership seats: {', '.join(imp['leadership_seats']) or 'none'}.", "",
              "## Code areas that need a new approver", "",
              "| Area | Left after departure | Proposed | Why | Patch |", "|---|---|---|---|---|"]
        for p in plan:
            md.append(f"| `{p['area']}` | {', '.join(p['left']) or '**nobody**'} | {p['proposed'] or '-'} | {'; '.join(p['because']) or '-'} | {p['patch']} |")
        if not plan:
            md.append("| none | every area keeps two or more approvers | | | |")
        md += ["", "## Decisions with nobody active left who wrote or approved them", ""]
        md += [f"- KEP-{d['kep']} {d['title']} ({d['source']})" for d in decisions] or ["- none"]
        md += ["", "## Who already carries this knowledge", ""]
        md += [f"- **{x['heir']}**: approves {x['already_approves_their_areas']} of the same areas; shared decisions: {', '.join(x['shared_decisions']) or '-'}" for x in heirs]
        md += ["", "## Apply the OWNERS changes", "", f"`git apply {os.path.basename(base)}.patch` in a kubernetes/kubernetes checkout, then open a PR for the owners to review.", "",
               f"_{POLICY}_", ""]
        open(base + ".md", "w").write("\n".join(md))
        if patches:
            open(base + ".patch", "w").write("".join(patches))
        note = dict(note=base + ".md", patch=base + ".patch" if patches else None)
        hid = f"handoff-{uuid.uuid4().hex[:8]}"
        _q("""MATCH (p:Person {handle:$h}) CREATE (x:Handoff {id:$id, at:$at, note:$note, areas:$n, patched:$pt})-[:FOR]->(p)""",
           {"h": h, "id": hid, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": base + ".md", "n": len(plan), "pt": len(patches)})
        for p in plan:
            if p["proposed"]:
                _q("""MATCH (x:Handoff {id:$id}), (s:Person {handle:$s}) CREATE (x)-[:PROPOSES {area:$a}]->(s)""",
                   {"id": hid, "s": p["proposed"], "a": p["area"]})
        note["saved_as"] = hid
    return {"handle": h, "leaving_together": sorted(gone - {h}), "outcome": "action_taken" if write else "draft",
            "areas_needing_successor": len(plan), "plan": plan[:15], "patches_ready": len(patches),
            "decisions_needing_owner": decisions, "heirs": [x["heir"] for x in heirs], "files": note,
            "as_of": _as_of(), "policy": POLICY}


def route_question(question: str, path: str = None, topic: str = None):
    """Send a question to the right active expert: by the code path it is about, or by topic.
    Records the routing in the graph so the next session knows who was asked."""
    if not path and not topic:
        raise ToolError("give the code path the question is about, or a topic")
    picks, why = [], ""
    if path:
        r = tools.who_approves(path)
        if r["outcome"] == "not_in_repo":
            return {"outcome": "not_in_graph", "message": r["message"]}
        near = r["nearest_approvers"] or {}
        ranked = _q("""MATCH (x:Person {status:'active'})-[:APPROVES|REVIEWS]->(d:Dir) WHERE x.handle IN $hs
                       AND (d.path = $p OR d.path STARTS WITH ($p + '/'))
                       RETURN x.handle, count(d) AS n ORDER BY n DESC, x.handle""",
                    {"hs": near.get("approvers", []), "p": near.get("owners_file", "").replace("kubernetes/kubernetes/", "").rsplit("/OWNERS", 1)[0]})
        picks = [h for h, _ in ranked] or near.get("approvers", [])
        why = f"approvers in {near.get('owners_file')}, ranked by how much of that subtree they own"
    else:
        k = who_knows(topic)
        if k["outcome"] != "answered":
            return k
        picks = [x["handle"] for x in k["people"]]
        why = f"who_knows('{topic}'): " + "; ".join(f"{x['handle']}: {', '.join(x['evidence'][:2])}" for x in k["people"][:2])
    if not picks:
        return {"outcome": "no_expert_found", "message": "No active approver or expert in the records for this."}
    qid = f"q-{uuid.uuid4().hex[:8]}"
    _q("""CREATE (q:Question {id:$id, at:$at, text:$t, path:$p, topic:$tp})
          WITH q MATCH (x:Person {handle:$to}) CREATE (q)-[:ROUTED_TO {role:'primary'}]->(x)""",
       {"id": qid, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "t": question[:500], "p": path, "tp": topic, "to": picks[0]})
    if len(picks) > 1:
        _q("MATCH (q:Question {id:$id}), (x:Person {handle:$b}) CREATE (q)-[:ROUTED_TO {role:'backup'}]->(x)", {"id": qid, "b": picks[1]})
    return {"outcome": "action_taken", "routed_to": picks[0], "backup": picks[1] if len(picks) > 1 else None,
            "because": why, "saved_as": qid, "as_of": _as_of()}


def who_knows(topic: str, top: int = 5):
    """Who knows about a topic: people connected to the design decisions, design documents and
    code folders that match it, ranked, with the evidence for each. Active people only."""
    t = (topic or "").strip()
    if len(t) < 3:
        raise ToolError("topic must be at least 3 characters")
    score, ev = {}, {}

    def add(h, s, e):
        score[h] = score.get(h, 0) + s
        ev.setdefault(h, [])
        if e not in ev[h] and len(ev[h]) < 4:
            ev[h].append(e)
    keps = []
    for idx in ("KEP", "Document"):
        try:
            keps += [r[0] for r in _q(f"CALL db.idx.fulltext.queryNodes('{idx}', $q) YIELD node RETURN "
                                     + ("node.number" if idx == "KEP" else "node.kep") + " LIMIT 8", {"q": t})]
        except Exception:
            pass
    for n in dict.fromkeys(keps):
        for h, rel, title in _q("""MATCH (x:Person {status:'active'})-[r:AUTHORED|APPROVED|REVIEWED]->(k:KEP {number:$n})
                                   RETURN x.handle, type(r), k.title""", {"n": n}):
            add(h, {"AUTHORED": 3, "APPROVED": 2, "REVIEWED": 1}[rel], f"{rel.lower()} KEP-{n} {title}")
    token = re.sub(r"[^a-z0-9-]", "", t.lower().split()[0]) if t.split() else ""
    if len(token) >= 4:
        for h, rel, p in _q("""MATCH (x:Person {status:'active'})-[r:APPROVES|REVIEWS]->(d:Dir) WHERE toLower(d.path) CONTAINS $tk
                               RETURN x.handle, type(r), d.path LIMIT 400""", {"tk": token}):
            add(h, 2 if rel == "APPROVES" else 1, f"{'approves' if rel == 'APPROVES' else 'reviews'} {p}/")
    if not score:
        return {"topic": topic, "outcome": "not_in_graph",
                "message": f"Not in the graph: no design decision, design document or code folder matches '{topic}'."}
    best = sorted(score, key=lambda h: (-score[h], h))[: max(1, min(int(top), 10))]
    return {"topic": topic, "outcome": "answered", "people": [dict(handle=h, score=score[h], evidence=ev[h]) for h in best],
            "method": "design decisions and documents (full-text) and code folders matching the topic, traversed to the active people on them",
            "as_of": _as_of(), "policy": POLICY}


def flag_stale_records(limit: int = 10):
    """Places where Kubernetes' own records disagree: team leads who stepped back from all code
    ownership, and emeritus lists naming people still current in the same folder."""
    a = _q("""MATCH (p:Person)-[:LEADS]->(t:Team)
              WHERE NOT (p)-[:APPROVES|REVIEWS]->(:Dir)
                AND ((p)-[:EMERITUS_OF]->(:Dir) OR (:Change)-[:AFFECTED {how:'removed_approver'}]->(p))
              OPTIONAL MATCH (c:Change)-[x:AFFECTED]->(p) WHERE x.how IN ['removed_approver', 'moved_to_emeritus']
              OPTIONAL MATCH (p)-[:EMERITUS_OF]->(e:Dir)
              WITH p, t, c, count(DISTINCT e) AS em ORDER BY c.date DESC
              WITH p, t, collect(c)[0] AS c, max(em) AS em
              RETURN p.handle, t.name, c.pr, c.date, c.url, em ORDER BY c.date DESC, p.handle LIMIT $n""", {"n": max(1, min(int(limit), 30))})
    b = _q("""MATCH (p:Person)-[:EMERITUS_OF]->(d:Dir)<-[:APPROVES|REVIEWS]-(p) RETURN p.handle, d.path LIMIT $n""", {"n": int(limit)})
    flags = [dict(kind="lead_who_stepped_back_from_code", handle=h, team=t,
                  detail=(f"stepped back from all code ownership in PR #{pr} on {d}, still listed as {t} lead" if pr
                          else f"emeritus in {em} OWNERS file{'s' if em != 1 else ''} with no current code role, still listed as {t} lead"),
                  sources=[x for x in (u, "kubernetes/community/sigs.yaml") if x]) for h, t, pr, d, u, em in a]
    flags += [dict(kind="emeritus_and_current_in_same_folder", handle=h, detail=f"listed both as emeritus and current in {_src_dir(p)}",
                   sources=[_src_dir(p)]) for h, p in b]
    for f in flags:
        _q("""MATCH (x:Person {handle:$h}) MERGE (g:Flag {kind:$k, handle:$h}) SET g.detail = $d, g.at = $at MERGE (g)-[:ABOUT]->(x)""",
           {"h": f["handle"], "k": f["kind"], "d": f["detail"], "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return {"outcome": "answered" if flags else "none_found", "flags": flags,
            "note": "Leads do not have to approve code; a flag means the records are worth a second look, not that anything is wrong.",
            "as_of": _as_of()}


_H = {"type": "string", "description": "GitHub handle"}
tools.TOOLS.update({
    "who_knows": (who_knows, "Who knows about a topic (e.g. 'sidecar containers', 'scheduler'): active people tied to matching design decisions, design docs and code folders, with evidence.", {"topic": {"type": "string"}, "top": {"type": "integer"}}, ["topic"]),
    "draft_handoff": (draft_handoff, "ACTION. Prepare a departure: propose a successor for every area left with no or one approver (with evidence), list decisions needing an owner, and write a hand-off note plus an OWNERS patch file. also_leaving for a group.", {"handle": _H, "also_leaving": {"type": "array", "items": {"type": "string"}}}, ["handle"]),
    "route_question": (route_question, "ACTION. Route a question to the right active expert, by the code path it concerns or by topic; records the routing in the graph.", {"question": {"type": "string"}, "path": {"type": "string"}, "topic": {"type": "string"}}, ["question"]),
    "flag_stale_records": (flag_stale_records, "Find places where the project's own records disagree (e.g. a lead who stepped back from all code ownership); saves flags to the graph.", {"limit": {"type": "integer"}}, []),
})
