# Inheritance: demo video script (~4 min)

Setup before recording:
- `python -m inheritance.load --reset --as-of 2026-10-01`, so the graph starts on Oct 1.
- Open the FalkorDB Browser at http://localhost:3000.
- Have the visuals page open.

The agent beats use `python -m inheritance.agent`. `./scripts/demo.sh` replays the same
beats without the LLM.

**1. Hook (0:00–0:20).** "When someone leaves a company, their badge stops working the same
day. What they knew stays scattered across the code they owned, the decisions they made and
the people they worked with. Inheritance maps all of that as a graph, keeps it current, and
does the hand-off work."

**2. Real data (0:20–0:40).** "This is Kubernetes, one of the largest engineering
organizations in open source. Its ownership, team and design-decision records are public:
850 people, 586 code areas, 683 design decisions with their design documents, and 137 real
departures. When a maintainer steps away, their name moves to an emeritus list." *Show
lavalamp's neighbourhood in the FalkorDB Browser.*

**3. Who do I ask? Shallow vs deep (0:40–1:05).** Ask: *"Who approves
pkg/apis/core/validation/validation.go? First check only its own folder."* "Its own
folder's OWNERS file names reviewers only. Nobody can approve there." Then: *"Now follow the
ownership tree."* "Two levels up, pkg/apis/OWNERS names six approvers. That's how Kubernetes'
own tooling works, and for 1,829 files the answer only exists by walking the tree."

**4. A real departure (1:05–1:25).** Ask: *"What did lavalamp leave behind, and who carries
it now?"* "31 code areas and 39 design decisions, and nothing went dark. deads2k, liggitt and
sttts already approve 22, 20 and 18 of those areas."

**5. What if, then act (1:25–2:05).** Ask: *"If thockin left tomorrow, what would go dark?
Draft the hand-off."* "137 areas. Two would lose their last local approver and eight would
be down to one person. Twelve design decisions would have nobody left who wrote or approved
them, IPVS load balancing among them." *Open the hand-off note and the patch.* "For each
at-risk area it proposes a successor, someone who already reviews that code, and writes an
OWNERS patch that applies cleanly. That's a PR the project could open today."

**6. Keeping current, live (2:05–2:45).** "The graph is from October 1. On October 3 this
merged." Run `python -m inheritance.sync --steps 1`. "PR #142237, 'Move myself to emeritus'.
The graph rewrites only the 35 folders that changed." Ask: *"Who covers what rexagod used to
approve? Are any records out of date?"* "Every one of those 30 areas still has six
approvers, with the PR cited. But sigs.yaml still lists rexagod as SIG Instrumentation lead,
and the graph flags that." Then run `python -m inheritance.sync --verify`. "Replaying every
change gives exactly the graph a fresh load would."

**7. Why, and who knows (2:45–3:10).** Ask: *"Why do sidecar containers restart during pod
termination the way they do, and who should I ask?"* "It finds the design decision, quotes
the motivation and the alternative the authors rejected, and routes the question to
sergeykanzhelev, with matthyx as backup."

**8. Honest gaps (3:10–3:20).** Ask: *"Who owns the Postgres driver?"* "It says plainly that
it's not in the graph. It doesn't make something up."

**9. Proof (3:20–3:45).** *Show the visuals' proof section.* "Every file's answer matches an
independent walk of the raw files: 31,441 out of 31,441. Delete any one kind of
relationship and answers change. Every answer comes with a receipt, and a grounding check
flags any name the graph didn't return."

**10. Close (3:45–4:00).** "Inheritance answers from people's public record, with
attribution, never as them. Built on FalkorDB and Claude. The code, data model, queries and
setup steps are in the repo."
