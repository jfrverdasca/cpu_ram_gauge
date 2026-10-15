"""What gauge.py and its plugins share about the 16x2 LCD: text that fits, and custom
characters (glyphs) loaded into the LCD's 8 slots while they're shown."""

import time
import unicodedata

WIDTH = 16
TEXT_INTERVAL = 0.25  # seconds between LCD text updates
# Both multiples of TEXT_INTERVAL: each step then comes after a whole number of updates
SCROLL_STEP = 0.5     # seconds per character when a text doesn't fit (2 text updates)
SCROLL_PAUSE = 2      # seconds still at each end

# Each registered glyph stands for a character of Unicode's private use area in the
# text, until gauge.py swaps it for the slot it's loaded into
_GLYPH_BASE = 0xE000
_patterns = []


def glyph(rows):
    """The character to put in text for a custom 5x8 character: 8 rows of "#" and
    ".", top first."""
    if rows not in _patterns:
        _patterns.append(rows)
    return chr(_GLYPH_BASE + _patterns.index(rows))


def is_glyph(ch):
    return 0 <= ord(ch) - _GLYPH_BASE < len(_patterns)


def ascii(text):
    """text with only what the LCD has: accents dropped ("João" -> "Joao"), anything
    else that isn't ASCII as "?". Glyphs stay."""
    out = []
    for ch in unicodedata.normalize("NFKD", text):
        if ch.isascii() or is_glyph(ch):
            out.append(ch)
        elif not unicodedata.combining(ch):
            out.append("?")
    return "".join(out)


def scroll_time(text, width=WIDTH):
    """Seconds scroll() takes to slide text once from start to end and back to the
    start: 0 when it fits."""
    extra = len(text) - width
    return 2 * SCROLL_PAUSE + extra * SCROLL_STEP if extra > 0 else 0


def next_update(now):
    """When the next text update is due: on multiples of TEXT_INTERVAL, so updates
    don't drift against scroll()'s steps (a loop that waits TEXT_INTERVAL from each
    update runs a little slower, and every few seconds two steps came one update
    apart)."""
    return (now // TEXT_INTERVAL + 1) * TEXT_INTERVAL


def scroll(text, width=WIDTH, since=0):
    """The part of text to show now: slides left when it doesn't fit, pausing at
    each end. With since (time.monotonic()), it starts from the beginning then.
    Counts whole text updates (next_update()), so a step comes every
    SCROLL_STEP / TEXT_INTERVAL updates, however late in its slot each one runs."""
    extra = len(text) - width
    if extra <= 0:
        return text
    ticks = int(time.monotonic() // TEXT_INTERVAL) - int(since // TEXT_INTERVAL)
    t = ticks * TEXT_INTERVAL % scroll_time(text, width)
    shift = min(extra, max(0, int((t - SCROLL_PAUSE) / SCROLL_STEP)))
    return text[shift:shift + width]


def fit(text, fixed=0, since=0):
    """A row: the first fixed characters stay still, the rest scrolls."""
    return text[:fixed] + scroll(text[fixed:], WIDTH - fixed, since)


def _slot_char(slot):
    # Slot 0 is chr(8): code 0 would end the line on the Arduino, and the LCD repeats
    # slots 0-7 at codes 8-15. Not 8 + n for the others: 10 and 13 are \n and \r
    return chr(slot or 8)


class Glyphs:
    """Which glyph each of the LCD's 8 slots holds. The slots are RAM in the LCD's
    controller: rewriting them doesn't wear it, but they're lost when the Arduino
    restarts (reset())."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.slots = [None] * 8

    def place(self, rows, shown):
        """("G<slot><hex>" lines to load the glyphs rows use, rows with each glyph as
        its slot's character). shown: the rows on the LCD until these arrive. A
        loaded glyph keeps its slot. A new one takes an empty slot, else one no row
        needs, preferably not in shown: rewriting a slot changes it on the LCD at
        once. Past 8 glyphs, the rest show as a space."""
        needed = list(dict.fromkeys(ch for row in rows for ch in row if is_glyph(ch)))
        on_screen = {ch for row in shown if row for ch in row}
        lines = []
        for ch in needed:
            if ch in self.slots:
                continue
            free = [n for n, g in enumerate(self.slots) if g not in needed]
            if not free:
                break
            slot = min(free, key=lambda n: (self.slots[n] is not None, self.slots[n] in on_screen))
            self.slots[slot] = ch
            bits = "".join(f"{int(r.replace('#', '1').replace('.', '0'), 2):02X}"
                           for r in _patterns[ord(ch) - _GLYPH_BASE])
            lines.append(f"G{slot}{bits}\n")
        chars = {g: _slot_char(n) for n, g in enumerate(self.slots) if g}
        return lines, ["".join(chars.get(ch, " ") if is_glyph(ch) else ch for ch in row)
                       for row in rows]
