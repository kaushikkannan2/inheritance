"""Draw a README graph map as a self-contained SVG: nodes on a grid, right-angled edges with
numbered step labels, and a step key. Usage: python graph_map.py inheritance|exploit-path OUT.svg"""
import sys
from html import escape

SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
BG, CARD, LINE, INK, MUTED, DIM = "#0D1117", "#161B22", "#30363D", "#E6EDF3", "#9198A1", "#6E7681"
NW, NH = 204, 92  # node size


def mono_w(text, size):
    return len(text) * size * 0.61


class Map:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.nodes, self.edges, self.labels, self.markers = {}, [], [], set()

    def node(self, key, x, y, label, sub, color, dashed=False):
        self.nodes[key] = dict(x=x, y=y, label=label, sub=sub, color=color, dashed=dashed)

    def port(self, key, side, offset=0):
        n = self.nodes[key]
        return {"top": (n["x"] + offset, n["y"] - NH / 2), "bottom": (n["x"] + offset, n["y"] + NH / 2),
                "left": (n["x"] - NW / 2, n["y"] + offset), "right": (n["x"] + NW / 2, n["y"] + offset)}[side]

    def edge(self, pts, rel, caption, color, step=None, seg=0, at=0.5, cap="below", dashed=False):
        """pts: polyline corners. The label sits on segment `seg` at fraction `at`."""
        self.markers.add(color)
        d = "M" + " L".join(f"{x:.0f},{y:.0f}" for x, y in pts)
        dash = ' stroke-dasharray="8 7"' if dashed else ""
        self.edges.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round"{dash} '
                          f'marker-end="url(#m{color[1:]})"/>')
        (x1, y1), (x2, y2) = pts[seg], pts[seg + 1]
        self._chip(x1 + (x2 - x1) * at, y1 + (y2 - y1) * at, rel, caption, color, step, cap, dashed)

    def _chip(self, x, y, rel, caption, color, step, cap, dashed):
        fs = 17
        badge = 28 if step else 0
        w = badge + (8 if step else 0) + mono_w(rel, fs) + 20
        left, top = x - w / 2, y - 17
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        out = [f'<rect x="{left:.0f}" y="{top:.0f}" width="{w:.0f}" height="34" rx="6" fill="{BG}" stroke="{color}" stroke-width="1.5"{dash}/>']
        tx = left + 10
        if step:
            out.append(f'<rect x="{tx - 3:.0f}" y="{top + 5:.0f}" width="{badge - 4}" height="24" rx="4" fill="{color}"/>'
                       f'<text x="{tx - 3 + (badge - 4) / 2:.0f}" y="{top + 23:.0f}" text-anchor="middle" font-family="{MONO}" '
                       f'font-size="16" font-weight="700" fill="{BG}">{step}</text>')
            tx += badge + 4
        out.append(f'<text x="{tx:.0f}" y="{top + 23:.0f}" font-family="{MONO}" font-size="{fs}" font-weight="600" fill="{color}">{escape(rel)}</text>')
        if caption:
            if cap == "below":
                cx, cy, anchor = x, y + 42, "middle"
            elif cap == "above":
                cx, cy, anchor = x, y - 28, "middle"
            elif cap == "right":
                cx, cy, anchor = left + w + 12, y + 6, "start"
            else:
                cx, cy, anchor = left - 12, y + 6, "end"
            cw = len(caption) * 17 * 0.52 + 12
            bx = cx - cw / 2 if anchor == "middle" else (cx - 6 if anchor == "start" else cx - cw + 6)
            out.append(f'<rect x="{bx:.0f}" y="{cy - 18:.0f}" width="{cw:.0f}" height="24" rx="4" fill="{BG}"/>'
                       f'<text x="{cx:.0f}" y="{cy:.0f}" text-anchor="{anchor}" font-family="{SANS}" font-size="17" '
                       f'fill="{MUTED}">{escape(caption)}</text>')
        self.labels.append("".join(out))

    def _node(self, n):
        x, y, c = n["x"] - NW / 2, n["y"] - NH / 2, n["color"]
        dash = ' stroke-dasharray="6 5"' if n["dashed"] else ""
        return (f'<rect x="{x:.0f}" y="{y:.0f}" width="{NW}" height="{NH}" rx="10" fill="{CARD}" stroke="{LINE}" stroke-width="1.5"{dash}/>'
                f'<rect x="{x + 1.5:.1f}" y="{y + 1.5:.1f}" width="{NW - 3}" height="6" rx="3" fill="{c}"/>'
                f'<text x="{n["x"]:.0f}" y="{n["y"] + 2:.0f}" text-anchor="middle" font-family="{MONO}" font-size="22" font-weight="700" '
                f'fill="{c}">{escape(n["label"])}</text>'
                f'<text x="{n["x"]:.0f}" y="{n["y"] + 30:.0f}" text-anchor="middle" font-family="{SANS}" font-size="16" '
                f'fill="{MUTED}">{escape(n["sub"])}</text>')

    def render(self, kicker, title, steps, legend):
        W, H = self.w, self.h
        defs = ['<pattern id="dots" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1.2" fill="#1C2129"/></pattern>']
        for c in self.markers:
            defs.append(f'<marker id="m{c[1:]}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
                        f'<path d="M0,1 L9,5 L0,9 z" fill="{c}"/></marker>')
        body = [f'<rect x="0.75" y="0.75" width="{W - 1.5}" height="{H - 1.5}" rx="14" fill="{BG}" stroke="{LINE}" stroke-width="1.5"/>',
                f'<rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="14" fill="url(#dots)"/>',
                f'<text x="56" y="66" font-family="{MONO}" font-size="18" fill="{DIM}">// {escape(kicker)}</text>',
                f'<text x="56" y="110" font-family="{SANS}" font-size="38" font-weight="700" fill="{INK}">{escape(title)}</text>']
        body += self.edges
        body += [self._node(n) for n in self.nodes.values()]
        body += self.labels
        cols = 3 if len(steps) > 4 else len(steps)
        rows = -(-len(steps) // cols)
        top = H - 64 - rows * 92
        body.append(f'<line x1="56" y1="{top - 28}" x2="{W - 56}" y2="{top - 28}" stroke="{LINE}" stroke-width="1.5"/>')
        colw = (W - 112 + 20) / cols
        for i, (num, head, rel, color) in enumerate(steps):
            x, y = 56 + (i % cols) * colw, top + (i // cols) * 92
            body.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{colw - 20:.0f}" height="76" rx="8" fill="{CARD}" stroke="{LINE}" stroke-width="1.5"/>'
                        f'<rect x="{x + 16:.0f}" y="{y + 22:.0f}" width="32" height="32" rx="6" fill="{color}"/>'
                        f'<text x="{x + 32:.0f}" y="{y + 45:.0f}" text-anchor="middle" font-family="{MONO}" font-size="18" font-weight="700" fill="{BG}">{num}</text>'
                        f'<text x="{x + 64:.0f}" y="{y + 33:.0f}" font-family="{SANS}" font-size="19" font-weight="600" fill="{INK}">{escape(head)}</text>'
                        f'<text x="{x + 64:.0f}" y="{y + 58:.0f}" font-family="{MONO}" font-size="15" fill="{color}">{escape(rel)}</text>')
        lx = 56
        for label, color in legend:
            body.append(f'<rect x="{lx}" y="{H - 41}" width="14" height="14" rx="3" fill="{color}"/>'
                        f'<text x="{lx + 22}" y="{H - 28}" font-family="{MONO}" font-size="16" fill="{MUTED}">{escape(label)}</text>')
            lx += 22 + mono_w(label, 16) + 30
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
                f'aria-label="{escape(title)}"><defs>{"".join(defs)}</defs>{"".join(body)}</svg>')


def inheritance():
    PERSON, CODE, CLIMB, DEC, TEAM, CHANGE, MEM = "#A78BFA", "#F0883E", "#FFA657", "#3FB9A2", "#F778BA", "#58A6FF", "#E3B341"
    c1, c2, c3, c4 = 160, 590, 1020, 1450
    r0, r1, r2, r3 = 220, 400, 580, 760
    m = Map(1600, 1160)
    m.node("chg", c3, r0, ":Change", "merged PR, live", CHANGE)
    m.node("dir", c3, r1, ":Dir", "code folder", CODE)
    m.node("parent", c4, r1, ":Dir", "parent folder", CODE)
    m.node("hand", c1, r2, ":Handoff", "written by the agent", MEM, dashed=True)
    m.node("p", c2, r2, ":Person", "leaving", PERSON)
    m.node("kep", c3, r2, ":KEP", "design decision", DEC)
    m.node("doc", c4, r2, ":Document", "why it was built", DEC)
    m.node("heir", c1, r3, ":Person", "carries it on", PERSON)
    m.node("team", c3, r3, ":Team", "team roster", TEAM)
    m.edge([m.port("p", "top"), (c2, r1), m.port("dir", "left")], "APPROVES", "what they own", CODE, step=1, seg=1, at=0.5)
    m.edge([m.port("dir", "right"), m.port("parent", "left")], "CHILD_OF", "climb for backup", CLIMB, step=2)
    m.edge([m.port("p", "right"), m.port("kep", "left")], "AUTHORED", "what they decided", DEC, step=3)
    m.edge([m.port("doc", "left"), m.port("kep", "right")], "DESCRIBES", "the why", DEC, step=3)
    m.edge([m.port("p", "bottom", -50), (c2 - 50, r3), m.port("heir", "right")], "WORKED_WITH", "who carries it on", PERSON, step=4, seg=1, at=0.5)
    m.edge([m.port("hand", "right"), m.port("p", "left")], "FOR", "hand-off + patch", MEM, step=5)
    m.edge([m.port("chg", "bottom"), m.port("dir", "top")], "TOUCHED", "keeps it current", CHANGE, step=6, cap="right")
    m.edge([m.port("p", "bottom", 50), (c2 + 50, r3), m.port("team", "left")], "LEADS", "stale-lead check", TEAM, seg=1, at=0.55, dashed=True)
    steps = [("1", "What they own", "(:Person)-[:APPROVES]->(:Dir)", CODE),
             ("2", "Who else can approve", "(:Dir)-[:CHILD_OF*]->(:Dir)", CLIMB),
             ("3", "What they decided, and why", "-[:AUTHORED]->(:KEP)<-[:DESCRIBES]-", DEC),
             ("4", "Who carries it on", "(:Person)-[:WORKED_WITH]-(:Person)", PERSON),
             ("5", "Act: hand-off + OWNERS patch", "(:Handoff)-[:FOR]->(:Person)", MEM),
             ("6", "Stay current", "(:Change)-[:TOUCHED]->(:Dir)", CHANGE)]
    legend = [("person", PERSON), ("code", CODE), ("decision", DEC), ("team", TEAM), ("change", CHANGE), ("agent-written", MEM)]
    return m.render("inheritance · graph map", "What the agent walks when someone leaves", steps, legend)


def exploit_path():
    APP, DEP, VULN, FIX, MEM = "#58A6FF", "#F0883E", "#F85149", "#3FB950", "#A78BFA"
    c1, c2, c3, c4 = 160, 590, 1020, 1450
    r1, r2 = 260, 510
    m = Map(1600, 830)
    m.node("app", c1, r1, ":App", "your service", APP)
    m.node("v1", c2, r1, ":Version", "direct dependency", DEP)
    m.node("v2", c3, r1, ":Version", "up to 15 hops down", DEP)
    m.node("vuln", c4, r1, ":Vuln", "on CISA's KEV list", VULN)
    m.node("plan", c2, r2, ":Plan", "min cut, by the agent", FIX, dashed=True)
    m.node("scan", c4, r2, ":Scan", "agent memory", MEM, dashed=True)
    m.edge([m.port("app", "right"), m.port("v1", "left")], "DEPENDS_ON", "what it uses", DEP, step=1)
    m.edge([m.port("v1", "right"), m.port("v2", "left")], "DEPENDS_ON*", "transitive", DEP, step=1)
    m.edge([m.port("vuln", "left"), m.port("v2", "right")], "AFFECTS", "exploited now?", VULN, step=2)
    m.edge([m.port("plan", "top"), m.port("v1", "bottom")], "CHANGES", "fewest fixes", FIX, step=3, at=0.5, cap="left")
    mid = (r1 + r2) / 2
    m.edge([m.port("plan", "right"), (c3, r2), (c3, mid), (c4 - 60, mid), m.port("vuln", "bottom", -60)],
           "RESOLVES", "every path cut", FIX, step=3, seg=2, at=0.45)
    m.edge([m.port("scan", "top", 60), m.port("vuln", "bottom", 60)], "FOUND", "diff vs last scan", MEM, step=4, at=0.4)
    steps = [("1", "What it really runs", "-[:DEPENDS_ON*1..15]->", DEP),
             ("2", "Is it exploited", "(:Vuln {kev})-[:AFFECTS]->", VULN),
             ("3", "Smallest fix", "algo.maxFlow → (:Plan)", FIX),
             ("4", "Remember", "(:Scan)-[:FOUND]->(:Vuln)", MEM)]
    legend = [("app", APP), ("dependency", DEP), ("vulnerability", VULN), ("agent-written", FIX), ("memory", MEM)]
    return m.render("exploit-path · graph map", "What the agent walks when CISA flags a flaw", steps, legend)


if __name__ == "__main__":
    which, out = sys.argv[1], sys.argv[2]
    svg = inheritance() if which == "inheritance" else exploit_path()
    open(out, "w").write(svg)
    print(out, len(svg), "bytes")
