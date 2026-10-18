"""Draw a README graph map as a self-contained SVG: colored nodes, numbered step badges on
the edges, and a step key. Usage: python graph_map.py inheritance|exploit-path OUT.svg"""
import math
import sys
from html import escape

SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
BG, PANEL, INK, MUTED = "#0B0E1A", "#12162A", "#EEF0F7", "#9AA3B8"


def shade(hex_color, f):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    if f >= 0:
        r, g, b = (int(c + (255 - c) * f) for c in (r, g, b))
    else:
        r, g, b = (int(c * (1 + f)) for c in (r, g, b))
    return f"#{r:02X}{g:02X}{b:02X}"


class Map:
    def __init__(self, w, h):
        self.w, self.h, self.nodes, self.defs, self.back, self.front = w, h, {}, [], [], []
        self.colors = set()

    def node(self, key, x, y, title, sub, color, ink="#FFFFFF", shape="rect", w=290, h=110, tag=None, tag_above=False):
        self.nodes[key] = dict(x=x, y=y, title=title, sub=sub, color=color, ink=ink, shape=shape, w=w, h=h, tag=tag, tag_above=tag_above)

    def _edge_point(self, n, tx, ty, pad=6):
        dx, dy = tx - n["x"], ty - n["y"]
        d = math.hypot(dx, dy) or 1
        if n["shape"] == "circle":
            r = n["w"] / 2 + pad
            return n["x"] + dx / d * r, n["y"] + dy / d * r
        hw, hh = n["w"] / 2 + pad, n["h"] / 2 + pad
        s = min(hw / abs(dx) if dx else 1e9, hh / abs(dy) if dy else 1e9)
        return n["x"] + dx * s, n["y"] + dy * s

    def edge(self, a, b, rel, caption, color, step=None, bend=0.0, dashed=False, at=0.5, label_dx=0, label_dy=0):
        A, B = self.nodes[a], self.nodes[b]
        mx, my = (A["x"] + B["x"]) / 2, (A["y"] + B["y"]) / 2
        nx, ny = -(B["y"] - A["y"]), B["x"] - A["x"]
        nl = math.hypot(nx, ny) or 1
        cx, cy = mx + nx / nl * bend, my + ny / nl * bend
        x1, y1 = self._edge_point(A, cx, cy)
        x2, y2 = self._edge_point(B, cx, cy, pad=14)
        self.colors.add(color)
        mid = f"arrow{color[1:]}"
        dash = ' stroke-dasharray="10 9"' if dashed else ""
        self.back.append(f'<path d="M{x1:.0f},{y1:.0f} Q{cx:.0f},{cy:.0f} {x2:.0f},{y2:.0f}" fill="none" stroke="{color}" '
                         f'stroke-width="5" stroke-linecap="round" opacity="0.95"{dash} marker-end="url(#{mid})"/>')
        t = at
        lx = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t ** 2 * x2 + label_dx
        ly = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t ** 2 * y2 + label_dy
        self._label(lx, ly, rel, caption, color, step)

    def loop_label(self, x, y, rel, caption, color, step):
        self._label(x, y, rel, caption, color, step)

    def _label(self, x, y, rel, caption, color, step):
        row1 = (42 if step else 0) + len(rel) * 11.4
        w = max(row1, len(caption) * 10.6) + 32
        h = 86 if caption else 52
        left, top = x - w / 2, y - h / 2
        g = [f'<g filter="url(#lift)"><rect x="{left:.0f}" y="{top:.0f}" width="{w:.0f}" height="{h}" rx="16" fill="{PANEL}" '
             f'stroke="{color}" stroke-width="2.5"/>']
        cx = x - row1 / 2
        if step:
            g.append(f'<circle cx="{cx + 14:.0f}" cy="{top + 26:.0f}" r="18" fill="{color}"/>'
                     f'<text x="{cx + 14:.0f}" y="{top + 33:.0f}" text-anchor="middle" font-family="{SANS}" font-size="20" '
                     f'font-weight="800" fill="{BG}">{step}</text>')
            cx += 42
        g.append(f'<text x="{cx:.0f}" y="{top + 33:.0f}" font-family="{MONO}" font-size="18" font-weight="700" '
                 f'fill="{shade(color, 0.35)}">{escape(rel)}</text>')
        if caption:
            g.append(f'<text x="{x:.0f}" y="{top + 68:.0f}" text-anchor="middle" font-family="{SANS}" font-size="20" '
                     f'font-weight="600" fill="{INK}">{escape(caption)}</text>')
        g.append("</g>")
        self.front.append("".join(g))

    def self_loop(self, key, rel, caption, color, step):
        n = self.nodes[key]
        x, y = n["x"], n["y"] - n["h"] / 2
        self.colors.add(color)
        self.back.append(f'<path d="M{x - 40},{y - 4} C{x - 70},{y - 110} {x + 70},{y - 110} {x + 40},{y - 14}" fill="none" '
                         f'stroke="{color}" stroke-width="5" stroke-linecap="round" marker-end="url(#arrow{color[1:]})"/>')
        self._label(x, y - 100, rel, caption, color, step)

    def _node_svg(self, k, n):
        c, x, y, w, h = n["color"], n["x"], n["y"], n["w"], n["h"]
        gid = f"g_{k}"
        self.defs.append(f'<linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{shade(c, 0.22)}"/>'
                         f'<stop offset="1" stop-color="{shade(c, -0.12)}"/></linearGradient>')
        out = [f'<g filter="url(#glow{c[1:]})">']
        if n["shape"] == "circle":
            out.append(f'<circle cx="{x}" cy="{y}" r="{w / 2}" fill="url(#{gid})" stroke="{shade(c, 0.5)}" stroke-width="3"/>')
            ty = y + 2 if not n["sub"] else y - 4
        else:
            out.append(f'<rect x="{x - w / 2}" y="{y - h / 2}" width="{w}" height="{h}" rx="22" fill="url(#{gid})" '
                       f'stroke="{shade(c, 0.5)}" stroke-width="3"/>')
            ty = y - 4 if n["sub"] else y + 8
        out.append("</g>")
        out.append(f'<text x="{x}" y="{ty}" text-anchor="middle" font-family="{SANS}" font-size="30" font-weight="800" '
                   f'fill="{n["ink"]}">{escape(n["title"])}</text>')
        if n["sub"]:
            out.append(f'<text x="{x}" y="{ty + 30}" text-anchor="middle" font-family="{SANS}" font-size="20" font-weight="600" '
                       f'fill="{n["ink"]}" opacity="0.9">{escape(n["sub"])}</text>')
        if n["tag"]:
            txt, tc = n["tag"]
            tw = len(txt) * 10.4 + 28
            half = n["w"] / 2 if n["shape"] == "circle" else n["h"] / 2
            top = y - half - 58 if n["tag_above"] else y + half + 12
            out.append(f'<rect x="{x - tw / 2:.0f}" y="{top:.0f}" width="{tw:.0f}" height="34" rx="17" fill="{PANEL}" stroke="{tc}" '
                       f'stroke-width="1.5" stroke-dasharray="4 3"/><text x="{x}" y="{top + 23:.0f}" text-anchor="middle" '
                       f'font-family="{SANS}" font-size="17" font-weight="700" fill="{tc}">{escape(txt)}</text>')
        return "".join(out)

    def render(self, kicker, title, steps, legend, kicker_color):
        for c in {n["color"] for n in self.nodes.values()}:
            self.defs.append(f'<filter id="glow{c[1:]}" x="-40%" y="-40%" width="180%" height="180%">'
                             f'<feDropShadow dx="0" dy="6" stdDeviation="10" flood-color="{c}" flood-opacity="0.35"/></filter>')
        for c in self.colors:
            self.defs.append(f'<marker id="arrow{c[1:]}" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" '
                             f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>')
        self.defs.append('<filter id="lift" x="-20%" y="-50%" width="140%" height="200%"><feDropShadow dx="0" dy="3" '
                         'stdDeviation="4" flood-color="#000" flood-opacity="0.55"/></filter>'
                         '<radialGradient id="bgA" cx="0.9" cy="0" r="0.8"><stop offset="0" stop-color="#3B2F8F" stop-opacity="0.55"/>'
                         '<stop offset="1" stop-color="#3B2F8F" stop-opacity="0"/></radialGradient>'
                         '<radialGradient id="bgB" cx="0" cy="1" r="0.7"><stop offset="0" stop-color="#0E6E66" stop-opacity="0.35"/>'
                         '<stop offset="1" stop-color="#0E6E66" stop-opacity="0"/></radialGradient>')
        W, H = self.w, self.h
        body = [f'<rect width="{W}" height="{H}" rx="32" fill="{BG}"/>',
                f'<rect width="{W}" height="{H}" rx="32" fill="url(#bgA)"/>',
                f'<rect width="{W}" height="{H}" rx="32" fill="url(#bgB)"/>',
                f'<text x="64" y="74" font-family="{MONO}" font-size="20" font-weight="700" letter-spacing="4" fill="{kicker_color}">{escape(kicker)}</text>',
                f'<text x="64" y="126" font-family="{SANS}" font-size="46" font-weight="800" fill="{INK}">{escape(title)}</text>']
        body += self.back
        body += [self._node_svg(k, n) for k, n in self.nodes.items()]
        body += self.front
        # step key
        cols = 3 if len(steps) > 4 else len(steps)
        top = H - 70 - 112 * math.ceil(len(steps) / cols)
        body.append(f'<line x1="64" y1="{top - 30}" x2="{W - 64}" y2="{top - 30}" stroke="#262C45" stroke-width="2"/>')
        colw = (W - 128 + 24) / cols
        for i, (num, head, text, color) in enumerate(steps):
            x = 64 + (i % cols) * colw
            y = top + (i // cols) * 112
            body.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{colw - 24:.0f}" height="92" rx="18" fill="{PANEL}" stroke="#262C45" stroke-width="2"/>'
                        f'<circle cx="{x + 42:.0f}" cy="{y + 46:.0f}" r="22" fill="{color}"/>'
                        f'<text x="{x + 42:.0f}" y="{y + 54:.0f}" text-anchor="middle" font-family="{SANS}" font-size="22" font-weight="800" fill="{BG}">{num}</text>'
                        f'<text x="{x + 80:.0f}" y="{y + 38:.0f}" font-family="{SANS}" font-size="24" font-weight="800" fill="{INK}">{escape(head)}</text>'
                        f'<text x="{x + 80:.0f}" y="{y + 70:.0f}" font-family="{SANS}" font-size="19" font-weight="500" fill="{MUTED}">{escape(text)}</text>')
        lx = 64
        for label, color in legend:
            body.append(f'<circle cx="{lx + 8}" cy="{H - 38}" r="8" fill="{color}"/><text x="{lx + 24}" y="{H - 32}" '
                        f'font-family="{SANS}" font-size="19" font-weight="600" fill="{MUTED}">{escape(label)}</text>')
            lx += 50 + len(label) * 9.4
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
                f'aria-label="{escape(title)}"><defs>{"".join(self.defs)}</defs>{"".join(body)}</svg>')


def inheritance():
    PERSON, CODE, DEC, TEAM, CHANGE, MEM, LEAD = "#8C7CFF", "#FFA866", "#4FD8C4", "#F472B6", "#60A5FA", "#FDE047", "#F472B6"
    m = Map(1600, 1320)
    m.node("chg", 500, 230, "🔀 Change", "merged PR, live", CHANGE)
    m.node("hand", 200, 500, "🧠 Handoff", "written by the agent", MEM, ink="#2A2003")
    m.node("p", 600, 500, "👤 Person", "leaving", PERSON, shape="circle", w=200)
    m.node("heir", 600, 840, "👤 Person", "carries it on", PERSON, shape="circle", w=184)
    m.node("dir", 1040, 270, "📁 Dir", "code folder", CODE, ink="#1A1206")
    m.node("parent", 1400, 270, "📁 Parent Dir", "up the tree", CODE, ink="#1A1206")
    m.node("kep", 1040, 580, "💡 KEP", "design decision", DEC, ink="#06201C")
    m.node("doc", 1400, 580, "📄 Document", "why it was built", DEC, ink="#06201C")
    m.node("team", 1040, 840, "👥 Team", "roster", TEAM)
    m.edge("chg", "dir", "TOUCHED", "keeps it current", CHANGE, step=6, at=0.5, label_dy=-4)
    m.edge("p", "dir", "APPROVES", "what they own", CODE, step=1, at=0.5, label_dx=-20, label_dy=20)
    m.edge("dir", "parent", "CHILD_OF", "climb for backup", "#FF8A5B", step=2, label_dy=-112)
    m.edge("p", "kep", "AUTHORED", "what they decided", DEC, step=3, at=0.44, label_dy=4)
    m.edge("doc", "kep", "DESCRIBES", "why", DEC, step=3, label_dy=-112)
    m.edge("p", "heir", "WORKED_WITH", "who carries it on", PERSON, step=4)
    m.edge("hand", "p", "FOR", "hand-off + patch", MEM, step=5, label_dy=-100, label_dx=-20)
    m.edge("p", "team", "LEADS", "stale-lead check", LEAD, dashed=True, at=0.55)
    steps = [("1", "What they own", "APPROVES → every code folder", CODE),
             ("2", "Who else can approve", "climb CHILD_OF up the folder tree", "#FF8A5B"),
             ("3", "What they decided, and why", "AUTHORED → KEP ← Document", DEC),
             ("4", "Who carries it on", "rank WORKED_WITH neighbors", PERSON),
             ("5", "Act", "write the hand-off + OWNERS patch", MEM),
             ("6", "Stay current", "a merged PR rewrites only its folders", CHANGE)]
    legend = [("people", PERSON), ("code", CODE), ("design decisions", DEC), ("teams", TEAM),
              ("live changes", CHANGE), ("written by the agent", MEM)]
    return m.render("INHERITANCE · GRAPH MAP", "What the agent walks when someone leaves", steps, legend, "#FFA866")


def exploit_path():
    APP, DEP, VULN, FIX, MEM = "#60A5FA", "#FFB454", "#FF6B6B", "#4ADE80", "#A78BFA"
    m = Map(1600, 980)
    m.node("app", 180, 350, "🖥️ App", "your service", APP, w=230)
    m.node("v1", 590, 350, "📦 Version", "direct dependency", DEP, ink="#1F1403", shape="circle", w=204,
           tag=("⚡ chokepoint · betweenness", "#FFB454"), tag_above=True)
    m.node("v2", 990, 350, "📦 Version", "15 hops down", DEP, ink="#1F1403", shape="circle", w=204)
    m.node("vuln", 1400, 350, "🚨 Vuln", "on CISA's exploited list", VULN, w=300)
    m.node("plan", 790, 600, "🛠️ Plan", "min cut · written by the agent", FIX, ink="#062012", w=360)
    m.node("scan", 1400, 600, "🕒 Scan", "agent memory", MEM)
    m.edge("app", "v1", "DEPENDS_ON", "what it uses", DEP, step=1, label_dy=-70)
    m.edge("v1", "v2", "DEPENDS_ON", "up to 15 hops", DEP, step=1, label_dy=-70)
    m.edge("vuln", "v2", "AFFECTS", "exploited now?", VULN, step=2, label_dy=-70)
    m.edge("plan", "v1", "CHANGES", "fewest fixes", FIX, step=3, at=0.5, label_dx=-110)
    m.edge("plan", "vuln", "RESOLVES", "every path cut", FIX, step=3, at=0.5, label_dx=30, label_dy=10)
    m.edge("scan", "vuln", "FOUND", "what changed", MEM, step=4)
    steps = [("1", "What it really runs", "DEPENDS_ON up to 15 hops", DEP),
             ("2", "Is it exploited", "AFFECTS from CISA's list", VULN),
             ("3", "Smallest fix", "maxFlow min cut → Plan", FIX),
             ("4", "Remember", "Scan FOUND → diff later", MEM)]
    legend = [("apps", APP), ("dependencies", DEP), ("vulnerabilities", VULN), ("agent memory", MEM), ("written by the agent", FIX)]
    return m.render("EXPLOIT PATH · GRAPH MAP", "What the agent walks when CISA flags a flaw", steps, legend, "#FFB454")


if __name__ == "__main__":
    which, out = sys.argv[1], sys.argv[2]
    svg = inheritance() if which == "inheritance" else exploit_path()
    open(out, "w").write(svg)
    print(out, len(svg), "bytes")
