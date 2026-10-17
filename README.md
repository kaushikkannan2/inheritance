# Inheritance

**When someone leaves, what leaves with them?**

Inheritance is a company brain for departures. It maps who owns which code, who made which
design decisions, and who works with whom, then answers questions like:

- *Who do I ask about this file?*
- *What did this person leave behind, and who carries it now?*
- *If this person, or this whole group, left tomorrow, what would go dark?*
- *Why does this feature work this way, and who can still explain it?*

It runs on **real, public data**: the governance records of the Kubernetes project, one of
the largest open-source organizations in the world. Kubernetes keeps unusually good
records of departures. When a maintainer steps away, their name moves to an
`emeritus_approvers` list in the code's OWNERS file, so the graph contains real history
of people leaving and of who took over.

Built for **Graph Hacks** (FalkorDB × WeMakeDevs), **Track 3**.

## Why a graph

Knowledge in an organization lives in the connections between people, code and decisions:

- Code ownership is inherited down a folder tree. To find who may approve a file you
  climb the tree until you reach an OWNERS file, and some folders stop inheritance.
- People are linked through the code they share and the decisions they made together.
- To find who carries a departed person's knowledge, you rank their neighbours.
- To find the real teams, as opposed to the org chart, you run community detection.
- To find the people who hold the organization together, you run betweenness centrality.

All of these are graph traversals or graph algorithms. In tables they become recursive
self-joins and offline batch jobs.

## What it found (Kubernetes, October 2026)

| | |
|---|---|
| People in the records | 852: 260 active, 140 emeritus, 452 seen only on design docs |
| Code areas with their own OWNERS file | 585, of which 116 list a former approver |
| **A real departure:** `lavalamp` | Emeritus on 31 code areas and 39 design decisions. Nothing went dark: `deads2k`, `liggitt` and `sttts` already approve 22, 20 and 18 of those areas. This is what a good inheritance looks like. |
| **What if `thockin` left today?** | 137 code areas: 1 with no local approver left, 9 down to a single approver. 12 of 107 design decisions would have nobody active left who wrote or approved them (for example IPVS load balancing and SCTP support). |
| **…together with `liggitt` and `deads2k`?** | 239 areas, 35 down to one approver, 28 decisions with nobody left, 3 leadership seats (Network, Auth, API Machinery). |
| Single points of failure | 45 areas whose OWNERS file has exactly one active approver |
| Orphaned decisions | 5 of 298 implemented design decisions have no author or approver still active |
| Shadow teams | Label propagation finds groups that cut across official teams: one 47-person group centred on Node spans 8 official teams |
| Keystones (betweenness) | `deads2k`, `liggitt`, `thockin`, `sttts`, `dims` |

"Active" means currently listed as an approver, reviewer or team lead.

## Responsible use

Inheritance answers **from** people's public record, with attribution. It never answers
**as** them. It does not imitate anyone's voice and does not guess why someone left.

- People are GitHub handles only. Names, employers and emails in `sigs.yaml` are never loaded.
- Every answer cites its source: the OWNERS file or the KEP.
- "What if X left" is a resilience exercise, not a prediction. It runs on public records
  that the project itself publishes for this exact purpose: knowing who can approve what.

## Data model

```
(:Person {handle, status: active|emeritus|unlisted})
(:Dir {path, no_parent_owners, labels, owners_file})-[:CHILD_OF]->(:Dir)      # OWNERS tree
(Person)-[:APPROVES|REVIEWS]->(Dir)                                            # current owners
(Person)-[:EMERITUS_OF]->(Dir)                                                 # former owners
(:Team {id, name, kind})          (Person)-[:LEADS {role}]->(Team)   (Person)-[:LED]->(Team)
(:Subproject {id, name})-[:PART_OF]->(Team)   (Subproject)-[:OWNS]->(Dir)
(:KEP {number, title, status, stage, milestone, created, path})
(KEP)-[:OWNED_BY]->(Team)   (KEP)-[:INVOLVES]->(Team)   (KEP)-[:REPLACES]->(KEP)
(Person)-[:AUTHORED|REVIEWED|APPROVED]->(KEP)
(Person)-[:WORKED_WITH {weight}]->(Person)   # +1 per shared OWNERS file, +0.5 per shared KEP
(:Scenario {id, at, note, dark, thin, orphan})-[:ABOUT]->(Person)              # agent memory
```

Full-text index on `KEP.title`. Sources:

| Source | What it gives | Pinned commit |
|---|---|---|
| `kubernetes/kubernetes` every `OWNERS` file + `OWNERS_ALIASES` | who may approve which code, including emeritus | `98d0129` |
| `kubernetes/community` `sigs.yaml` | teams, leads, former leads, subprojects | `440e496` |
| `kubernetes/enhancements` every `keps/**/kep.yaml` | design decisions and who made them | `6ebe0a9` |

All are Apache 2.0.

## Key Cypher queries

All are in `inheritance/tools.py`. The model never writes Cypher. It picks from these tools.

**Who approves a file.** This finds the nearest OWNERS folder, then climbs the tree, stopping
at a folder that sets `no_parent_owners`:

```cypher
MATCH (d:Dir) WHERE d.path = $p OR $p STARTS WITH (d.path + '/') OR d.path = '.'
RETURN d.path ORDER BY size(d.path) DESC LIMIT 1;

MATCH p = (d:Dir {path:$p})-[:CHILD_OF*0..25]->(a:Dir)
RETURN a.path, a.no_parent_owners, length(p) ORDER BY length(p);
```

**Departure impact.** For each area the leavers approve, this finds who is left:

```cypher
MATCH (p:Person)-[:APPROVES]->(d:Dir) WHERE p.handle IN $gone RETURN DISTINCT d.path;

MATCH (g:Person)-[:APPROVED|AUTHORED]->(k:KEP) WHERE g.handle IN $gone
WITH DISTINCT k
OPTIONAL MATCH (o:Person)-[:APPROVED|AUTHORED]->(k) WHERE NOT o.handle IN $gone AND o.status = 'active'
WITH k, collect(DISTINCT o.handle) AS still_here
RETURN k.number, k.title, k.status, k.path, still_here;
```

**Heirs.** Active people ranked by shared ownership and decisions, with the evidence:

```cypher
MATCH (p:Person {handle:$h})-[w:WORKED_WITH]-(x:Person {status:'active'})
RETURN x.handle, sum(w.weight) AS score ORDER BY score DESC LIMIT $k;

MATCH (:Person {handle:$h})-[:APPROVES|EMERITUS_OF]->(d:Dir)<-[:APPROVES]-(:Person {handle:$x})
RETURN count(DISTINCT d);
```

**Shadow teams.** Label propagation over each active person's 3 strongest collaborators,
compared with the team that owns the code each person works on:

```cypher
MATCH (a:Person {status:'active'})-[w:WORKED_WITH]-(b:Person {status:'active'})
WITH a, b, w.weight AS wt ORDER BY wt DESC
WITH a, collect(b)[0..3] AS top UNWIND top AS b CREATE (a)-[:ACTIVE_WITH]->(b);

CALL algo.labelPropagation({nodeLabels:['Person'], relationshipTypes:['ACTIVE_WITH'], maxIterations:30})
YIELD node, communityId RETURN node.handle, communityId;
```

**Keystones.** Betweenness centrality:

```cypher
CALL algo.betweenness({nodeLabels:['Person'], relationshipTypes:['ACTIVE_WITH'], samplingSize:100000, samplingSeed:7})
YIELD node, score RETURN node.handle, score ORDER BY score DESC LIMIT $k;
```

**Why.** Full-text search over design decisions:

```cypher
CALL db.idx.fulltext.queryNodes('KEP', $q) YIELD node, score RETURN node.number, score ORDER BY score DESC LIMIT $n;
```

**Single points of failure** and **orphaned decisions**:

```cypher
MATCH (x:Person {status:'active'})-[:APPROVES]->(d:Dir)
WITH d, collect(x.handle) AS ap WHERE size(ap) = 1
OPTIONAL MATCH (c:Dir)-[:CHILD_OF*1..25]->(d)
RETURN d.path, ap[0], count(DISTINCT c) AS below ORDER BY below DESC;

MATCH (k:KEP) WHERE k.status = 'implemented'
MATCH (p:Person)-[:AUTHORED|APPROVED]->(k)
WITH k, collect(p.status) AS st WHERE NOT 'active' IN st RETURN k.number, k.title;
```

## The agent

`inheritance/agent.py` is a tool-use loop with Claude. It has 12 fixed tools:

`find_person` `whois` `who_approves` `departure_impact` `heirs` `why`
`single_points_of_failure` `orphaned_knowledge` `shadow_teams` `keystone_people`
`record_scenario` `recall`

Every handle and path is checked against the graph. Bad input comes back as an error the
model can recover from, and every claim cites a source file. Scenarios the agent saves are
stored as `:Scenario` nodes, so later sessions can recall them.

## Setup

Requires Docker, Python 3.10+ and git.

```bash
git clone https://github.com/kaushikkannan2/inheritance.git && cd inheritance
docker compose up -d                       # FalkorDB on :6379, browser on http://localhost:3000
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./scripts/fetch_data.sh                    # ~110 MB sparse clones, pinned commits, ~30 s
python -m inheritance.load --reset         # builds the graph, ~5 s
./scripts/demo.sh                          # replays every demo beat without an LLM
```

Ask the agent (needs an Anthropic API key):

```bash
export ANTHROPIC_API_KEY=...               # user-level keys (sk-ant-usr-...) also need:
export ANTHROPIC_WORKSPACE_ID=wrkspc_...
python -m inheritance.agent                # interactive
python -m inheritance.agent "If thockin left tomorrow, what would go dark and who should we start pairing with?"
```

Or call the tools directly: `python -m inheritance.cli --help`.

## Limits

- Activity is read from OWNERS and leadership lists, not from commit history. Some people
  listed only on design docs (452 "unlisted") may still be active.
- Folders that inherit owners without their own OWNERS file are covered by climbing the tree.
  Per-file `filters` sections are merged into one list per folder.
- `WORKED_WITH` measures shared responsibility, not conversation.

## AI disclosure

This project was built with Claude Code (Anthropic) as a pair programmer: it wrote and
debugged much of the loader, tools, agent and docs, under my direction and review. At
runtime, the agent uses Claude through the Anthropic API.

## License

Code: MIT. Data: Kubernetes project records, Apache 2.0.
