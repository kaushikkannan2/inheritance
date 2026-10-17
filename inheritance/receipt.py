"""Graph receipts and the grounding check.

A receipt shows what FalkorDB did for one answer: queries, traversals, algorithms, rows,
time. The grounding check takes every person, design decision (KEP) and code folder named
in the answer and confirms that FalkorDB returned it: from a tool call this session, or
from the graph-memory brief read at session start ("answered from memory"). Anything else
came from the model's own memory and is flagged.
"""
import json
import os
import re
import time
from collections import Counter

from . import tools

_ROOTS = r"(?:pkg|cmd|staging|test|hack|build|cluster|plugin|third_party|api|vendor|docs)"
_handles = None


def _all_handles():
    global _handles
    if _handles is None:
        _handles = {r[0] for r in tools._q("MATCH (p:Person) RETURN p.handle")}
    return _handles


def mentions(answer):
    """People, KEPs and code folders named in an answer."""
    keps = set(re.findall(r"KEP[- #]?(\d{2,5})\b", answer))
    paths = {p.rstrip("/.,:;)") for p in re.findall(rf"\b({_ROOTS}/[A-Za-z0-9_./-]+)", answer)}
    words = set(re.findall(r"(?<![\w/.-])([a-z0-9][a-z0-9-]{1,38})(?![\w/-])", answer.lower()))
    return dict(people=sorted(words & _all_handles()), keps=sorted(keps, key=int), paths=sorted(paths))


def _hit(kind, x, seen):
    if kind == "keps":
        return re.search(rf'"{x}"|kep-{x}\b|/{x}-', seen)
    if kind == "paths":
        return x.lower() in seen
    return re.search(rf'(?<![\w-]){re.escape(x)}(?![\w-])', seen)


def grounding(answer, tool_outputs, memory=""):
    """Split everything the answer names into: returned by FalkorDB this session, recalled
    from graph memory (the session-start brief), or neither (the model's own memory)."""
    seen, mem = "\n".join(tool_outputs).lower(), (memory or "").lower()
    out = {}
    for kind, items in mentions(answer).items():
        ok, recalled, flagged = [], [], []
        for x in items:
            (ok if _hit(kind, x, seen) else recalled if mem and _hit(kind, x, mem) else flagged).append(x)
        out[kind] = dict(grounded=ok, from_graph_memory=recalled, not_from_graph=flagged)
    total = sum(len(v["grounded"]) + len(v["from_graph_memory"]) + len(v["not_from_graph"]) for v in out.values())
    good = sum(len(v["grounded"]) + len(v["from_graph_memory"]) for v in out.values())
    out["recalled"] = sum(len(v["from_graph_memory"]) for v in out.values())
    out["score"] = f"{good}/{total}"
    out["all_grounded"] = good == total
    return out


def build(question, answer, trace, ledger, tool_outputs, use_graph=True, memory=""):
    kinds = Counter(x["kind"] for x in ledger)
    return dict(
        question=question, answer=answer, graph=use_graph,
        at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        tool_calls=[dict(tool=t["tool"], input=t["input"], error=t["error"]) for t in trace],
        falkordb=dict(queries=len(ledger), by_kind=dict(kinds), rows=sum(x["rows"] for x in ledger),
                      ms=round(sum(x["ms"] for x in ledger), 1),
                      algorithms=[x["cypher"] for x in ledger if x["kind"] == "algorithm"]),
        grounding=grounding(answer, tool_outputs, memory),
        ledger=ledger)


def render(r):
    f, g = r["falkordb"], r["grounding"]
    k = f["by_kind"]
    lines = ["", "── graph receipt " + "─" * 45]
    if not r["graph"]:
        lines.append("graph OFF: no tools, the model answered from its own memory")
    else:
        lines.append(f"{len(r['tool_calls'])} tool calls · {f['queries']} Cypher queries · {f['rows']:,} rows · {f['ms']:.0f} ms in FalkorDB")
        lines.append("   " + " · ".join(f"{n} {kind}" for kind, n in sorted(k.items(), key=lambda kv: -kv[1])))
        for a in f["algorithms"]:
            lines.append("   algorithm: " + a.split("(")[0].replace("CALL ", ""))
    lines.append(f"grounding: {g['score']} names in the answer came from FalkorDB"
                 + (f" ({g['recalled']} recalled from graph memory)" if g.get("recalled") else " this session")
                 + (" ✓" if g["all_grounded"] else ""))
    for kind in ("people", "keps", "paths"):
        bad = g[kind]["not_from_graph"]
        if bad:
            lines.append(f"   ⚠ {kind} not from the graph: {', '.join(bad[:12])}")
    lines.append("─" * 62)
    return "\n".join(lines)


def save(r, folder="out/receipts"):
    os.makedirs(folder, exist_ok=True)
    path = f"{folder}/{r['at'].replace(':', '')}{'' if r['graph'] else '-nograph'}.json"
    with open(path, "w") as fh:
        json.dump(r, fh, indent=2, default=str)
    return path
