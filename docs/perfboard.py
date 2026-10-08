"""Generate docs/perfboard.svg: perfboard layout of the CPU/RAM gauge (final version).

    python3 docs/perfboard.py docs/perfboard.svg

Parts on top, every connection a trace on the solder side (bare wire or solder
along the drawn holes). No two traces cross: before drawing, the script checks that
no hole is shared by two nets and that every net is fully connected.
"""
import sys

DRV_GAP = 7  # holes between the TB6612FNG module's two pin rows: measure yours

# Grid in holes. Nano with the USB end up, seen from the parts side.
XL = 12            # Nano left pin column (D13 ... VIN)
XR = XL + 6        # Nano right pin column (D12 ... TX1)
Y0 = 6             # row of the Nano's first pins (USB end)
XP = XL - 9        # LCD pads
XD = XR + 7        # driver input row (GND ... PWMA), chip side up
XO = XD + DRV_GAP  # driver output row (GND ... VM)
XG = XO + 4        # ground riser on the right edge
W, H = XG + 2, Y0 + 20


def r(i):
    """Row of the Nano's i-th pin (0 = USB end)."""
    return Y0 + i


def t(k):
    """k rows above the Nano's first pins."""
    return Y0 - k


def b(k):
    """k rows below the Nano's last pins."""
    return Y0 + 15 + k


NANO_L = ["D13", "3V3", "REF", "A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "5V", "RST", "GND", "VIN"]
NANO_R = ["D12", "D11", "D10", "D9", "D8", "D7", "D6", "D5", "D4", "D3", "D2", "GND", "RST", "RX0", "TX1"]
# Top to bottom, module chip side up with the inputs towards the Nano
DRV_IN = [("GND", "GND"), ("PWMB", "5V"), ("BIN2", "D9"), ("BIN1", "D8"),
          ("STBY", "5V"), ("AIN1", "D2"), ("AIN2", "D3"), ("PWMA", "5V")]
DRV_OUT = [("GND", "GND"), ("BO1", "BO1"), ("BO2", "BO2"), ("AO2", "AO2"),
           ("AO1", "AO1"), ("GND", "GND"), ("VCC", "5V"), ("VM", "5V")]

pins = {}   # hole -> net, for everything that goes through a hole
groups = []  # off-board pads: (title, [(hole, label)], label side)
parts = []   # drawn on the parts side


def pin(x, y, net):
    if pins.get((x, y), net) != net:
        sys.exit(f"pin at {(x, y)}: {net} vs {pins[(x, y)]}")
    pins[(x, y)] = net


for i, name in enumerate(NANO_L):
    pin(XL, r(i), name if name in ("5V", "GND") or name[0] in "DA" and name not in ("A6", "A7")
        else f"nc:L{name}")
for i, name in enumerate(NANO_R):
    pin(XR, r(i), name if name == "GND" or name[0] == "D" else f"nc:R{name}")
for i, ((_, net_in), (_, net_out)) in enumerate(zip(DRV_IN, DRV_OUT)):
    pin(XD, r(2 + i), net_in)
    pin(XO, r(2 + i), net_out)


def part(kind, legs, nets, label="", at=None):
    """at: label position in holes from the first leg, when the default overlaps."""
    for leg, net in zip(legs, nets):
        pin(*leg, net)
    parts.append((kind, legs, label, at))


def pads(title, items, side):
    """Off-board wire pads: items = [(hole, net, label)]."""
    for hole, net, _ in items:
        pin(*hole, net)
    groups.append((title, [(hole, label) for hole, _, label in items], side))


# ---------------------------------------------------------------- parts
# Nano and driver caps: 100 nF ceramic + 100 uF electrolytic (+ on 5V, the upper leg)
part("cap", [(XL + 2, b(2)), (XL + 2, b(3))], ["5V", "GND"], "104")
part("ecap", [(XL + 4, b(2)), (XL + 4, b(3))], ["5V", "GND"], "100µ")
part("cap", [(XO + 1, r(9)), (XO + 1, r(8))], ["5V", "GND"], "104", (0.3, 1.1))
part("ecap", [(XO + 2, r(9)), (XO + 2, r(8))], ["5V", "GND"], "100µ", (1.3, 1.1))
# LCD caps
part("cap", [(XL - 8, r(12)), (XL - 8, r(13))], ["5V", "GND"], "104")
part("ecap", [(XL - 3, r(12)), (XL - 3, r(13))], ["5V", "GND"], "100µ")
# contrast trimpot: GND, wiper, 5V
part("trim", [(XL - 6, r(9)), (XL - 6, r(10)), (XL - 6, r(11))], ["GND", "V0", "5V"], "10k")
# warning lights, resistors standing (body over the first leg)
part("res_up", [(XR + 1, r(6)), (XR + 2, r(6))], ["D6", "W2"], "330 ×2", (0.8, -0.75))
part("res_up", [(XR + 1, r(7)), (XR + 2, r(7))], ["D5", "W1"])
# lighting transistor
part("res", [(XR + 2, t(2)), (XR + 6, t(2))], ["D10", "BASE"], "1k")
part("res_up", [(XR + 6, t(5)), (XR + 6, t(4))], ["GND", "BASE"], "10k")
part("to92", [(XR + 5, t(3)), (XR + 6, t(3)), (XR + 7, t(3))], ["COLL", "BASE", "GND"], "BC337")

# ---------------------------------------------------------------- off-board pads
lcd = [(r(0), "D7", "4 RS"), (r(1), "D12", "6 E"), (r(2), "GND", "1 VSS"),
       (r(3), "A0", "11 D4"), (r(4), "A1", "12 D5"), (r(5), "A2", "13 D6"), (r(6), "A3", "14 D7"),
       (r(8), "GND", "5 RW"), (r(10), "V0", "3 V0"), (r(12), "5V", "2 VDD")]
pads("LCD", [((XP, y), net, label) for y, net, label in lcd], "left")
pads("motor", [((XO + 2, r(3)), "BO1", "B1"), ((XO + 2, r(4)), "BO2", "B2"),
               ((XO + 2, r(5)), "AO2", "A2"), ((XO + 2, r(6)), "AO1", "A1")], "right")
light = []
for n in range(4):
    light += [((XD + 2 + n, t(1)), "COLL", f"{n + 1}−"), ((XD + 2 + n, r(0)), "5V", f"{n + 1}+")]
pads("lighting", light, "none")
pads("warnings", [((XR + 3, r(6)), "W2", "D6"), ((XR + 3, r(7)), "W1", "D5"),
                  ((XR + 3, r(8)), "GND", "GND")], "right")
pads("button", [((XR + 1, r(8)), "D4", "D4"), ((XR + 1, r(9)), "GND", "GND")], "right")
pads("Pomodoro", [((XR + 1, t(3)), "D11", "D11"), ((XR + 1, t(4)), "GND", "GND")], "right")
pads("piezo", [((XL - 2, r(0)), "D13", "D13"), ((XL - 2, r(1)), "GND", "GND")], "left")
pads("RTC", [((XL - 2, r(7)), "A4", "SDA"), ((XL - 2, r(8)), "A5", "SCL"),
             ((XL - 2, r(9)), "GND", "GND"), ((XL - 2, r(10)), "5V", "VCC")], "left")

# ---------------------------------------------------------------- traces (solder side)
traces = [
    # 5V: left to the LCD side, through the Nano's middle to the driver
    ("5V", [(XL, r(11)), (XL - 1, r(11)), (XL - 1, r(12)), (XP, r(12))]),
    ("5V", [(XL - 2, r(10)), (XL - 2, r(12))]),
    ("5V", [(XL - 6, r(11)), (XL - 6, r(12))]),
    ("5V", [(XL, r(11)), (XL + 2, r(11)), (XL + 2, b(2)), (XD + 1, b(2)), (XD + 1, r(3)), (XD, r(3))]),
    ("5V", [(XD + 1, r(6)), (XD, r(6))]),
    ("5V", [(XD + 1, r(8)), (XO, r(8))]),
    ("5V", [(XD, r(9)), (XO + 2, r(9))]),
    ("5V", [(XD, r(3)), (XD - 1, r(3)), (XD - 1, r(0)), (XD + 5, r(0))]),
    # GND: round the LCD pads, through the Nano's middle, up the right edge
    ("GND", [(XL, r(13)), (XP - 1, r(13)), (XP - 1, r(2)), (XP, r(2)), (XL - 2, r(2)), (XL - 2, r(1))]),
    ("GND", [(XP - 1, r(8)), (XL - 4, r(8)), (XL - 4, r(9)), (XL - 2, r(9))]),
    ("GND", [(XL - 6, r(8)), (XL - 6, r(9))]),
    ("GND", [(XL, r(13)), (XL + 1, r(13)), (XL + 1, b(3)), (XG, b(3)), (XG, t(5)), (XR + 1, t(5))]),
    ("GND", [(XG, r(7)), (XO, r(7))]),
    ("GND", [(XO + 1, r(7)), (XO + 1, r(8))]),
    ("GND", [(XO + 2, r(7)), (XO + 2, r(8))]),
    ("GND", [(XR + 1, t(4)), (XR + 1, t(5))]),
    ("GND", [(XR + 7, t(3)), (XR + 7, t(5))]),
    ("GND", [(XR, r(11)), (XR + 3, r(11)), (XR + 3, r(8))]),
    ("GND", [(XR + 1, r(11)), (XR + 1, r(9))]),
    # LCD
    *[(f"A{k}", [(XL, r(3 + k)), (XP, r(3 + k))]) for k in range(4)],
    ("D7", [(XR, r(5)), (XL + 1, r(5)), (XL + 1, t(1)), (XP - 1, t(1)), (XP - 1, r(0)), (XP, r(0))]),
    ("D12", [(XR, r(0)), (XR, t(2)), (XP - 2, t(2)), (XP - 2, r(1)), (XP, r(1))]),
    ("V0", [(XL - 6, r(10)), (XP, r(10))]),
    # motor: coil B on channel B, coil A on channel A
    ("D9", [(XR, r(3)), (XD - 3, r(3)), (XD - 3, r(4)), (XD, r(4))]),
    ("D8", [(XR, r(4)), (XD - 4, r(4)), (XD - 4, r(5)), (XD, r(5))]),
    ("D2", [(XR, r(10)), (XL + 5, r(10)), (XL + 5, b(0)), (XD - 2, b(0)), (XD - 2, r(7)), (XD, r(7))]),
    ("D3", [(XR, r(9)), (XL + 4, r(9)), (XL + 4, b(1)), (XD - 1, b(1)), (XD - 1, r(8)), (XD, r(8))]),
    *[(net, [(XO, r(3 + k)), (XO + 2, r(3 + k))]) for k, net in enumerate(("BO1", "BO2", "AO2", "AO1"))],
    # warnings and button
    ("D6", [(XR, r(6)), (XR + 1, r(6))]),
    ("D5", [(XR, r(7)), (XR + 1, r(7))]),
    ("W2", [(XR + 2, r(6)), (XR + 3, r(6))]),
    ("W1", [(XR + 2, r(7)), (XR + 3, r(7))]),
    ("D4", [(XR, r(8)), (XR + 1, r(8))]),
    # lighting
    ("D10", [(XR, r(2)), (XR + 2, r(2)), (XR + 2, t(2))]),
    ("BASE", [(XR + 6, t(2)), (XR + 6, t(4))]),
    ("COLL", [(XR + 5, t(3)), (XR + 5, t(1)), (XD + 5, t(1))]),
    # future
    ("D11", [(XR, r(1)), (XR + 1, r(1)), (XR + 1, t(3))]),
    ("D13", [(XL, r(0)), (XL - 2, r(0))]),
    ("A4", [(XL, r(7)), (XL - 2, r(7))]),
    ("A5", [(XL, r(8)), (XL - 2, r(8))]),
]

# ---------------------------------------------------------------- check
holes = dict(pins)
parent = {}


def find(h):
    while parent.setdefault(h, h) != h:
        h = parent[h]
    return h


def union(a, c):
    parent[find(a)] = find(c)


for net, pts in traces:
    prev = None
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if x1 != x2 and y1 != y2:
            sys.exit(f"{net}: diagonal {(x1, y1)} -> {(x2, y2)}")
        n = abs(x2 - x1) + abs(y2 - y1)
        for s in range(n + 1):
            h = (x1 + (x2 - x1) * s // n, y1 + (y2 - y1) * s // n)
            if not (0 <= h[0] < W and 0 <= h[1] < H):
                sys.exit(f"{net}: {h} off the board")
            if holes.setdefault(h, net) != net:
                sys.exit(f"{net} crosses {holes[h]} at {h}")
            if prev:
                union(prev, h)
            prev = h
# connected inside the Nano and the driver module
union((XL, r(13)), (XR, r(11)))
for y in (r(2), r(7)):
    union((XD, r(2)), (XO, y))
for net in {n for n in pins.values() if not n.startswith("nc:")}:
    roots = {find(h) for h, n in pins.items() if n == net}
    if len(roots) > 1:
        sys.exit(f"net {net} is split in {len(roots)} parts")

# ---------------------------------------------------------------- draw
P = 22
P5V, PGND = "#c2410c", "#334155"
COLOURS = {"motor": "#2563eb", "lcd": "#7c3aed", "light": "#d97706", "io": "#059669", "future": "#9ca3af"}
NET_KIND = {"5V": P5V, "GND": PGND}
for n in ("D2", "D3", "D8", "D9", "AO1", "AO2", "BO1", "BO2"):
    NET_KIND[n] = COLOURS["motor"]
for n in ("A0", "A1", "A2", "A3", "D7", "D12", "V0"):
    NET_KIND[n] = COLOURS["lcd"]
for n in ("D10", "BASE", "COLL"):
    NET_KIND[n] = COLOURS["light"]
for n in ("D4", "D5", "D6", "W1", "W2"):
    NET_KIND[n] = COLOURS["io"]
for n in ("D11", "D13", "A4", "A5"):
    NET_KIND[n] = COLOURS["future"]

out = []
add = out.append


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, size=12, anchor="start", c="#111827", weight="normal", halo=False):
    h = ' stroke="white" stroke-width="3" paint-order="stroke"' if halo else ""
    add(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" fill="{c}" '
        f'font-weight="{weight}"{h}>{esc(s)}</text>')


class Panel:
    def __init__(self, ox, oy, mirror):
        self.ox, self.oy, self.mirror = ox, oy, mirror

    def xy(self, x, y):
        if self.mirror:
            x = W - 1 - x
        return self.ox + x * P + P / 2, self.oy + y * P + P / 2

    def board(self):
        add(f'<rect x="{self.ox}" y="{self.oy}" width="{W * P}" height="{H * P}" rx="8" '
            f'fill="#efe3c2" stroke="#b49a62" stroke-width="2"/>')
        for x in range(W):
            for y in range(H):
                cx, cy = self.xy(x, y)
                add(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="2.6" fill="#a8916a"/>')

    def traces(self, width, opacity):
        for net, pts in traces:
            p = " ".join("%.1f,%.1f" % self.xy(*h) for h in pts)
            add(f'<polyline points="{p}" fill="none" stroke="{NET_KIND[net]}" stroke-width="{width}" '
                f'stroke-linecap="round" stroke-linejoin="round" opacity="{opacity}"/>')

    def joints(self):
        for h in pins:
            cx, cy = self.xy(*h)
            add(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="#d1d5db" stroke="#6b7280" stroke-width="1.2"/>')

    def rect(self, x1, y1, x2, y2, fill, stroke="#1f2937", dash=False, rx=4):
        (ax, ay), (bx, by) = self.xy(x1, y1), self.xy(x2, y2)
        d = ' stroke-dasharray="6 4"' if dash else ""
        add(f'<rect x="{min(ax, bx):.1f}" y="{min(ay, by):.1f}" width="{abs(bx - ax):.1f}" '
            f'height="{abs(by - ay):.1f}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="2"{d}/>')

    def outlines(self, dash):
        fill = "none" if dash else "#1e3a8a"
        self.rect(XL - 0.55, r(-1.1), XR + 0.55, r(14.7), fill, "#1f2937", dash)
        self.rect(XL + 1.8, r(-1.6), XR - 1.8, r(-0.3), "none" if dash else "#cbd5e1", "#475569", dash, 2)
        self.rect(XD - 0.6, r(1.15), XO + 0.6, r(9.85), "none" if dash else "#b91c1c", "#1f2937", dash)

    def pair_numbers(self):
        for n in range(4):
            cx, cy = self.xy(XD + 2 + n, t(1.75))
            text(cx, cy + 4, str(n + 1), 10, "middle", c="#92400e", weight="bold", halo=True)

    def group_labels(self):
        for title, items, side in groups:
            for (x, y), label in items:
                cx, cy = self.xy(x, y)
                add(f'<rect x="{cx - 6:.1f}" y="{cy - 6:.1f}" width="12" height="12" fill="#fde68a" '
                    f'stroke="#92400e" stroke-width="1.5"/>')
                if side == "none":
                    continue
                s = side
                if self.mirror and s in ("left", "right"):
                    s = "right" if s == "left" else "left"
                if s == "left":
                    text(cx - 10, cy + 4, label, 10, "end", halo=True)
                elif s == "right":
                    text(cx + 10, cy + 4, label, 10, "start", halo=True)
                else:
                    text(cx, cy + 18, label, 10, "middle", halo=True)


def draw_parts(pn):
    for kind, legs, label, at in parts:
        pts = [pn.xy(*leg) for leg in legs]
        (x1, y1), (x2, y2) = pts[0], pts[-1]
        if at:
            lx, ly = pn.xy(legs[0][0] + at[0], legs[0][1] + at[1])
            text(lx, ly + 4, label, 9, "middle", weight="bold", halo=True)
            label = ""
        if kind == "res":
            add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#6b7280" stroke-width="2"/>')
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            add(f'<rect x="{mx - 26:.1f}" y="{my - 7:.1f}" width="52" height="14" rx="6" fill="#e9d5a7" '
                f'stroke="#78350f" stroke-width="1.5"/>')
            text(mx, my + 4, label, 10, "middle", weight="bold")
        elif kind == "res_up":
            add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#6b7280" stroke-width="2"/>')
            add(f'<circle cx="{x1:.1f}" cy="{y1:.1f}" r="8" fill="#e9d5a7" stroke="#78350f" stroke-width="1.5"/>')
            text(x1 - 12, y1 + 4, label, 10, "end", weight="bold", halo=True)
        elif kind == "cap":
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            add(f'<rect x="{mx - 7:.1f}" y="{min(y1, y2) - 4:.1f}" width="14" height="{abs(y2 - y1) + 8:.1f}" '
                f'rx="6" fill="#f59e0b" stroke="#92400e" stroke-width="1.5"/>')
            text(mx, max(y1, y2) + 18, label, 9, "middle", halo=True)
        elif kind == "ecap":
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            rad = abs(y2 - y1) / 2 + 7
            add(f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="{rad:.1f}" fill="#1e40af" stroke="#0f172a" stroke-width="1.5"/>')
            # stripe on the minus side (second leg)
            sy = my + (rad - 4) * (1 if y2 > y1 else -1)
            add(f'<line x1="{mx - rad + 3:.1f}" y1="{sy:.1f}" x2="{mx + rad - 3:.1f}" y2="{sy:.1f}" '
                f'stroke="#e5e7eb" stroke-width="3"/>')
            text(x1, y1 + 4, "+", 12, "middle", c="white", weight="bold")
            text(mx, my + rad + 12, label, 9, "middle", halo=True)
        elif kind == "trim":
            add(f'<rect x="{x1 - 10:.1f}" y="{y1 - 10:.1f}" width="20" height="{y2 - y1 + 20:.1f}" rx="3" '
                f'fill="#2563eb" stroke="#1e3a8a" stroke-width="1.5"/>')
            add(f'<circle cx="{x1:.1f}" cy="{y1 - 4:.1f}" r="4" fill="#facc15"/>')
            text(x1, y1 - 16, label, 9, "middle", halo=True)
        elif kind == "to92":
            # flat face toward the bottom of the drawing: legs C B E from left to right
            add(f'<path d="M{x1 - 10:.1f},{y1 + 7:.1f} L{x2 + 10:.1f},{y2 + 7:.1f} '
                f'A{(x2 - x1) / 2 + 12:.1f},20 0 0 0 {x1 - 10:.1f},{y1 + 7:.1f} Z" fill="#111827" opacity="0.85"/>')
            for (lx, ly), name in zip(pts, "CBE"):
                text(lx, ly + 20, name, 10, "middle", weight="bold", halo=True)
            text(x2 + 16, y2 - 2, label, 10, "start", weight="bold", halo=True)


def nano_labels(pn):
    for i, name in enumerate(NANO_L):
        x, y = pn.xy(XL, r(i))
        text(x + 10, y + 4, name, 9, "start", c="white")
    for i, name in enumerate(NANO_R):
        x, y = pn.xy(XR, r(i))
        text(x - 10, y + 4, name, 9, "end", c="white")
    cx, _ = pn.xy((XL + XR) / 2, 0)
    text(cx, pn.xy(0, r(7))[1], "Arduino", 12, "middle", c="white", weight="bold")
    text(cx, pn.xy(0, r(7.8))[1], "Nano", 12, "middle", c="white", weight="bold")
    for i, ((ni, _), (no, _)) in enumerate(zip(DRV_IN, DRV_OUT)):
        x, y = pn.xy(XD, r(2 + i))
        text(x + 10, y + 4, ni, 9, "start", c="white")
        x, y = pn.xy(XO, r(2 + i))
        text(x - 10, y + 4, no, 9, "end", c="white")
    cx, cy = pn.xy((XD + XO) / 2, r(5.5))
    text(cx, cy, "TB6612FNG", 11, "middle", c="white", weight="bold")


def group_titles(pn):
    spots = {"LCD": (XP, t(2.6), "middle"), "motor": (XO + 2.4, r(2.3), "middle"),
             "lighting": (XD + 3.5, t(2.6), "middle"), "warnings": (XR + 4.6, r(5.2), "middle"),
             "button": (XR + 1.6, r(10.2), "middle"), "Pomodoro": (XR + 1, t(5.6), "middle"),
             "piezo": (XL - 2.5, t(0.6), "middle"), "RTC": (XL - 2, r(6.5), "middle")}
    for title, (x, y, anchor) in spots.items():
        cx, cy = pn.xy(x, y)
        text(cx, cy + 4, title, 11, anchor, c="#92400e", weight="bold", halo=True)


PW, PH = W * P, H * P
LEFT = 120
total_w = LEFT + PW + 150
top_y = 115
bot_y = top_y + PH + 90
notes_y = bot_y + PH + 50
total_h = notes_y + 330

add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{total_h}" '
    f'viewBox="0 0 {total_w} {total_h}" font-family="Helvetica, Arial, sans-serif">')
add(f'<rect width="{total_w}" height="{total_h}" fill="white"/>')
text(30, 36, "CPU/RAM gauge: perfboard layout", 22, weight="bold")
text(30, 58, f"{W} × {H} holes (2.54 mm). Traces are on the solder side and never cross; "
     "parts may sit over them.", 13, c="#475569")

top = Panel(LEFT, top_y, False)
text(LEFT, top_y - 10, "1. Parts side (top view). Traces shown faint, they are underneath.", 15, weight="bold")
top.board()
top.traces(4, 0.35)
top.outlines(False)
nano_labels(top)
draw_parts(top)
top.group_labels()
top.pair_numbers()
group_titles(top)

bot = Panel(LEFT, bot_y, True)
text(LEFT, bot_y - 10, "2. Solder side (board flipped left to right): solder these traces.", 15, weight="bold")
bot.board()
bot.outlines(True)
bot.traces(6, 1)
bot.joints()
bot.group_labels()
bot.pair_numbers()
group_titles(bot)
for name, x, y in (("D13", XL, r(0)), ("D12", XR, r(0)), ("VIN", XL, r(14)), ("TX1", XR, r(14))):
    cx, cy = bot.xy(x, y)
    text(cx, cy - 9, name, 9, "middle", c="#1e3a8a", weight="bold", halo=True)
cx, cy = bot.xy((XL + XR) / 2, r(-1.4))
text(cx, cy, "USB end", 10, "middle", c="#475569", halo=True)

notes = [
    ("Before soldering", [
        "Nano pins assumed as on its silkscreen (D13 next to the USB on one side, D12 on the other). Check yours.",
        f"Driver: common TB6612FNG module, rows {DRV_GAP} holes apart, chip side up, GND at the top of both rows. Measure "
        "yours; if different, change DRV_GAP and run the script again.",
        "Nano on female headers. Module GND pins are joined on the module, so one is enough here.",
    ]),
    ("Off-board wires (yellow pads)", [
        "LCD: the pin number on each pad. Pins 15 (A) and 16 (K) go to lighting pair 1.",
        "Lighting: top row − (transistor), bottom row + (5V). 1 LCD backlight, 2 scale, 3 needle, 4 spare.",
        "Each LED with its own 150 Ω, soldered at the LED.",
        "Motor: B1/B2/A1/A2 = the wire that was on D8/D9/D2/D3 in the bench test.",
        "Warnings: LED + to D5/D6 (330 Ω on the board), − to GND. Button: D4 and GND.",
        "Future: Pomodoro button (D11), piezo (D13), RTC (A4 SDA, A5 SCL).",
    ]),
    ("Then", [
        "Electrolytic caps: + on 5V (marked +), stripe on GND. 3296W trimpot: bend the legs if they are staggered.",
        "With the Nano out, check with the multimeter (continuity) that 5V and GND are not shorted.",
        "Recalibrate HOME_PHASE after moving the motor to the driver.",
    ]),
]
y = notes_y
for title, lines in notes:
    text(30, y, title, 14, weight="bold")
    y += 20
    for line in lines:
        text(42, y, "• " + line, 12, c="#1f2937")
        y += 18
    y += 8
lx, ly = total_w - 360, notes_y
for i, (name, c) in enumerate((("5V", P5V), ("GND", PGND), ("motor", COLOURS["motor"]), ("LCD", COLOURS["lcd"]),
                               ("lighting", COLOURS["light"]), ("buttons / warnings", COLOURS["io"]),
                               ("future", COLOURS["future"]))):
    yy = ly + i * 20
    add(f'<line x1="{lx}" y1="{yy}" x2="{lx + 28}" y2="{yy}" stroke="{c}" stroke-width="5" stroke-linecap="round"/>')
    text(lx + 36, yy + 4, name, 12)

add("</svg>")
open(sys.argv[1], "w").write("\n".join(out))
