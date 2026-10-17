"""Run any agent tool directly, without the language model (for demos and testing).

  python -m inheritance.cli whois lavalamp
  python -m inheritance.cli approves pkg/kubelet/kuberuntime/kuberuntime_container.go
  python -m inheritance.cli leaves thockin
  python -m inheritance.cli leaves thockin liggitt deads2k      # group departure
  python -m inheritance.cli heirs lavalamp
  python -m inheritance.cli why "sidecar containers"
  python -m inheritance.cli spof
  python -m inheritance.cli orphans
  python -m inheritance.cli shadow
  python -m inheritance.cli keystones
  python -m inheritance.cli ask "What did lavalamp leave behind, and who carries it now?"
"""
import argparse
import json

from . import tools


def main():
    ap = argparse.ArgumentParser(description="Inheritance tools")
    ap.add_argument("cmd", choices=["find", "whois", "approves", "leaves", "heirs", "why", "spof",
                                    "orphans", "shadow", "keystones", "recall", "ask"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args()
    x = a.args
    try:
        if a.cmd == "ask":
            from .agent import ask
            print(ask(" ".join(x))[0])
            return
        out = {
            "find": lambda: tools.find_person(x[0]),
            "whois": lambda: tools.whois(x[0]),
            "approves": lambda: tools.who_approves(x[0]),
            "leaves": lambda: tools.departure_impact(x[0], x[1:]),
            "heirs": lambda: tools.heirs(x[0]),
            "why": lambda: tools.why(" ".join(x)),
            "spof": lambda: tools.single_points_of_failure(),
            "orphans": lambda: tools.orphaned_knowledge(),
            "shadow": lambda: tools.shadow_teams(),
            "keystones": lambda: tools.keystone_people(),
            "recall": lambda: tools.recall(x[0] if x else None),
        }[a.cmd]
        print(json.dumps(out(), indent=2, default=str))
    except tools.ToolError as e:
        print(f"error: {e}")
    except IndexError:
        ap.error("missing argument; see the examples in --help")


if __name__ == "__main__":
    main()
