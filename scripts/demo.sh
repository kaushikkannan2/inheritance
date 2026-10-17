#!/usr/bin/env bash
# Load the graph and replay the demo beats with the tools directly (no LLM needed).
set -euo pipefail
cd "$(dirname "$0")/.."
cli() { python -m inheritance.cli "$@"; }
short() { python -c "import json,sys; d=json.load(sys.stdin); print(json.dumps({k:d[k] for k in sys.argv[1:] if k in d}, indent=2))" "$@"; }
echo "== 0. load the public Kubernetes records"
python -m inheritance.load --reset
echo "== 1. who do I ask? (OWNERS inheritance, with the file cited)"
cli approves pkg/kubelet/kuberuntime/kuberuntime_container.go | short governed_by nearest_approvers
echo "== 2. a real departure: what lavalamp left behind"
cli leaves lavalamp | short status areas areas_with_no_local_approver areas_down_to_one decisions
echo "== 3. and who carried it"
cli heirs lavalamp | short heirs
echo "== 4. what if a keystone left today?"
cli leaves thockin | short areas areas_with_no_local_approver areas_down_to_one dark_areas decisions_left_with_nobody_active examples_of_those_decisions
echo "== 5. ... or three of them at once?"
cli leaves thockin liggitt deads2k | short leaving_together areas areas_down_to_one decisions_left_with_nobody_active leadership_seats
echo "== 6. why does it work this way?"
cli why sidecar containers | short decisions
echo "== 7. org health: single points of failure, orphaned decisions"
cli spof | short areas_with_one_active_approver largest
cli orphans | short implemented_decisions with_nobody_active_left examples
echo "== 8. shadow teams (label propagation) and keystones (betweenness)"
cli shadow | short communities cross_team_groups
cli keystones
