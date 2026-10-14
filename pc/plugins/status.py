"""Uptime and network speed on row 1: "⏻ 03:26h   ↓1.2M"."""

import socket
import time

import psutil

import lcd
from plugins import Plugin

UPTIME_ICON = lcd.glyph([
    ".....",   # power symbol
    "..#..",
    ".###.",
    "#.#.#",
    "#.#.#",
    "#...#",
    ".###.",
    "....."])
DOWN_ICON = lcd.glyph([
    "..#..",
    "..#..",
    "..#..",
    "..#..",
    "#.#.#",
    ".###.",
    "..#..",
    "....."])
UP_ICON = lcd.glyph([
    "..#..",
    ".###.",
    "#.#.#",
    "..#..",
    "..#..",
    "..#..",
    "..#..",
    "....."])

INTERFACE_INTERVAL = 5  # seconds between checks of the internet interface (Wi-Fi <-> cable)


def uptime():
    # "03:26h" (hours:minutes), or "02:05d" (days:hours) after a day
    m = int(time.time() - psutil.boot_time()) // 60
    d, h, m = m // 1440, m // 60 % 24, m % 60
    return f"{d:02d}:{h:02d}d" if d else f"{h:02d}:{m:02d}h"


def rate(bps):
    """Bytes per second in at most 4 characters: "0.3K", "12K", "345K", "1.2M"."""
    for unit in "KMG":
        bps /= 1000
        if bps < 9.95:
            return f"{bps:.1f}{unit}"
        if bps < 999.5:
            return f"{bps:.0f}{unit}"
    return f"{bps:.0f}G"


def net_interface():
    """Name of the interface that traffic to the internet goes through, None when
    offline. The same on every OS, unlike interface names. A UDP connect only picks
    the route: nothing is sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 53))
            ip = sock.getsockname()[0]
    except OSError:
        return None
    for name, addrs in psutil.net_if_addrs().items():
        if any(a.address == ip for a in addrs):
            return name
    return None


class Network:
    """Download and upload speed of the internet interface, in bytes per second.
    None while offline and until the second reading after a change of interface."""

    def __init__(self):
        self.iface = self.last = self.down = self.up = None

    def find_interface(self):
        iface = net_interface()
        if iface != self.iface:
            self.iface = iface
            self.last = self.down = self.up = None

    def read(self):
        c = psutil.net_io_counters(pernic=True).get(self.iface) if self.iface else None
        now = time.monotonic()
        if c and self.last:
            t, recv, sent = self.last
            self.down = max(0, c.bytes_recv - recv) / (now - t)
            self.up = max(0, c.bytes_sent - sent) / (now - t)
        else:
            self.down = self.up = None
        self.last = (now, c.bytes_recv, c.bytes_sent) if c else None

    def text(self):
        """The faster direction only, for lack of space; download on a tie."""
        if self.down is None:
            return DOWN_ICON + "--"
        if self.up > self.down:
            return UP_ICON + rate(self.up)
        return DOWN_ICON + rate(self.down)


class Status(Plugin):
    interval = 1      # seconds between network speed readings (~70 us each)

    def __init__(self):
        self.net = Network()
        self.next_interface = 0

    def resume(self):
        self.net.last = None  # the next reading would span the pause

    def update(self, ctx):
        if ctx.now >= self.next_interface:
            self.net.find_interface()
            self.next_interface = ctx.now + INTERFACE_INTERVAL
        self.net.read()

    def rows(self, ctx):
        # The faster of download/upload right-aligned
        left = f"{UPTIME_ICON} {uptime()}"
        return None, left + self.net.text().rjust(lcd.WIDTH - len(left))
