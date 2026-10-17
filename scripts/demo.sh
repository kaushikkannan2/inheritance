#!/usr/bin/env bash
# Replay the demo beats with the tools directly (no LLM needed).
set -euo pipefail
cd "$(dirname "$0")/.."
cli() { python -m inheritance.cli "$@"; }
short() { python -c "import json,sys; d=json.load(sys.stdin); print(json.dumps({k:d[k] for k in sys.argv[1:] if k in d}, indent=2))" "$@"; }
echo "== 0. load the public Kubernetes records as they were on Oct 1, 2026"
python -m inheritance.load --reset --as-of 2026-10-01
echo "== 1. who do I ask? Shallow (own folder only) vs following the OWNERS tree"
cli approves pkg/apis/core/validation/validation.go --climb 0 | short outcome nearest_approvers
cli approves pkg/apis/core/validation/validation.go | short outcome climbed nearest_approvers
echo "== 2. a real departure: what lavalamp left behind, and who carried it"
cli leaves lavalamp | short status areas areas_with_no_local_approver areas_down_to_one decisions
cli heirs lavalamp | short heirs
echo "== 3. what if a keystone left today? ... or three at once?"
cli leaves thockin | short areas areas_with_no_local_approver areas_down_to_one decisions_left_with_nobody_active
cli leaves thockin liggitt deads2k | short leaving_together areas areas_down_to_one decisions_left_with_nobody_active leadership_seats
echo "== 4. ACT: draft the hand-off note and OWNERS patch"
cli handoff thockin | short areas_needing_successor patches_ready files
echo "== 5. why does it work this way, and who knows? (design documents)"
cli why sidecar containers | python -c "import json,sys; d=json.load(sys.stdin)['decisions'][0]; print(json.dumps({k:d[k] for k in ['kep','title','can_still_explain','design_doc']}, indent=2))"
cli knows sidecar containers | short people
cli route pkg/proxy/conntrack/cleanup.go "Why does kube-proxy clear this conntrack entry?"
echo "== 6. LIVE: a real ownership change arrives (merged PR), the graph updates in place"
python -m inheritance.sync --list | head -3
python -m inheritance.sync --steps 1
cli leaves rexagod | short status stepped_back_recently
cli flags | short flags
echo "== 7. apply the rest, then prove the replayed graph equals a fresh load"
python -m inheritance.sync --verify | tail -6
echo "== 8. org health: single points of failure, orphaned decisions, shadow teams, keystones"
cli spof | short areas_with_one_active_approver
cli orphans | short implemented_decisions with_nobody_active_left
cli shadow | short communities
cli keystones | short keystones
echo "== 9. honest gaps: things that are not in the graph"
cli knows postgres
cli approves pkg/postgres/driver.go | short outcome message
echo "== 10. memory: what this session left in the graph"
cli recall | short handoffs questions flags
