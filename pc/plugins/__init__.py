"""Plugins: what the needle and the LCD show.

gauge.py's PLUGINS lists them. A click moves the needle to the next of their needles,
in that order. Each LCD row shows the first plugin in the list with something for it;
one that has nothing (None) leaves the row to the next.

A plugin subclasses Plugin and overrides only what it needs. Custom characters come
from lcd.glyph(), text helpers from lcd.py. Keep update() quick: it runs in the loop
that moves the needle every 50 ms; anything that waits (network) goes to a thread
(see email.py).
"""

from collections import namedtuple
from types import MappingProxyType

# now: time.monotonic(); needle: the name of the needle's metric; verbose: gauge.py -v
Context = namedtuple("Context", "now needle verbose")


class Plugin:
    interval = 0      # seconds between update() calls; 0 = every reading (50 ms)
    # needle name -> function returning its %, e.g. {"CPU": ...}; a plugin sets its
    # own (read-only here, as every plugin would share it)
    needles = MappingProxyType({})

    def start(self):
        """Once, when gauge.py starts."""

    def update(self, ctx):
        """Refresh the figures. Not called while the computer's screen sleeps or the
        Arduino runs the Pomodoro."""

    def resume(self):
        """After such a pause: figures from before would span it. update() follows at
        once."""

    def rows(self, ctx):
        """(row 0, row 1) of LCD text, None for a row it has nothing for. Every 0.25 s
        while update() runs."""
        return None, None

    def notifying(self, ctx):
        """Whether what it shows now is a notification (new email): the LCD's light
        goes to full meanwhile. Asked with rows(), every 0.25 s."""
        return False
