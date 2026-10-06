"""Generate docs/wiring.svg: full wiring of the CPU/RAM gauge (final version).

    python3 docs/wiring.py docs/wiring.svg
"""
import sys

W, H = 1440, 1010
out = []
add = out.append

WIRE = "#1f2937"
P5V = "#c2410c"
GND = "#334155"
SIG = {"motor": "#2563eb", "lcd": "#7c3aed", "light": "#d97706", "io": "#059669", "future": "#9ca3af"}


def line(x1, y1, x2, y2, c=WIRE, w=2, dash=False):
    d = ' stroke-dasharray="6 4"' if dash else ""
    add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{c}" stroke-width="{w}"{d}/>')


def poly(pts, c=WIRE, w=2, dash=False):
    d = ' stroke-dasharray="6 4"' if dash else ""
    p = " ".join(f"{x},{y}" for x, y in pts)
    add(f'<polyline points="{p}" fill="none" stroke="{c}" stroke-width="{w}"{d}/>')


def text(x, y, s, size=13, anchor="start", c="#111827", weight="normal", italic=False):
    st = ' font-style="italic"' if italic else ""
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    add(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" fill="{c}" '
        f'font-weight="{weight}"{st}>{s}</text>')


def box(x, y, w, h, title, sub=None, fill="#f8fafc"):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{WIRE}" stroke-width="2"/>')
    text(x + w / 2, y + 20, title, 15, "middle", weight="bold")
    if sub:
        text(x + w / 2, y + 37, sub, 12, "middle", c="#475569")


def dot(x, y, c=WIRE):
    add(f'<circle cx="{x}" cy="{y}" r="4" fill="{c}"/>')


def v5(x, y, label="5V"):
    """5V supply symbol: bar on top of a stub ending at (x, y)."""
    line(x, y, x, y - 14, P5V)
    line(x - 10, y - 14, x + 10, y - 14, P5V, 3)
    text(x, y - 19, label, 12, "middle", c=P5V, weight="bold")


def v5h(x, y, side):
    """5V label on a horizontal stub ending at (x, y); side = -1 left, +1 right."""
    line(x, y, x + 22 * side, y, P5V)
    text(x + 26 * side, y + 4, "5V", 12, "start" if side > 0 else "end", c=P5V, weight="bold")


def gnd(x, y):
    """Ground symbol hanging from (x, y)."""
    line(x, y, x, y + 10, GND)
    line(x - 11, y + 10, x + 11, y + 10, GND, 2.5)
    line(x - 7, y + 15, x + 7, y + 15, GND, 2.5)
    line(x - 3, y + 20, x + 3, y + 20, GND, 2.5)


def gndh(x, y, side):
    """Ground on a horizontal stub ending at (x, y)."""
    line(x, y, x + 18 * side, y, GND)
    gx = x + 18 * side
    line(gx, y, gx, y + 6, GND)
    line(gx - 9, y + 6, gx + 9, y + 6, GND, 2.5)
    line(gx - 5, y + 10, gx + 5, y + 10, GND, 2.5)
    line(gx - 2, y + 14, gx + 2, y + 14, GND, 2.5)


def resistor_h(x1, x2, y, label, c=WIRE, dash=False):
    """Horizontal resistor from x1 to x2 (box style)."""
    mid = (x1 + x2) / 2
    line(x1, y, mid - 18, y, c, dash=dash)
    line(mid + 18, y, x2, y, c, dash=dash)
    add(f'<rect x="{mid - 18}" y="{y - 7}" width="36" height="14" fill="white" stroke="{c}" stroke-width="2"/>')
    text(mid, y - 12, label, 12, "middle")


def resistor_v(x, y1, y2, label, c=WIRE, label_side=1):
    mid = (y1 + y2) / 2
    line(x, y1, x, mid - 18, c)
    line(x, mid + 18, x, y2, c)
    add(f'<rect x="{x - 7}" y="{mid - 18}" width="14" height="36" fill="white" stroke="{c}" stroke-width="2"/>')
    text(x + 12 * label_side, mid + 4, label, 12, "start" if label_side > 0 else "end")


def led_v(x, y1, y2, c=WIRE, label=None):
    """LED pointing down (anode at top) from y1 to y2."""
    mid = (y1 + y2) / 2
    line(x, y1, x, mid - 9, c)
    line(x, mid + 9, x, y2, c)
    add(f'<polygon points="{x - 9},{mid - 9} {x + 9},{mid - 9} {x},{mid + 7}" fill="white" stroke="{c}" stroke-width="2"/>')
    line(x - 9, mid + 8, x + 9, mid + 8, c, 2)
    for dx in (0, 7):
        line(x + 12 + dx, mid - 4, x + 19 + dx, mid - 11, c, 1.5)
    if label:
        text(x - 13, mid + 4, label, 12, "end")


def led_h(x1, x2, y, c=WIRE, dash=False):
    """LED pointing left (anode on the right) from x2 to x1."""
    mid = (x1 + x2) / 2
    line(x2, y, mid + 9, y, c, dash=dash)
    line(mid - 9, y, x1, y, c, dash=dash)
    add(f'<polygon points="{mid + 9},{y - 9} {mid + 9},{y + 9} {mid - 7},{y}" fill="white" stroke="{c}" stroke-width="2"/>')
    line(mid - 8, y - 9, mid - 8, y + 9, c, 2)
    for dx in (0, 7):
        line(mid - 2 + dx, y - 12, mid + 5 + dx, y - 19, c, 1.5)


def button_h(x1, x2, y, c=WIRE, dash=False):
    mid = (x1 + x2) / 2
    line(x2, y, mid + 14, y, c, dash=dash)
    line(mid - 14, y, x1, y, c, dash=dash)
    add(f'<circle cx="{mid + 14}" cy="{y}" r="3" fill="white" stroke="{c}" stroke-width="2"/>')
    add(f'<circle cx="{mid - 14}" cy="{y}" r="3" fill="white" stroke="{c}" stroke-width="2"/>')
    line(mid - 16, y - 10, mid + 16, y - 10, c, 2)
    line(mid, y - 10, mid, y - 18, c, 2)


def caps_pair(x, y_top, y_bot, label=True):
    """100 uF electrolytic and 100 nF ceramic in parallel between 5V (top) and GND."""
    for i, (cx, name) in enumerate(((x, "100µF"), (x + 70, "100nF"))):
        mid = (y_top + y_bot) / 2
        line(cx, y_top, cx, mid - 5, P5V if True else WIRE)
        line(cx, mid + 5, cx, y_bot, GND)
        line(cx - 11, mid - 5, cx + 11, mid - 5, WIRE, 3)
        if i == 0:
            add(f'<path d="M{cx - 11},{mid + 8} Q{cx},{mid + 1} {cx + 11},{mid + 8}" fill="none" stroke="{WIRE}" stroke-width="3"/>')
            text(cx - 15, mid - 6, "+", 12, "end")
        else:
            line(cx - 11, mid + 5, cx + 11, mid + 5, WIRE, 3)
        if label:
            text(cx + 14, mid + 4, name, 11)
    line(x, y_top, x + 70, y_top, P5V)
    line(x, y_bot, x + 70, y_bot, GND)


def pin_label(x, y, s, anchor, c="#111827"):
    text(x, y + 4, s, 12, anchor, c=c)


add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
    f'font-family="Helvetica, Arial, sans-serif">')
add(f'<rect width="{W}" height="{H}" fill="white"/>')
text(30, 40, "CPU/RAM gauge: wiring (final version)", 22, weight="bold")
text(30, 62, "Nano pins grouped by function, not in board order. Same label (5V, GND, V0) = same net.", 13, c="#475569")

# ---------------------------------------------------------------- Nano
NX, NY, NW, NH = 560, 140, 200, 760
box(NX, NY, NW, NH, "Arduino Nano V3", "ATmega328P")
# power on top
for px, name in ((NX + 60, "5V"), (NX + 140, "GND")):
    line(px, NY, px, NY - 20, P5V if name == "5V" else GND)
    pin_label(px, NY + 52, name, "middle")
v5(NX + 60, NY - 20)
line(NX + 140, NY - 20, NX + 140, NY - 20)
# GND symbol pointing up is awkward: route to the right and drop
poly([(NX + 140, NY - 20), (NX + NW + 30, NY - 20)], GND)
gnd(NX + NW + 30, NY - 20)
# decoupling at the Nano
caps_pair(NX - 150, 92, 122)
v5(NX - 150, 92)
gnd(NX - 80, 122)
text(NX - 115, 158, "next to the Nano", 11, "middle", c="#475569", italic=True)
# USB
line(NX + NW / 2, NY + NH, NX + NW / 2, NY + NH + 30, WIRE, 3)
text(NX + NW / 2, NY + NH + 48, "Mini-USB → computer (power + serial)", 13, "middle", weight="bold")
text(NX + NW / 2, NY + NH + 66, "D0/D1 are the USB serial: leave them free", 12, "middle", c="#475569")

# ---------------------------------------------------------------- TB6612FNG + motor
TX, TY, TW, TH = 250, 170, 170, 330
box(TX, TY, TW, TH, "TB6612FNG", "motor driver")
right_pins = [("AIN1", 220, "D2"), ("AIN2", 250, "D3"), ("BIN1", 280, "D8"), ("BIN2", 310, "D9")]
for name, y, npin in right_pins:
    pin_label(TX + TW - 8, y, name, "end")
    line(TX + TW, y, NX, y, SIG["motor"])
    pin_label(NX + 8, y, npin, "start")
for name, y in (("PWMA", 360), ("PWMB", 390), ("STBY", 420)):
    pin_label(TX + TW - 8, y, name, "end")
    v5h(TX + TW, y, 1)
left_pins = [("AO1", 215), ("AO2", 255), ("BO1", 305), ("BO2", 345), ("VM", 400), ("VCC", 430), ("GND", 470)]
for name, y in left_pins:
    pin_label(TX + 8, y, name, "start")
# motor coils
MX = 150
for (a, ya), (b, yb), label, ohm in ((left_pins[0], left_pins[1], "coil A", "~135 Ω"),
                                      (left_pins[2], left_pins[3], "coil B", "~142 Ω")):
    line(TX, ya, MX, ya, SIG["motor"])
    line(TX, yb, MX, yb, SIG["motor"])
    # inductor bumps
    n, top, bot = 3, ya, yb
    step = (bot - top) / n
    d = f"M{MX},{top} " + " ".join(f"A{step / 2},{step / 2} 0 0 0 {MX},{top + step * (i + 1)}" for i in range(n))
    add(f'<path d="{d}" fill="none" stroke="{SIG["motor"]}" stroke-width="2"/>')
    text(MX - 26, (ya + yb) / 2 + 4, f"{label} {ohm}", 12, "end")
add(f'<rect x="20" y="170" width="{MX - 1}" height="250" rx="8" fill="none" stroke="#94a3b8" stroke-dasharray="5 4"/>')
text(30, 190, "VDO 91 255 008", 13, weight="bold")
for i, t in enumerate(("4 pins, pairs crossed:", "find each coil with", "the multimeter.", "Wrong direction? Swap", "one coil's wires.")):
    text(30, 350 + 14 * i, t, 11, c="#475569")
# driver power
v5h(TX, 400, -1)
v5h(TX, 430, -1)
gndh(TX, 470, -1)
caps_pair(70, 460, 490)
v5(70, 460)
gnd(140, 490)
text(105, 530, "next to VM", 11, "middle", c="#475569", italic=True)

# ---------------------------------------------------------------- button, warnings, future
# D4 button
y = 580
pin_label(NX + 8, y, "D4", "start")
line(NX, y, 470, y, SIG["io"])
button_h(400, 470, y, SIG["io"])
line(400, y, 370, y, SIG["io"])
gndh(370, y, -1)
text(435, y + 22, "CPU/RAM button", 12, "middle")
text(435, y + 37, "(INPUT_PULLUP, to GND)", 11, "middle", c="#475569")
# warnings
for y, pin in ((660, "D5"), (720, "D6")):
    pin_label(NX + 8, y, pin, "start")
    line(NX, y, 500, y, SIG["io"])
    resistor_h(430, 500, y, "330Ω", SIG["io"])
    led_h(350, 430, y, SIG["io"])
    line(350, y, 330, y, SIG["io"])
    gndh(330, y, -1)
text(270, 693, "warning lights", 12, "end")
text(270, 708, "(salvaged LEDs)", 11, "end", c="#475569")
# future
y = 800
pin_label(NX + 8, y, "D11", "start", SIG["future"])
line(NX, y, 470, y, SIG["future"], dash=True)
button_h(400, 470, y, SIG["future"], dash=True)
line(400, y, 370, y, SIG["future"], dash=True)
gndh(370, y, -1)
text(435, y + 22, "Pomodoro button (future)", 12, "middle", c=SIG["future"])
y = 860
pin_label(NX + 8, y, "D13", "start", SIG["future"])
line(NX, y, 440, y, SIG["future"], dash=True)
add(f'<rect x="400" y="{y - 12}" width="40" height="24" rx="12" fill="white" stroke="{SIG["future"]}" stroke-width="2"/>')
text(420, y + 4, "piezo", 11, "middle", c=SIG["future"])
line(400, y, 370, y, SIG["future"], dash=True)
gndh(370, y, -1)
text(420, y + 30, "passive piezo (future)", 12, "middle", c=SIG["future"])

# ---------------------------------------------------------------- LCD
LX, LY, LW = 1010, 150, 190
pins = ["1 VSS", "2 VDD", "3 V0", "4 RS", "5 RW", "6 E", "7 D0", "8 D1", "9 D2", "10 D3",
        "11 D4", "12 D5", "13 D6", "14 D7", "15 A", "16 K"]
py = {i + 1: 175 + 25 * i for i in range(16)}
box(LX, LY - 50, LW, py[16] - LY + 75, "16×2 LCD", "JHD162A, 4-bit mode")
for i, name in enumerate(pins, 1):
    c = SIG["future"] if 7 <= i <= 10 else "#111827"
    pin_label(LX + 10, py[i], name, "start", c)
text(LX + LW - 10, py[8] + 4, "not", 11, "end", c=SIG["future"])
text(LX + LW - 10, py[9] + 4, "connected", 11, "end", c=SIG["future"])
gndh(LX, py[1], -1)
v5h(LX, py[2], -1)
line(LX, py[3], LX - 22, py[3], SIG["lcd"])
text(LX - 26, py[3] + 4, "V0", 12, "end", c=SIG["lcd"], weight="bold")
gndh(LX, py[5], -1)
for pin, npin in ((4, "D7"), (6, "D12"), (11, "A0"), (12, "A1"), (13, "A2"), (14, "A3")):
    line(NX + NW, py[pin], LX, py[pin], SIG["lcd"])
    pin_label(NX + NW - 8, py[pin], npin, "end")
v5h(LX, py[15], -1)
# contrast trimpot (top right)
TPX = 1300
text(TPX, 112, "contrast", 13, "middle", weight="bold")
v5(TPX, 140)
resistor_v(TPX, 140, 220, "10 kΩ", label_side=1)
gnd(TPX, 220)
add(f'<polygon points="{TPX - 8},180 {TPX - 20},173 {TPX - 20},187" fill="{SIG["lcd"]}"/>')
line(TPX - 20, 180, TPX - 50, 180, SIG["lcd"])
text(TPX - 54, 184, "V0", 12, "end", c=SIG["lcd"], weight="bold")
text(TPX, 262, "trimpot (3296W)", 11, "middle", c="#475569")
# decoupling near LCD
caps_pair(1290, 340, 380)
v5(1290, 340)
gnd(1360, 380)
text(1325, 418, "next to the LCD", 11, "middle", c="#475569", italic=True)

# ---------------------------------------------------------------- lighting
RAIL_Y, BUS_Y = 620, 760
rail_x1, rail_x2 = 1030, 1370
line(rail_x1, RAIL_Y, rail_x2, RAIL_Y, P5V)
v5(rail_x1, RAIL_Y)
branches = [(1090, "white"), (1160, "white"), (1300, "orange"), (1370, "orange")]
for bx, colour in branches:
    dot(bx, RAIL_Y, P5V)
    resistor_v(bx, RAIL_Y, 680, "150Ω", SIG["light"], 1)
    led_v(bx, 690, 740, SIG["light"])
    line(bx, 680, bx, 690, SIG["light"])
    line(bx, 740, bx, BUS_Y, SIG["light"])
    dot(bx, BUS_Y, SIG["light"])
text(1230, 700, "…", 22, "middle")
text(1125, 600, "scale: ~8 × cool white", 12, "middle")
text(1335, 600, "needle: ~2 × orange", 12, "middle")
# LCD backlight K to the bus
line(LX, py[16], 985, py[16], SIG["light"])
poly([(985, py[16]), (985, BUS_Y), (1370, BUS_Y)], SIG["light"])
dot(985, BUS_Y, SIG["light"])
text(980, 690, "LCD backlight", 11, "end", c="#475569")
text(980, 704, "(K, pin 16)", 11, "end", c="#475569")
# BC337
QX, QY = 1200, 840     # base contact point is (QX - 20, QY)
add(f'<circle cx="{QX}" cy="{QY}" r="26" fill="white" stroke="{WIRE}" stroke-width="2"/>')
line(QX - 10, QY - 15, QX - 10, QY + 15, WIRE, 3)
line(QX - 36, QY, QX - 10, QY, WIRE)
line(QX - 10, QY - 6, QX + 10, QY - 22, WIRE)
line(QX - 10, QY + 6, QX + 10, QY + 22, WIRE)
add(f'<polygon points="{QX + 10},{QY + 22} {QX + 1},{QY + 20} {QX + 6},{QY + 13}" fill="{WIRE}"/>')
line(QX + 10, QY - 22, QX + 10, BUS_Y, SIG["light"])
dot(QX + 10, BUS_Y, SIG["light"])
line(QX + 10, QY + 22, QX + 10, QY + 45, GND)
gnd(QX + 10, QY + 45)
text(QX + 34, QY - 8, "BC337", 13, weight="bold")
text(QX + 34, QY + 8, "C top, E bottom", 11, c="#475569")
text(QX + 34, QY + 22, "flat face: C B E", 11, c="#475569")
text(QX + 16, QY - 30, "C", 11)
text(QX + 16, QY + 40, "E", 11)
text(QX - 40, QY - 6, "B", 11, "end")
# D10 → 1k → base, 10k to GND
pin_label(NX + NW - 8, QY, "D10", "end")
line(NX + NW, QY, 1000, QY, SIG["light"])
resistor_h(1000, 1100, QY, "1 kΩ", SIG["light"])
line(1100, QY, QX - 36, QY, SIG["light"])
dot(1120, QY, SIG["light"])
resistor_v(1120, QY, QY + 70, "10 kΩ", label_side=-1)
gnd(1120, QY + 70)
text(1040, 980, "lighting: brightness from D10 (PWM). Each LED has its own resistor.", 12, c="#475569")

# ---------------------------------------------------------------- legend
lx, ly = 30, 920
for i, (name, c) in enumerate((("5V", P5V), ("GND", GND), ("motor", SIG["motor"]), ("LCD", SIG["lcd"]),
                               ("lighting", SIG["light"]), ("buttons / warnings", SIG["io"]),
                               ("future", SIG["future"]))):
    x = lx + (i % 3) * 165
    yy = ly + (i // 3) * 22
    line(x, yy, x + 28, yy, c, 3, dash=name == "future")
    text(x + 36, yy + 4, name, 12)

add("</svg>")
open(sys.argv[1], "w").write("\n".join(out))
