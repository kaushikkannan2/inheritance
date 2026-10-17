"""Keep the graph current: replay real ownership changes from kubernetes/kubernetes, one
merged pull request at a time, into the live graph.

  python -m inheritance.load --reset --as-of 2026-08-20     # the graph as it was in August
  python -m inheritance.sync --steps 1                      # apply the next ownership change
  python -m inheritance.sync                                # apply everything up to the pinned commit
  python -m inheritance.sync --verify                       # and check it equals a fresh load

Each merged PR becomes a (:Change) node linked to the folders it touched and the people it
affected, so answers can cite the PR that made them true. Only the folders a PR touched are
rewritten; nothing is reloaded.
"""
import argparse
import json
import os
import re
from itertools import combinations

from . import db
from .load import RAW, _git, nearest_parent, parse_aliases, parse_owners_text

ROOT = f"{RAW}/k8s"
REPO_URL = "https://github.com/kubernetes/kubernetes"
SPECS = ["*OWNERS", "OWNERS_ALIASES"]
HOW = dict(added_approvers="added_approver", removed_approvers="removed_approver", added_reviewers="added_reviewer",
           removed_reviewers="removed_reviewer", to_emeritus="moved_to_emeritus")


def _is_owners(path):
    return path == "OWNERS" or path.endswith("/OWNERS")


def _show(rev, path):
    try:
        return _git("show", f"{rev}:{path}")
    except Exception:
        return None


_blob = {}


def _cached(path, rev):
    """OWNERS text at rev, reusing the blob id so unchanged files are read once."""
    try:
        oid = _git("rev-parse", f"{rev}:{path}")
    except Exception:
        return None
    if oid not in _blob:
        _blob[oid] = _git("cat-file", "-p", oid)
    return _blob[oid]


def pending(g, to="HEAD"):
    """Merged PRs that changed ownership after the graph's commit, oldest first."""
    src = g.query("MATCH (s:Source {repo:'kubernetes/kubernetes'}) RETURN s.sha").result_set
    if not src:
        raise SystemExit("No Source node: run python -m inheritance.load first")
    target = _git("rev-parse", to)
    out = _git("log", "--first-parent", "--reverse", "--format=%H%x1f%cs%x1f%s%x1f%b%x1e",
               f"{src[0][0]}..{target}", "--", *SPECS)
    commits = []
    for rec in filter(None, (r.strip() for r in out.split("\x1e"))):
        sha, date, subject, body = (rec.split("\x1f") + [""])[:4]
        files = [l.split("\t") for l in _git("diff", "--name-status", "--no-renames", f"{sha}^1", sha, "--", *SPECS).splitlines()]
        files = [(st, p) for st, p in files if _is_owners(p) or p == "OWNERS_ALIASES"]
        if not files:
            continue  # e.g. only CODEOWNERS changed
        m = re.search(r"#(\d+)", subject)
        commits.append(dict(sha=sha, date=date, pr=m and m.group(1),
                            title=(body.strip().splitlines() or [subject])[0][:200], files=files))
    return commits


def _crew(spec):
    return set(spec["approvers"]) | set(spec["reviewers"]) | set(spec["emeritus"]) if spec else set()


def _pairs(crew):
    return set(combinations(sorted(crew), 2))


def _current(g, path):
    rows = g.query("""MATCH (d:Dir {path:$p}) OPTIONAL MATCH (x:Person)-[r:APPROVES|REVIEWS|EMERITUS_OF]->(d)
                      RETURN d.no_parent_owners, d.labels, type(r), x.handle""", {"p": path}).result_set
    if not rows:
        return None
    spec = dict(approvers=[], reviewers=[], emeritus=[], no_parent=rows[0][0], labels=rows[0][1])
    key = {"APPROVES": "approvers", "REVIEWS": "reviewers", "EMERITUS_OF": "emeritus"}
    for _, _, rel, h in rows:
        if rel:
            spec[key[rel]].append(h)
    return spec


def _apply_dir(g, path, new, change):
    """Rewrite one folder's ownership in place. Returns what changed for the Change node."""
    old = _current(g, path)
    if old is None and new is None:
        return None
    existing = {r[0] for r in g.query("MATCH (d:Dir) RETURN d.path").result_set}
    if new is None:  # OWNERS file deleted: children re-attach to this folder's parent
        g.query("""MATCH (d:Dir {path:$p}) OPTIONAL MATCH (d)-[:CHILD_OF]->(up:Dir)
                   OPTIONAL MATCH (c:Dir)-[:CHILD_OF]->(d)
                   WITH d, up, collect(c) AS kids
                   FOREACH (k IN CASE WHEN up IS NULL THEN [] ELSE kids END | CREATE (k)-[:CHILD_OF]->(up))
                   DETACH DELETE d""", {"p": path})
        kind = "deleted"
    elif old is None:  # new OWNERS file: slot it into the tree between its parent and children
        parent = nearest_parent(path, existing)
        g.query("CREATE (:Dir {path:$p, owners_file:$f})", {"p": path, "f": ("" if path == "." else path + "/") + "OWNERS"})
        if parent:
            g.query("MATCH (c:Dir {path:$c}), (p:Dir {path:$p}) CREATE (c)-[:CHILD_OF]->(p)", {"c": path, "p": parent})
            g.query("""MATCH (k:Dir)-[r:CHILD_OF]->(:Dir {path:$up}) WHERE k.path STARTS WITH ($p + '/')
                       MATCH (n:Dir {path:$p}) DELETE r CREATE (k)-[:CHILD_OF]->(n)""", {"up": parent, "p": path})
        kind = "created"
    else:
        kind = "updated"
    if new is not None:
        g.query("""MATCH (d:Dir {path:$p}) SET d.no_parent_owners = $np, d.labels = $lb
                   WITH d OPTIONAL MATCH (:Person)-[r:APPROVES|REVIEWS|EMERITUS_OF]->(d) DELETE r""",
                {"p": path, "np": new["no_parent"], "lb": new["labels"]})
        for rel, key in (("APPROVES", "approvers"), ("REVIEWS", "reviewers"), ("EMERITUS_OF", "emeritus")):
            g.query(f"""UNWIND $hs AS h MERGE (x:Person {{handle:h}}) ON CREATE SET x.status = 'unlisted'
                        WITH x MATCH (d:Dir {{path:$p}}) CREATE (x)-[:{rel}]->(d)""", {"hs": new[key], "p": path})
    # WORKED_WITH: take this folder's old crew out, put the new crew in
    before, after = _pairs(_crew(old)), _pairs(_crew(new))
    if before - after:
        g.query("""UNWIND $rows AS r MATCH (:Person {handle:r[0]})-[w:WORKED_WITH]->(:Person {handle:r[1]})
                   SET w.weight = w.weight - 1.0 WITH w WHERE w.weight <= 0.0001 DELETE w""",
                {"rows": [list(x) for x in before - after]})
    if after - before:
        g.query("""UNWIND $rows AS r MATCH (a:Person {handle:r[0]}), (b:Person {handle:r[1]})
                   MERGE (a)-[w:WORKED_WITH]->(b) ON CREATE SET w.weight = 0.0 SET w.weight = w.weight + 1.0""",
                {"rows": [list(x) for x in after - before]})
    o, n = old or dict(approvers=[], reviewers=[], emeritus=[]), new or dict(approvers=[], reviewers=[], emeritus=[])
    diff = dict(path=path, kind=kind,
                added_approvers=sorted(set(n["approvers"]) - set(o["approvers"])),
                removed_approvers=sorted(set(o["approvers"]) - set(n["approvers"])),
                added_reviewers=sorted(set(n["reviewers"]) - set(o["reviewers"]) - set(o["approvers"])),
                removed_reviewers=sorted(set(o["reviewers"]) - set(n["reviewers"]) - set(n["approvers"])),
                to_emeritus=sorted(set(n["emeritus"]) - set(o["emeritus"])))
    people = set().union(*[diff[k] for k in diff if k not in ("path", "kind")])
    if kind == "created":
        g.query("MATCH (c:Change {sha:$s}), (d:Dir {path:$p}) CREATE (c)-[:TOUCHED {kind:'created'}]->(d)", {"s": change, "p": path})
    elif kind == "updated" and people:
        g.query("MATCH (c:Change {sha:$s}), (d:Dir {path:$p}) CREATE (c)-[:TOUCHED {kind:'updated'}]->(d)", {"s": change, "p": path})
    for how in ("added_approvers", "removed_approvers", "added_reviewers", "removed_reviewers", "to_emeritus"):
        if diff[how]:
            g.query("""UNWIND $hs AS h MATCH (c:Change {sha:$s}), (x:Person {handle:h})
                       CREATE (c)-[:AFFECTED {how:$how, path:$p}]->(x)""", {"hs": diff[how], "s": change, "how": HOW[how], "p": path})
    diff["people"] = people | _crew(old) | _crew(new)
    return diff


def _restatus(g, handles):
    g.query("""UNWIND $hs AS h MATCH (p:Person {handle:h})
               OPTIONAL MATCH (p)-[a:APPROVES|REVIEWS|LEADS]->() WITH p, count(a) AS act
               OPTIONAL MATCH (p)-[e:EMERITUS_OF|LED]->() WITH p, act, count(e) AS em
               OPTIONAL MATCH (p)-[k:AUTHORED|REVIEWED|APPROVED]->() WITH p, act, em, count(k) AS kp
               WITH p, CASE WHEN act > 0 THEN 'active' WHEN em > 0 THEN 'emeritus' ELSE 'unlisted' END AS st, act + em + kp AS links
               SET p.status = st
               WITH p, links WHERE links = 0 DELETE p""", {"hs": sorted(handles)})


def apply(g, c, aliases):
    """Apply one merged PR. Returns (summary, aliases after the PR)."""
    paths = {p for _, p in c["files"] if _is_owners(p)}
    new_aliases = aliases
    if any(p == "OWNERS_ALIASES" for _, p in c["files"]):
        new_aliases = parse_aliases(_show(c["sha"], "OWNERS_ALIASES"))
        changed = {k for k in set(aliases) | set(new_aliases) if aliases.get(k) != new_aliases.get(k)}
        # folders whose owners depend on a changed alias: parse their OWNERS file (as of this
        # PR) with the old and the new aliases and see if the answer differs
        if changed:
            for f in _git("ls-tree", "-r", "--name-only", c["sha"]).splitlines():
                if _is_owners(f):
                    text = _show(c["sha"], f) if f in paths else _cached(f, c["sha"])
                    if text and parse_owners_text(text, aliases) != parse_owners_text(text, new_aliases):
                        paths.add(f)
    g.query("""MERGE (c:Change {sha:$sha}) SET c.date = $date, c.pr = $pr, c.title = $title,
               c.url = $url, c.files = $files""",
            {"sha": c["sha"], "date": c["date"], "pr": c["pr"], "title": c["title"],
             "url": f"{REPO_URL}/pull/{c['pr']}" if c["pr"] else f"{REPO_URL}/commit/{c['sha']}",
             "files": [p for _, p in c["files"]]})
    diffs, people = [], set()
    for f in sorted(paths, key=lambda p: (p.count("/"), p)):  # parents before children
        path = os.path.dirname(f) or "."
        text = _show(c["sha"], f)
        spec = parse_owners_text(text, new_aliases) if text is not None else None
        if text is not None and spec is None:
            continue  # unparseable OWNERS file: the loader skips these too
        d = _apply_dir(g, path, spec, c["sha"])
        if d:
            people |= d.pop("people")
            if d["kind"] != "updated" or any(d[k] for k in d if k not in ("path", "kind")):
                diffs.append(d)
    _restatus(g, people)
    g.query("MATCH (s:Source {repo:'kubernetes/kubernetes'}) SET s.sha = $sha, s.date = $date", {"sha": c["sha"], "date": c["date"]})
    return dict(pr=c["pr"], date=c["date"], title=c["title"], changes=diffs), new_aliases


def sync(steps=None, to="HEAD", name=None, log=print):
    g = db.graph(name) if name else db.graph()
    todo = pending(g, to)
    if steps:
        todo = todo[:steps]
    if not todo:
        log("graph is up to date")
        return []
    start = g.query("MATCH (s:Source {repo:'kubernetes/kubernetes'}) RETURN s.sha").result_set[0][0]
    aliases = parse_aliases(_show(start, "OWNERS_ALIASES"))
    out = []
    for c in todo:
        s, aliases = apply(g, c, aliases)
        out.append(s)
        log(f"{s['date']}  PR #{s['pr']}  {s['title']}")
        for d in s["changes"]:
            bits = [f"{k.replace('_', ' ')}: {', '.join(d[k])}" for k in d if k not in ("path", "kind") and d[k]]
            log(f"    {d['kind']:<8} {d['path']}/OWNERS  " + ("; ".join(bits) or ""))
    left = len(pending(g, to))
    if not left:  # later commits did not touch ownership: the graph now reflects the target
        g.query("MATCH (s:Source {repo:'kubernetes/kubernetes'}) SET s.sha = $sha, s.date = $date",
                {"sha": _git("rev-parse", to), "date": _git("log", "-1", "--format=%cs", to)})
    log(f"applied {len(out)} change{'s' if len(out) != 1 else ''}; {left} still to apply")
    return out


def snapshot(name=None):
    """A canonical picture of everything the OWNERS files determine."""
    g = db.graph(name) if name else db.graph()
    def q(c, page=5000):  # page past FalkorDB's result-set cap
        rows, skip = [], 0
        while True:
            part = g.query(f"{c} ORDER BY 1 SKIP {skip} LIMIT {page}".replace("ORDER BY 1", "ORDER BY k"), timeout=120000).result_set
            rows += [tuple(map(str, r[1:])) for r in part]
            if len(part) < page:
                return sorted(rows)
            skip += page
    return dict(
        ownership=q("MATCH (x:Person)-[r:APPROVES|REVIEWS|EMERITUS_OF]->(d:Dir) RETURN x.handle + type(r) + d.path AS k, x.handle, type(r), d.path"),
        tree=q("MATCH (c:Dir)-[:CHILD_OF]->(p:Dir) RETURN c.path + '>' + p.path AS k, c.path, p.path"),
        dirs=q("MATCH (d:Dir) RETURN d.path AS k, d.path, d.no_parent_owners"),
        status=q("MATCH (p:Person) RETURN p.handle AS k, p.handle, p.status"),
        worked_with=q("MATCH (a:Person)-[w:WORKED_WITH]->(b:Person) RETURN a.handle + '>' + b.handle AS k, a.handle, b.handle, toFloat(round(w.weight * 2)) / 2.0"))


def verify():
    """Does the incrementally synced graph equal a fresh load at the same commit?"""
    from .load import load
    load(reset=True, name="inheritance_verify")
    a, b = snapshot(), snapshot("inheritance_verify")
    db.graph("inheritance_verify").delete()
    report = {}
    for k in a:
        sa, sb = set(a[k]), set(b[k])
        report[k] = dict(rows=len(sa), only_synced=sorted(sa - sb)[:5], only_fresh=sorted(sb - sa)[:5], equal=sa == sb)
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Replay ownership changes into the graph")
    ap.add_argument("--steps", type=int, help="apply only the next N changes")
    ap.add_argument("--to", default="HEAD", help="stop at this commit (default: the checked-out pin)")
    ap.add_argument("--list", action="store_true", help="show pending changes without applying them")
    ap.add_argument("--verify", action="store_true", help="after syncing, compare with a fresh load")
    a = ap.parse_args()
    if a.list:
        for c in pending(db.graph(), a.to):
            print(f"{c['date']}  PR #{c['pr']}  {c['title']}  ({len(c['files'])} file{'s' if len(c['files']) != 1 else ''})")
    else:
        sync(a.steps, a.to)
        if a.verify:
            r = verify()
            for k, v in r.items():
                print(f"verify {k:<12} {v['rows']:>6} rows  {'EQUAL' if v['equal'] else 'DIFFERENT'}"
                      + ("" if v["equal"] else f"  synced-only {v['only_synced']}  fresh-only {v['only_fresh']}"))
            print(json.dumps({k: v["equal"] for k, v in r.items()}))
