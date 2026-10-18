# Inheritance: demo video script (~3 min, terminal only)

The video shows the agent running from start to finish. No slides.

**Before recording**
- Start FalkorDB with `docker compose up -d`.
- In the terminal: `export ANTHROPIC_API_KEY=...`. Do this off camera, and never show the key.
- Load the graph as of Oct 1: `python -m inheritance.load --reset --as-of 2026-10-01`
- Make the terminal font large. The agent prints each tool call as it runs and a receipt
  after each answer. Leave both on screen; they show the graph doing the work.

**1. Start (0:00–0:15).** Run `python -m inheritance.agent`.
Say: "When someone leaves a company, what they knew leaves with them. Inheritance is an AI
agent that answers from a FalkorDB graph of Kubernetes' public records: who owns which code,
who made which design decisions, and who works with whom."

**2. Who do I ask? (0:15–0:40).** Type: *Who can approve pkg/apis/core/validation/validation.go?*
Say: "Its own folder names nobody who can approve. The agent climbs the folder tree in the
graph and finds six approvers two levels up. For 1,829 files, that climb is the only way to
get the answer."

**3. A real departure (0:40–1:00).** Type: *What did lavalamp leave behind, and who carries it now?*
Say: "31 code areas and 39 design decisions, and none went dark. deads2k, liggitt and sttts
already approve most of those areas."

**4. What if, then act (1:00–1:35).** Type: *If thockin left tomorrow, what would go dark? Draft the hand-off.*
Say: "Two areas would lose every approver, eight would be down to one person, and twelve
design decisions would have nobody left who can explain them. The agent then writes a
hand-off note and an OWNERS patch." Open `out/handoffs/` and show the patch for a second.

**5. Live update (1:35–2:10).** Quit with Ctrl-D. Run `python -m inheritance.sync --steps 1`.
Say: "On October 3 a real pull request moved a maintainer to emeritus. The graph rewrites
only the folders it touched." Start the agent again and type: *Who covers what rexagod used
to approve? Are any records out of date?*
Say: "All 30 areas still have six approvers, and it flags that the team roster still lists
rexagod as a lead."

**6. Honest gap (2:10–2:20).** Type: *Who owns the Postgres driver?*
Say: "It isn't in the graph, so the agent says so instead of guessing."

**7. Without the graph (2:20–2:50).** Quit. Run
`python -m inheritance.agent --no-graph "What did lavalamp leave behind, and who carries it now?"`
Say: "Same AI, same question, no FalkorDB. It can't name the areas or the people. Across our
test questions: 19 of 19 facts right with the graph, 0 of 19 without." Show `docs/with_without.md`.

**8. Close (2:50–3:00).** Say: "Every answer comes from the graph, with a receipt. Code, graph
map and queries are in the repo."

`./scripts/demo.sh` runs the same steps through the tools directly, without the AI.
