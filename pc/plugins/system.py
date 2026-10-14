"""CPU and RAM usage: the needles, the metric the needle isn't on row 0 with the app
using the most of it, and past a threshold the app behind the needle on row 1."""

import os
import re
import sys
from collections import defaultdict

import psutil

import lcd
from plugins import Plugin

CPU_ICON = lcd.glyph([
    ".#.#.",   # CPU chip
    "#####",
    "#...#",
    "#.#.#",
    "#...#",
    "#####",
    ".#.#.",
    "....."])
RAM_ICON = lcd.glyph([
    ".....",   # RAM module, contacts at the bottom
    "#####",
    "#...#",
    "#...#",
    "#####",
    "#.#.#",
    "#.#.#",
    "....."])
APP_ICON = lcd.glyph([
    ".....",   # app window, for an app's own share (the others are the PC's total)
    "#####",
    "#####",
    "#...#",
    "#...#",
    "#...#",
    "#####",
    "....."])

_denied = set()  # system processes need root: skip them on later scans
_cores = psutil.cpu_count() or 1  # None when it can't tell
_ram_total = psutil.virtual_memory().total


def app_name(p):
    """The app a process belongs to. On macOS, the outermost .app bundle, so helper
    and content processes ("plugin-container" inside Firefox.app) count towards
    their app; outside a bundle, the command it was started as ("claude", not its
    binary named after the version)."""
    if sys.platform != "darwin":
        return p.name()
    bundle = re.search(r"/([^/]+)\.app/", p.exe())
    if bundle:
        return bundle.group(1)
    cmd = p.cmdline()
    return os.path.basename(cmd[0]) if cmd else p.name()


def top_apps():
    """(name, % of the total) of the apps using the most CPU and RAM, leaving out
    this script. A process counts towards its app (app_name())."""
    cpu, ram = defaultdict(float), defaultdict(int)
    denied = set()
    for p in psutil.process_iter():
        if p in _denied:
            denied.add(p)
            continue
        if p.pid == os.getpid():
            continue
        try:
            with p.oneshot():
                name, c, m = app_name(p), p.cpu_percent(), p.memory_info().rss
        except psutil.AccessDenied:
            denied.add(p)
            continue
        except psutil.NoSuchProcess:
            continue
        if not name:
            continue
        cpu[name] += c
        ram[name] += m
    _denied.clear()  # forget the ones that ended
    _denied.update(denied)
    top_cpu = max(cpu, key=cpu.get, default="")
    top_ram = max(ram, key=ram.get, default="")
    # A process's CPU % is of one core (0-800% with 8): scale it to the whole machine
    return ((top_cpu, cpu.get(top_cpu, 0) / _cores),
            (top_ram, ram.get(top_ram, 0) / _ram_total * 100))


def metric_row(icon, value, app):
    # "C 5% Code", "C 72% Code": low values leave more room for the name
    row = f"{icon} {value:.0f}% "
    # Accents off before fitting: a decomposed "é" (NFD, common in macOS paths) is
    # two characters until then, "™" one that becomes "TM"
    return lcd.fit(row + lcd.ascii(app), len(row))


class System(Plugin):
    """alpha: CPU smoothing, s = s*(1-alpha) + new*alpha (0.04: ~1.2 s time constant).
    hot: needle metric % from which row 1 shows the app using the most of it, and
    below which it goes back, so the row doesn't flicker. RAM higher: macOS sits at
    60-70%. apps_interval: seconds between top app scans (a scan costs ~15 ms of
    CPU); hot_apps_interval: while CPU or RAM is past hot, so the app keeps up.
    Icons: a glyph, or a letter ("C", "R")."""

    def __init__(self, alpha=0.04, hot={"CPU": (60, 50), "RAM": (80, 75)},
                 apps_interval=5, hot_apps_interval=1,
                 cpu_icon=CPU_ICON, ram_icon=RAM_ICON, app_icon=APP_ICON):
        self.alpha, self.hot = alpha, hot
        self.apps_interval, self.hot_apps_interval = apps_interval, hot_apps_interval
        self.cpu_icon, self.ram_icon, self.app_icon = cpu_icon, ram_icon, app_icon
        self.needles = {"CPU": lambda: self.cpu or 0, "RAM": lambda: self.ram or 0}
        self.cpu = self.ram = None
        self.hot_cpu = self.hot_ram = False  # past hot
        self.stale_cpu = False  # the next scan's CPU figures can't be trusted
        self.fresh_cpu = False  # top_cpu is from a scan with real CPU figures
        self.top_cpu = self.top_ram = ("", 0)
        self.next_scan = 0

    def start(self):
        psutil.cpu_percent()  # the first reading is always 0
        top_apps()            # same for each process

    def resume(self):
        # Each app's CPU would span the pause: keep the last app on row 0 and none on
        # row 1 (uptime and network) until the next scan, 1 s later
        self.stale_cpu = True
        self.next_scan = 0

    def past(self, needle, value, was):
        """Whether the metric is past hot: on from its first value, off below the
        second."""
        on, off = self.hot[needle]
        return value >= (off if was else on)

    def update(self, ctx):
        cpu = psutil.cpu_percent()
        self.cpu = cpu if self.cpu is None else self.cpu * (1 - self.alpha) + cpu * self.alpha
        self.ram = psutil.virtual_memory().percent

        was_high, was_cpu_hot = self.hot_cpu or self.hot_ram, self.hot_cpu
        self.hot_cpu = self.past("CPU", self.cpu, self.hot_cpu)
        self.hot_ram = self.past("RAM", self.ram, self.hot_ram)
        high = self.hot_cpu or self.hot_ram
        if high and not was_high:
            self.next_scan = ctx.now  # the last scan is from before the rise
        # A process's first scan reads 0% CPU: whatever started the rise may be new,
        # and only shows on the scan after
        if self.hot_cpu and not was_cpu_hot:
            self.stale_cpu = True

        # Faster while high (either: the needle may switch), so the app keeps up
        if ctx.now >= self.next_scan:
            app_cpu, self.top_ram = top_apps()
            if not self.stale_cpu:
                self.top_cpu = app_cpu
            self.fresh_cpu = not self.stale_cpu
            if ctx.verbose:
                print(f"Top CPU {self.top_cpu[0]} {self.top_cpu[1]:.0f}%  "
                      f"Top RAM {self.top_ram[0]} {self.top_ram[1]:.0f}%")
            self.next_scan = ctx.now + (self.hot_apps_interval if self.stale_cpu or high
                                        else self.apps_interval)
            self.stale_cpu = False

    def rows(self, ctx):
        if self.cpu is None:
            return None, None
        # An app's share can't pass the total: summed RSS counts shared pages once per
        # process, and the share is from the last scan, up to apps_interval ago
        top_cpu = self.top_cpu[0], min(self.top_cpu[1], self.cpu)
        top_ram = self.top_ram[0], min(self.top_ram[1], self.ram)
        if ctx.needle == "CPU":
            app = self.hot_cpu and self.fresh_cpu and top_cpu[0]
            return (metric_row(self.ram_icon, self.ram, top_ram[0]),
                    metric_row(self.app_icon, top_cpu[1], top_cpu[0]) if app else None)
        app = ctx.needle == "RAM" and self.hot_ram and top_ram[0]
        return (metric_row(self.cpu_icon, self.cpu, top_cpu[0]),
                metric_row(self.app_icon, top_ram[1], top_ram[0]) if app else None)
