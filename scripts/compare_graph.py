"""The same questions to the same model, with FalkorDB and without it.

  python scripts/compare_graph.py              # needs ANTHROPIC_API_KEY; ~3 min
  python scripts/compare_graph.py --no-setup   # use the graph as it is now

Setup loads the graph as of Oct 1, 2026 and replays one real PR (#142237), the same state
as the demo. Each question then runs twice: with the graph tools, and with no tools at all
(the model answers from its own knowledge). Both answers are scored on:

  facts    the expected facts, computed from the graph by the tools themselves (not by
           the model), that appear in the answer
  grounded names in the answer (people, KEPs, folders) that FalkorDB returned
  sources  OWNERS files, KEPs and PR links the answer cites

Writes docs/with_without.md (both answers side by side) and docs/with_without.json.
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from inheritance import tools  # noqa: E402


def expected():
    """Ground truth for each question, from the graph, without the language model."""
    t = tools.departure_impact("thockin")
    r = tools.departure_impact("rexagod")["stepped_back_recently"] or {}
    a = tools.who_approves("pkg/apis/core/validation/validation.go")["nearest_approvers"] or {}
    return [
        dict(q="If thockin left tomorrow, how many code areas would they leave behind, how many would have "
               "no local approver left, how many would be down to one approver, and how many design decisions "
               "would have nobody active left who wrote or approved them?",
             facts=[str(t["areas"]), str(t["areas_with_no_local_approver"]), str(t["areas_down_to_one"]),
                    str(t["decisions_left_with_nobody_active"])]),
        dict(q="rexagod recently stepped back in Kubernetes. Which pull request did it, how many code areas did it "
               "affect, and who approves those areas now?",
             facts=[str(r.get("pr")), str(r.get("areas"))] + sorted((r.get("thinnest") or [{}])[0].get("now_approved_by", []))),
        dict(q="Who can approve a change to pkg/apis/core/validation/validation.go in kubernetes/kubernetes, "
               "and which OWNERS file says so?",
             facts=[a.get("owners_file", "").replace("kubernetes/kubernetes/", "")] + a.get("approvers", [])),
    ]


def score(answer, facts):
    low = answer.lower()
    hit = [f for f in facts if f and re.search(rf"(?<![\w/.-]){re.escape(f.lower())}(?![\w-])", low)]
    sources = len(set(re.findall(r"[\w/.-]*OWNERS\b|KEP-\d+|pull/\d+|#\d{6}", answer)))
    return hit, sources


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-setup", action="store_true", help="skip loading Oct 1 and replaying PR #142237")
    a = ap.parse_args()
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))
    from inheritance import agent, load, receipt, sync
    if not a.no_setup:
        print("setup: graph as of 2026-10-01, then replay PR #142237", flush=True)
        load.load(reset=True, as_of="2026-10-01")
        sync.sync(steps=1, log=lambda *_: None)
    rows = []
    for i, item in enumerate(expected(), 1):
        for use_graph in (True, False):
            label = "with FalkorDB" if use_graph else "without FalkorDB"
            print(f"Q{i} {label} ...", flush=True)
            t0 = time.time()
            ans, _, r = agent.ask(item["q"], use_graph=use_graph, log=lambda *_: None)
            hit, src = score(ans, item["facts"])
            g = r["grounding"]
            rows.append(dict(q=i, question=item["q"], graph=use_graph, answer=ans, facts_expected=item["facts"],
                             facts_found=hit, sources=src, grounding=g["score"],
                             flagged=g["people"]["not_from_graph"] + g["keps"]["not_from_graph"] + g["paths"]["not_from_graph"],
                             tool_calls=len(r["tool_calls"]), queries=r["falkordb"]["queries"], seconds=round(time.time() - t0, 1)))
            receipt.save(r)
    print("\n  Q  mode               facts      grounded   sources  tool calls  queries  flagged")
    for x in rows:
        print(f"  {x['q']}  {'with FalkorDB   ' if x['graph'] else 'without FalkorDB'}   "
              f"{len(x['facts_found'])}/{len(x['facts_expected']):<8} {x['grounding']:<10} {x['sources']:<8} "
              f"{x['tool_calls']:<11} {x['queries']:<8} {', '.join(x['flagged'][:6])}")
    tot = lambda g, k: sum(len(x[k]) for x in rows if x["graph"] == g)
    exp = sum(len(x["facts_expected"]) for x in rows if x["graph"])
    print(f"\n  facts correct: with FalkorDB {tot(True, 'facts_found')}/{exp}, without {tot(False, 'facts_found')}/{exp}")
    print(f"  names not from the graph: with {tot(True, 'flagged')}, without {tot(False, 'flagged')}")
    json.dump(rows, open("docs/with_without.json", "w"), indent=2)
    md = ["# The same questions with and without FalkorDB", "",
          "Generated by `python scripts/compare_graph.py`. Same model, same question; the only difference is "
          "whether the agent has the graph tools. Expected facts are computed from the graph by the tools, not by the model.", "",
          "| Q | Mode | Facts correct | Grounded names | Sources cited | Names not from the graph |", "|---|---|---|---|---|---|"]
    for x in rows:
        md.append(f"| {x['q']} | {'with FalkorDB' if x['graph'] else 'without FalkorDB'} | {len(x['facts_found'])}/{len(x['facts_expected'])} "
                  f"| {x['grounding']} | {x['sources']} | {', '.join(x['flagged'][:8]) or '-'} |")
    for i in sorted({x["q"] for x in rows}):
        pair = [x for x in rows if x["q"] == i]
        md += ["", f"## Q{i}. {pair[0]['question']}", "", f"Expected facts (from the graph): {', '.join(pair[0]['facts_expected'])}"]
        for x in pair:
            md += ["", f"### {'With FalkorDB' if x['graph'] else 'Without FalkorDB'}", "", x["answer"].strip()]
    open("docs/with_without.md", "w").write("\n".join(md) + "\n")
    print("\nwrote docs/with_without.md and docs/with_without.json")


if __name__ == "__main__":
    main()
