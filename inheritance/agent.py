"""Inheritance agent: Claude picks a graph tool; the graph answers.

The model never writes Cypher. It chooses from a fixed toolbox (tools.py), every handle
and path is validated against the graph, and every claim cites the OWNERS file or KEP
it came from. It answers from people's public record, never as them.
"""
import json
import os
import sys

import anthropic

from . import tools

MODEL = os.environ.get("INHERITANCE_MODEL", "claude-opus-5-5")

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


def ask(question, history=None, log=lambda s: print(s, file=sys.stderr), max_turns=20):
    """Run one question through the tool loop. Returns (answer, messages, trace)."""
    client = _client()
    messages = list(history or []) + [{"role": "user", "content": question}]
    specs = tools.tool_specs()
    trace = []
    for _ in range(max_turns):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            tools=specs,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            return "The model declined this request.", messages, trace
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "max_tokens":
            return "Stopped: the answer hit the token limit.", messages, trace
        uses = [b for b in response.content if b.type == "tool_use"]
        if not uses:
            return "\n".join(b.text for b in response.content if b.type == "text"), messages, trace
        results = []
        for u in uses:
            log(f"  → {u.name}({json.dumps(u.input)})")
            content, is_err = _run_tool(u.name, u.input, log)
            trace.append({"tool": u.name, "input": u.input, "error": is_err})
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": content, "is_error": is_err})
        messages.append({"role": "user", "content": results})
    return "Stopped after too many tool calls.", messages, trace


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
            answer, history, _ = ask(q, history)
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
        print("\n" + answer)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(ask(" ".join(sys.argv[1:]))[0])
    else:
        repl()
