# Inheritance: demo video script (~3 min)

Run `./scripts/demo.sh` for the tool output, or the agent for the live beats.

**1. Hook (0:00–0:20).** "When someone leaves a company, their badge stops working the same
day. What they knew stays scattered across code they owned, decisions they made and
people they worked with. Inheritance maps all of that as a graph, so you can see what
leaves with a person and who already carries it."

**2. The data (0:20–0:40).** "This is real. Kubernetes is one of the largest engineering
organizations in the world, and it keeps public records of who owns which code and who
made which design decisions. When a maintainer steps away, their name moves to an emeritus
list. That gives us 853 people, 586 code areas, 683 design decisions, and 137 real
departures." *Show the FalkorDB browser on lavalamp's neighbourhood.*

**3. Who do I ask? (0:40–1:00).** Ask: *"Who do I ask about
pkg/kubelet/kuberuntime/kuberuntime_container.go?"* "The graph climbs the folder tree to
the nearest OWNERS file, the same way Kubernetes' own tooling does, and cites it. It also
shows who used to own this code."

**4. A real departure (1:00–1:30).** Ask: *"What did lavalamp leave behind, and who carries
it now?"* "Lavalamp stepped back from 31 code areas and 39 design decisions. Nothing went
dark. Deads2k, liggitt and sttts already approve 22, 20 and 18 of those areas. This is what a
healthy inheritance looks like, and the graph can prove it."

**5. What if? (1:30–2:05).** Ask: *"If thockin left tomorrow, what would go dark?"* "137
code areas. One loses its last local approver, nine drop to a single person, and 12 design
decisions would have nobody left who wrote or approved them, like IPVS load balancing."
Then: *"What if liggitt and deads2k left with him?"* "239 areas, 35 hanging by one person,
28 orphaned decisions, three team leadership seats."

**6. Why is it like this? (2:05–2:25).** Ask: *"Why do sidecar containers work the way they
do, and who can still explain it?"* "It finds the design decisions, who made them, and
who's still around to ask."

**7. Org health (2:25–2:45).** "Across the whole org, 45 areas depend on a single approver.
Community detection finds the real teams. One group of 48 people spans eight official
teams. Betweenness centrality finds the keystones who connect everyone."

**8. Responsible by design (2:45–3:00).** "Inheritance answers from people's public
record, with attribution. It never imitates anyone or guesses why they left. Built on
FalkorDB and Claude. The code, data model and queries are in the repo."
