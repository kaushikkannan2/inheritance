"""Inheritance agent: Claude picks a graph tool; the graph answers.

The model never writes Cypher. It chooses from a fixed toolbox (tools.py), every handle
and path is validated against the graph, and every claim cites the OWNERS file or KEP
it came from. It answers from people's public record, never as them.
"""
import json
import os
import sys

import anthropic

from . import receipt, tools

MODEL = os.environ.get("INHERITANCE_MODEL", "claude-opus-5-5")

# The control arm for the grounding check: same model, same question, no graph.
NO_GRAPH_SYSTEM = """You are Inheritance, the memory of an engineering organization: what people know,
own and decided, and who carries it when they leave. Your only source of truth is a
FalkorDB graph of the Kubernetes project's public records: every OWNERS file in
kubernetes/kubernetes (who may approve which code, including emeritus approvers who have
stepped away), sigs.yaml (teams, leads, former leads), every KEP (design decisions, who
authored and approved them, and their design documents), and the ownership changes synced
from merged pull requests since the graph was loaded. You reach it only through the tools.

How to work:
- Resolve handles first with `find_person` if you have not seen one in a tool result.
- Report outcomes honestly. Tools return an `outcome`: answered, not_in_graph, not_in_repo,
  no_approvers_within_reach, or an "Unknown entity" error. Say "not in the graph" plainly;
  never present a gap in the records as "nothing exists". Never guess.
- "Who approves / who do I ask about this code": `who_approves`; name the OWNERS file and,
  if present, the latest ownership change (PR) behind it. `max_climb=0` gives the shallow view.
- "Who knows about X": `who_knows`. "Why does X work this way": `why`; quote the design
  document's summary or motivation briefly and cite the KEP and its URL.
- "What if X leaves": `departure_impact` (also_leaving for a group), then `heirs`.
  For someone who already left, say what they left behind and who holds it now. If
  `stepped_back_recently` is present, the departure arrived through a synced PR: cite it.
- "What changed": `recent_changes`. Records that disagree: `flag_stale_records`.
- Act when asked: `draft_handoff` writes a hand-off note and an OWNERS patch file;
  `route_question` routes a question to an expert and records it. Name the files written.
- Org health: `single_points_of_failure`, `orphaned_knowledge`, `shadow_teams`
  (community detection vs official teams), `keystone_people` (betweenness).
- Save conclusions with `record_scenario` when asked; `recall` shows earlier sessions.
- Provenance policy: attribute everything to the public record. Never imitate a person,
  speak in their voice, or speculate about why they left or their private life.
- The records hold GitHub handles only, not pronouns. Refer to people by handle or as
  "they/them"; never guess gender from a handle.
- Keep the final answer short: verdict first, then evidence with sources, then what to do."""

SYSTEM = """You are Inheritance, the memory of an engineering organization: what people know,
own and decided, and who carries it when they leave. Your only source of truth is a
FalkorDB graph of the Kubernetes project's public records: every OWNERS file in
kubernetes/kubernetes (who may approve which code, including emeritus approvers who have
stepped away), sigs.yaml (teams, leads, former leads) and every KEP (design decisions and
who authored and approved them). You reach it only through the provided tools.

How to work:
- Resolve handles first with `find_person` if you have not seen one in a tool result.
  If a person, file or topic is not in the graph, say so; never guess.
- For "who do I ask / who approves", use `who_approves` and name the OWNERS file.
- For "what if X leaves" use `departure_impact` (add also_leaving for a group), then
  `heirs` to say who already carries the knowledge, with their evidence. For someone
  already emeritus, describe what they left behind and who holds it now.
- For "why does this work this way", use `why` and cite the KEP, who made the decision,
  and who can still explain it.
- For org health, use `single_points_of_failure`, `orphaned_knowledge`, `shadow_teams`
  (community detection vs official teams) and `keystone_people` (betweenness).
- Save conclusions with `record_scenario` when asked; check `recall` for earlier ones.
- Provenance policy: attribute everything to the public record. Never imitate a person,
  speak in their voice, or speculate about why they left or their private life.
- The records hold GitHub handles only, not pronouns. Refer to people by handle or as
  "they/them"; never guess gender from a handle.
- Keep the final answer short: verdict first, then evidence with sources, then what to do."""


def _client():
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        sys.exit("ANTHROPIC_API_KEY is not set in this terminal. Run: export ANTHROPIC_API_KEY=...")
    # User-level keys (sk-ant-usr-...) need the workspace in a header.
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    headers = {"anthropic-workspace-id": workspace} if workspace else None
    return anthropic.Anthropic(default_headers=headers)


def _run_tool(name, args, log):
    try:
        out = tools.call(name, args)
        log(f"  ← {name}: ok")
        return json.dumps(out, default=str)[:20000], False
    except tools.ToolError as e:
        log(f"  ← {name}: rejected ({e})")
        return f"Tool rejected the arguments: {e}", True
    except Exception as e:  # database / query failure: report, let the model adapt
        log(f"  ← {name}: error ({type(e).__name__}: {e})")
        return f"Tool failed: {type(e).__name__}: {e}", True


def ask(question, history=None, log=lambda s: print(s, file=sys.stderr), max_turns=20, use_graph=True):
    """Run one question through the tool loop. Returns (answer, messages, receipt).
    use_graph=False sends the same question with no tools, as a control."""
    client = _client()
    messages = list(history or []) + [{"role": "user", "content": question}]
    specs = tools.tool_specs() if use_graph else []
    trace, outputs, start = [], [], len(tools.LEDGER)
    brief = tools.memory_brief() if use_graph else ""

    def done(answer):
        r = receipt.build(question, answer, trace, tools.LEDGER[start:], outputs, use_graph, brief)
        return answer, messages, r

    for _ in range(max_turns):
        extra = {"tools": specs} if specs else {}
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=(SYSTEM + "\n\nMemory at session start (from the graph):\n" + brief) if use_graph else NO_GRAPH_SYSTEM,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            **extra,
        )
        if response.stop_reason == "refusal":
            return done("The model declined this request.")
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "max_tokens":
            return done("Stopped: the answer hit the token limit.")
        uses = [b for b in response.content if b.type == "tool_use"]
        if not uses:
            return done("\n".join(b.text for b in response.content if b.type == "text"))
        results = []
        for u in uses:
            log(f"  → {u.name}({json.dumps(u.input)})")
            content, is_err = _run_tool(u.name, u.input, log)
            trace.append({"tool": u.name, "input": u.input, "error": is_err})
            if not is_err:
                outputs.append(content)
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": content, "is_error": is_err})
        messages.append({"role": "user", "content": results})
    return done("Stopped after too many tool calls.")


def _answer(q, history, use_graph=True):
    answer, history, r = ask(q, history, use_graph=use_graph)
    print("\n" + answer)
    print(receipt.render(r), file=sys.stderr)
    print(f"receipt saved: {receipt.save(r)}", file=sys.stderr)
    return history


def repl():
    print("Inheritance. Ask who owns what, what leaves when someone leaves, and why things are the way they are (Ctrl-D to quit).")
    history = []
    while True:
        try:
            q = input("\n> ").strip()
        except EOFError:
            break
        if not q:
            continue
        try:
            history = _answer(q, history)
        except anthropic.AuthenticationError:
            print("\nThe API key was rejected. Create a new key in the Anthropic Console and export it again.")
            continue
        except anthropic.BadRequestError as e:
            msg = getattr(e, "message", str(e))
            if "workspace" in msg:
                print("\nThis key needs a workspace. Run: export ANTHROPIC_WORKSPACE_ID=wrkspc_...  "
                      "(Console -> Settings -> Workspaces), then start again.")
            else:
                print(f"\nThe API rejected the request: {msg}")
            continue
        except anthropic.APIConnectionError:
            print("\nCould not reach the Anthropic API. Check your internet connection.")
            continue


if __name__ == "__main__":
    args = sys.argv[1:]
    use_graph = "--no-graph" not in args
    args = [a for a in args if a != "--no-graph"]
    if args:
        _answer(" ".join(args), None, use_graph)
    else:
        repl()
