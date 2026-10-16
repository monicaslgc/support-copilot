"""The architecture diagram, drawn with the author's own diagram kit (Dark green / Cream / Sage / Charcoal, DM Serif
Display + Inter + JetBrains Mono). The Diagram class and the icons are from that kit, trimmed to what is used here.

    python docs/src/architecture.py        # writes docs/src/architecture.svg (scripts/render_assets.py makes the PNG)

The kit's colours file was not available, so the neutral tints (border, step, line) are derived from the palette.
"""
import html
from pathlib import Path

FOREST, CREAM, SAGE, CHAR = "#153D32", "#F4F1E8", "#A0B3A0", "#202A27"
MUTED, BORDER, STEP, STEPB, LINE, WHITE = "#4C6354", "#D6D9D0", "#DDE5DC", "#A0B3A0", "#153D32", "#FFFFFF"
W = 1600
FOOTER = "Concept project · synthetic data · offline tests"
FONTS = ""   # the renderer injects the fonts
F_DISPLAY, F_BODY, F_MONO = "'DM Serif Display', Georgia, serif", "Inter, 'DM Sans', sans-serif", "'JetBrains Mono', monospace"

ICON = {  # 24x24 line icons, drawn with a thin stroke
    'flow':   '<circle cx="6" cy="12" r="2.2"/><circle cx="18" cy="6" r="2.2"/><circle cx="18" cy="18" r="2.2"/><path d="M8 11.2 16 7M8 12.8 16 17"/>',
    'db':     '<path d="M5 6c0-1.7 3.1-3 7-3s7 1.3 7 3-3.1 3-7 3-7-1.3-7-3zM5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/>',
    'person': '<circle cx="12" cy="8" r="3.4"/><path d="M5.5 20c.8-3.6 3.3-5.5 6.5-5.5s5.7 1.9 6.5 5.5"/>',
    'chat':   '<path d="M4 5h16v11H9l-5 4z"/>',
    'clock':  '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3 2"/>',
    'server': '<rect x="4" y="4" width="16" height="6" rx="1.5"/><rect x="4" y="14" width="16" height="6" rx="1.5"/><path d="M7.5 7h.01M7.5 17h.01"/>',
    'inbox':  '<path d="M4 13l2-7h12l2 7v5H4zM4 13h5l1 2h4l1-2h5"/>',
    'check':  '<circle cx="12" cy="12" r="8.5"/><path d="M8 12.5l2.7 2.7L16 9.5"/>',
    'doc':    '<path d="M6 3h9l4 4v14H6zM15 3v4h4M9 12h7M9 16h7"/>',
    'sync':   '<path d="M4 12a8 8 0 0 1 13.5-5.8L20 8.5M20 4v4.5h-4.5M20 12a8 8 0 0 1-13.5 5.8L4 15.5M4 20v-4.5h4.5"/>',
    'table':  '<rect x="4" y="5" width="16" height="14" rx="1.5"/><path d="M4 10h16M10 5v14"/>',
    'bell':   '<path d="M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15zM10 20a2 2 0 0 0 4 0"/>',
    'target': '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r=".8"/>',
    'calc':   '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 12h.01M12 12h.01M16 12h.01M8 16h.01M12 16h.01M16 16h.01"/>',
    'void':   '<circle cx="12" cy="12" r="8.5"/><path d="M7 7l10 10"/>',
    'shield': '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/><path d="M9 12l2.2 2.2L15 10.5"/>',
}


def e(s):
    return html.escape(str(s), quote=False)


class Diagram:
    def __init__(self, title, eyebrow, sub, h=900):
        self.h = h
        self.nodes = {}
        self.body = []
        self.defs = []
        self.title, self.eyebrow, self.sub = title, eyebrow, sub

    # ---------------------------------------------------------------- primitives
    def node(self, nid, x, y, kind, icon, label, tag='', note=None, size=84, text=None, lp='b'):
        self.nodes[nid] = (x, y, size / 2 if not text else 62, size / 2 if not text else 23)
        if text:   # external system chip (name only, no logos)
            w, h = 124, 46
            self.nodes[nid] = (x, y, w / 2, h / 2)
            self.body.append(f'<g><rect x="{x - w/2}" y="{y - h/2}" width="{w}" height="{h}" rx="13" fill="{CREAM}" stroke="{STEPB}" stroke-width="1.4"/>'
                             f'<text x="{x}" y="{y + 5}" text-anchor="middle" class="chip">{e(text)}</text>')
            if tag:
                self.body.append(f'<text x="{x}" y="{y + h/2 + 20}" text-anchor="middle" class="tag">{e(tag)}</text>')
            self.body.append('</g>')
            return
        fills = {'wf': (STEP, STEPB, FOREST), 'data': (WHITE, BORDER, FOREST), 'human': (FOREST, FOREST, CREAM), 'ext': (CREAM, STEPB, FOREST)}
        f, s, ic = fills[kind]
        half = size / 2
        dash = ' stroke-dasharray="4 4"' if kind == 'data' else ''
        g = [f'<g><rect x="{x - half}" y="{y - half}" width="{size}" height="{size}" rx="{size * .2}" fill="{f}" stroke="{s}" stroke-width="1.5"{dash}/>']
        sc = size / 24 * .52
        g.append(f'<g transform="translate({x - 12 * sc} {y - 12 * sc}) scale({sc})" fill="none" stroke="{ic}" stroke-width="{1.5 / sc * .62:.2f}" stroke-linecap="round" stroke-linejoin="round">{ICON[icon]}</g>')
        notes = list(note or [])
        if lp == 'b':
            ly = y + half + 24
            g.append(f'<text x="{x}" y="{ly}" text-anchor="middle" class="label">{e(label)}</text>')
            if tag:
                g.append(f'<text x="{x}" y="{ly + 19}" text-anchor="middle" class="tag">{e(tag)}</text>')
            for i, line in enumerate(notes):
                g.append(f'<text x="{x}" y="{ly + 40 + i * 17}" text-anchor="middle" class="note">{e(line)}</text>')
        elif lp == 'a':
            base = y - half - 14
            if tag:
                g.append(f'<text x="{x}" y="{base}" text-anchor="middle" class="tag">{e(tag)}</text>')
                ly = base - 20
            else:
                ly = base
            g.append(f'<text x="{x}" y="{ly}" text-anchor="middle" class="label">{e(label)}</text>')
            for i, line in enumerate(notes):
                g.append(f'<text x="{x}" y="{ly - 22 - i * 17}" text-anchor="middle" class="note">{e(line)}</text>')
        else:  # 'r': to the right of the node
            tx = x + half + 18
            g.append(f'<text x="{tx}" y="{y - 8}" class="label">{e(label)}</text>')
            if tag:
                g.append(f'<text x="{tx}" y="{y + 11}" class="tag">{e(tag)}</text>')
            for i, line in enumerate(notes):
                g.append(f'<text x="{tx}" y="{y + 32 + i * 17}" class="note">{e(line)}</text>')
        g.append('</g>')
        self.body.append(''.join(g))

    def edge(self, a, b, sa='r', sb='l', bend=.5, dashed=False, label=None, dip=None, lxy=None):
        ax, ay, ahw, ahh = self.nodes[a]
        bx, by, bhw, bhh = self.nodes[b]
        pa = {'r': (ax + ahw, ay), 'l': (ax - ahw, ay), 't': (ax, ay - ahh), 'b': (ax, ay + ahh)}[sa]
        pb = {'r': (bx + bhw, by), 'l': (bx - bhw, by), 't': (bx, by - bhh), 'b': (bx, by + bhh)}[sb]
        (x1, y1), (x2, y2) = pa, pb
        if dip is not None:
            d = f'M{x1} {y1} C{x1} {dip} {x2} {dip} {x2} {y2}'
        elif sa in 'rl' and sb in 'rl':
            dx = (x2 - x1) * bend
            d = f'M{x1} {y1} C{x1 + dx} {y1} {x2 - dx} {y2} {x2} {y2}'
        else:
            dy = (y2 - y1) * bend
            d = f'M{x1} {y1} C{x1} {y1 + dy} {x2} {y2 - dy} {x2} {y2}'
        da = ' stroke-dasharray="5 5"' if dashed else ''
        self.body.insert(0, f'<g><path d="{d}" fill="none" stroke="{LINE}" stroke-width="1.6" stroke-linecap="round"{da} marker-end="url(#ah)"/>'
                            f'<circle cx="{x1}" cy="{y1}" r="3.6" fill="{LINE}"/></g>')
        if label:
            lx, ly = lxy if lxy else ((x1 + x2) / 2, min(y1, y2) - 12)
            self.body.append(f'<text x="{lx}" y="{ly}" text-anchor="middle" class="tag">{e(label)}</text>')

    def text(self, x, y, s, cls='note', anchor='start'):
        self.body.append(f'<text x="{x}" y="{y}" text-anchor="{anchor}" class="{cls}">{e(s)}</text>')

    def raw(self, s):
        self.body.append(s)

    def legend(self, items, x=80, y=None):
        y = y or self.h - 34
        cx = x
        for kind, label in items:
            fills = {'wf': (STEP, STEPB, ''), 'data': (WHITE, BORDER, ' stroke-dasharray="3 3"'), 'human': (FOREST, FOREST, ''), 'ext': (CREAM, STEPB, '')}
            f, s, d = fills[kind]
            self.body.append(f'<rect x="{cx}" y="{y - 11}" width="22" height="22" rx="5" fill="{f}" stroke="{s}" stroke-width="1.3"{d}/>'
                             f'<text x="{cx + 32}" y="{y + 4}" class="tag">{e(label)}</text>')
            cx += 52 + len(label) * 8.2

    # ---------------------------------------------------------------- output
    def svg(self, standalone=True):
        h = self.h
        style = (f'<style>{FONTS if standalone else ""}'
                 f'.title{{font:400 46px {F_DISPLAY};fill:{FOREST};letter-spacing:-.5px}}'
                 f'.eyebrow{{font:500 12.5px {F_MONO};fill:{MUTED};letter-spacing:2.4px;text-transform:uppercase}}'
                 f'.sub{{font:400 17px {F_BODY};fill:{MUTED}}}'
                 f'.label{{font:600 15.5px {F_BODY};fill:{FOREST}}}'
                 f'.chip{{font:500 15px {F_BODY};fill:{FOREST}}}'
                 f'.tag{{font:500 10.5px {F_MONO};fill:{MUTED};letter-spacing:1.6px;text-transform:uppercase}}'
                 f'.note{{font:400 13px {F_BODY};fill:{CHAR}}}'
                 f'.lane{{font:500 11.5px {F_MONO};fill:{FOREST};letter-spacing:2.2px;text-transform:uppercase}}'
                 f'</style>')
        blobs = (f'<path d="M-40 -10 L520 -10 C470 120 330 190 160 170 C60 158 -10 110 -40 60 Z" fill="{SAGE}" opacity=".20"/>'
                 f'<path d="M1040 {h + 10} L{W + 40} {h + 10} L{W + 40} {h - 230} C1500 {h - 170} 1330 {h - 60} 1180 {h - 70} C1110 {h - 74} 1070 {h - 40} 1040 {h + 10} Z" fill="{SAGE}" opacity=".22"/>'
                 f'<path d="M-30 {h - 140} C80 {h - 230} 150 {h - 90} 90 {h + 20} L-30 {h + 20} Z" fill="{SAGE}" opacity=".12"/>')
        head = (f'<text x="80" y="62" class="eyebrow">{e(self.eyebrow)}</text>'
                f'<text x="80" y="118" class="title">{e(self.title)}</text>'
                f'<text x="80" y="152" class="sub">{e(self.sub)}</text>')
        foot = (f'<text x="{W - 80}" y="{h - 30}" text-anchor="end" class="tag">{e(FOOTER)}</text>')
        marker = (f'<marker id="ah" viewBox="0 0 10 10" refX="8.2" refY="5" markerWidth="8" markerHeight="8" orient="auto">'
                  f'<path d="M1.5 1.5 8.5 5 1.5 8.5" fill="none" stroke="{LINE}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></marker>')
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}" role="img" aria-label="{e(self.title)}">'
                f'<title>{e(self.title)}</title><defs>{style}{marker}</defs>'
                f'<rect width="{W}" height="{h}" fill="{CREAM}"/>{blobs}{head}{"".join(self.body)}{foot}</svg>')


def architecture():
    d = Diagram("Nothing reaches the customer until a person approves", "support-copilot · architecture",
                "Which steps are AI, which are plain rules, and where the human decision sits.", 900)
    y = 400
    xs = [130 + i * 223 for i in range(7)]
    d.node("tk", xs[0], y, "ext", "inbox", "Ticket arrives", "zammad or zendesk", ["signed, idempotent", "webhook"])
    d.node("mk", xs[1], y, "wf", "shield", "Mask private data", "plain rules", ["emails, cards, IBANs,", "phones, passwords"])
    d.node("tr", xs[2], y, "wf", "flow", "Triage", "claude or rules", ["category, P1 to P4,", "sentiment, language"])
    d.node("dr", xs[3], y, "wf", "doc", "Draft reply", "claude or template", ["help article, in the", "customer's language"])
    d.node("ck", xs[4], y, "wf", "check", "Check and link", "plain rules", ["promises flagged,", "incidents grouped"])
    d.node("hu", xs[5], y, "human", "person", "Human approval", "approve or reject", ["edit first; one click", "for a whole incident"])
    d.node("cu", xs[6], y, "ext", "chat", "Customer", "after approval only", ["public reply; the AI's", "notes stay internal"])
    d.node("me", 1180, 250, "data", "table", "Per-category metrics", "informational", ["clean rate and a", "confidence bound"], lp="r")
    for a_, b_ in (("tk", "mk"), ("mk", "tr"), ("tr", "dr"), ("dr", "ck"), ("ck", "hu")):
        d.edge(a_, b_)
    d.edge("hu", "cu", label="approved", lxy=(xs[5] + 112, y - 52))
    d.edge("hu", "me", "t", "b")
    cards = [("No key needed", "Without an API key every AI step falls back to plain rules, so it runs offline."),
             ("Failure is visible", "A refusal, cut-off or outage falls back to rules, and the triage says so."),
             ("Known gaps", "Names and addresses are not masked, and an oddly worded promise gets through."),
             ("Not verified", "Zammad, Zendesk and the live Claude API are only tested against fakes.")]
    for i, (h_, t_) in enumerate(cards):
        x = 80 + i * 370
        yy = 700
        d.raw(f'<rect x="{x}" y="{yy}" width="340" height="104" rx="14" fill="{WHITE}" stroke="{BORDER}" stroke-width="1.3"/>')
        d.text(x + 22, yy + 35, h_, "label")
        words, lines, cur = t_.split(), [], ""
        for wd in words:
            if len(cur) + len(wd) > 40:
                lines.append(cur)
                cur = wd
            else:
                cur = (cur + " " + wd).strip()
        lines.append(cur)
        for j, ln in enumerate(lines):
            d.text(x + 22, yy + 61 + j * 19, ln, "note")
    d.legend([("wf", "step"), ("data", "metrics"), ("human", "human step"), ("ext", "outside system")], y=870)
    return d


if __name__ == "__main__":
    out = Path(__file__).with_name("architecture.svg")
    out.write_text(architecture().svg(True), encoding="utf8")
    print("wrote", out)
