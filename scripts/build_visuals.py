"""Build docs/visuals/index.html from the live graph and docs/proof.json.

  python scripts/build_visuals.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from inheritance import tools  # noqa: E402

os.chdir(os.path.join(os.path.dirname(__file__), ".."))


def chain(path):
    d = tools._nearest_dir(path)
    out = []
    for p in tools._chain(d):
        n = tools._q("MATCH (x:Person {status:'active'})-[:APPROVES]->(:Dir {path:$p}) RETURN count(x)", {"p": p})[0][0]
        f = tools._q("MATCH (x:Person)-[:EMERITUS_OF]->(:Dir {path:$p}) RETURN collect(x.handle)", {"p": p})[0][0]
        out.append(dict(path=p, approvers=n, former=sorted(f)))
        if n:
            break
    return dict(file=path, steps=out, answer=tools.who_approves(path)["nearest_approvers"])


proof = json.load(open("docs/proof.json"))
lav = tools.departure_impact("lavalamp")
data = dict(
    proof=dict(integrity=proof["integrity"], correctness={k: v for k, v in proof["correctness"].items() if k != "differ"},
               ablation=dict(questions=proof["ablation"]["questions"],
                             without={k: v["changed"] for k, v in proof["ablation"]["without"].items()}),
               grounding=dict(t=proof["grounding"]["fixture_true"], f=proof["grounding"]["fixture_false"]),
               receipts=proof["receipts"]),
    status=dict(tools._q("MATCH (p:Person) RETURN p.status, count(*)")),
    keps=tools._q("MATCH (k:KEP) RETURN count(k)")[0][0],
    who=[chain("pkg/kubelet/kuberuntime/kuberuntime_container.go"), chain("pkg/apis/core/validation/validation.go")],
    lavalamp=dict(areas=lav["areas"], decisions=lav["decisions"], dark=lav["areas_with_no_local_approver"],
                  thin=lav["areas_down_to_one"], heirs=tools.heirs("lavalamp", 5)["heirs"],
                  sample=tools.whois("lavalamp")["emeritus_of"]),
    whatif=[tools.departure_impact("thockin"), tools.departure_impact("thockin", ["liggitt", "deads2k"])],
    spof=tools.single_points_of_failure(6), orphans=tools.orphaned_knowledge(4),
    shadow=tools.shadow_teams(), keystones=tools.keystone_people(6)["keystones"],
    why=tools.why("sidecar containers", 2)["decisions"],
)
HANDOFF = ["pkg/registry", "pkg/controlplane", "pkg/kubeapiserver", "staging/src/k8s.io/apimachinery",
           "staging/src/k8s.io/apiserver", "staging/src/k8s.io/client-go"]
top = [h["heir"] for h in data["lavalamp"]["heirs"][:3]]
data["handoff"] = dict(heirs=top, areas=[dict(area=a, held_by=[r[0] for r in tools._q(
    "MATCH (x:Person)-[:APPROVES]->(:Dir {path:$a}) WHERE x.handle IN $h RETURN x.handle ORDER BY x.handle", {"a": a, "h": top})])
    for a in HANDOFF])
from inheritance import actions  # noqa: E402
ho = actions.draft_handoff("thockin", write=False)
data["handoff_plan"] = [dict(area=p["area"], left=p["left"], proposed=p["proposed"], because=p["because"][:2]) for p in ho["plan"]]
first = next((p for p in ho["plan"] if p["proposed"]), None)
data["patch_sample"] = actions._patch_owners(first["area"], first["proposed"]) if first else ""
data["incremental"] = proof.get("incremental")
for w in data["whatif"]:
    w["safe"] = w["areas"] - w["areas_with_no_local_approver"] - w["areas_down_to_one"]
html = open("scripts/visuals_template.html").read().replace("__DATA__", json.dumps(data, default=str))
open("docs/visuals/index.html", "w").write(html)
print(f"wrote docs/visuals/index.html ({len(html) // 1024} KB)")
