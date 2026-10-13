#!/usr/bin/env python3
"""Drive the Arduino needle gauge over serial: CPU or RAM usage on the needle.

The Arduino is only the physical part; this script decides what it shows. The needle
goes as "N45.3" every reading (also the sign the script is alive), the LCD rows as
"T<row><text>" when they change. Custom LCD characters are sent as "G<slot><8 rows
in hex>", the backlight as "B<0-255>", the Pomodoro minutes as "M<focus> <break>
<long break> <cycles>", the piezo as "S<Hz> <volume>" and the time for the Arduino's
clock module as "Z<UTC seconds> <standard offset, minutes> <0/1>": on connecting and
whenever the Arduino says READY (it restarted). A click on the Arduino's button
("CLICK") switches the needle to the next of MODES. Change lcd_rows() and GLYPHS to
show something else.

While the computer's screen is asleep (macOS and Linux) it parks the needle and
turns the LCD off.

On exit (Ctrl+C, logout or shutdown) it asks the Arduino to park the needle at 0%,
so the next boot doesn't need to home.

    python3 gauge.py [--port /dev/cu.usbmodem101] [-v]
"""

import argparse
import ctypes
import ctypes.util
import glob
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

import psutil
import serial
from serial.tools import list_ports

# ---------------------------------------------------------------- settings
# Safe to change.

BRIGHTNESS = 128      # LCD backlight, 1-255
MODES = ["C", "R"]    # what the needle shows, in click order: C = CPU, R = RAM
NOTICE = 3            # seconds "Needle: RAM" stays on row 0 after a click
ALPHA = 0.04          # CPU smoothing: s = s*0.96 + new*0.04 (~1.2 s time constant)
# Needle metric % from which row 1 shows the app using the most of it, and below
# which it goes back, so the row doesn't flicker. RAM higher: macOS sits at 60-70%.
HOT = {"C": (60, 50), "R": (80, 75)}
APPS_INTERVAL = 5     # seconds between top app scans (a scan costs ~15 ms of CPU)
HOT_APPS_INTERVAL = 1 # ... while CPU or RAM is past HOT, so the app on row 1 keeps up
NET_INTERVAL = 1      # seconds between network speed readings (~70 us each)
SCROLL_STEP = 0.5     # seconds per character when an app name doesn't fit (2 text updates)
SCROLL_PAUSE = 2      # seconds still at each end

# Pomodoro, run by the Arduino (also without this script); saved there
POMODORO_FOCUS = 25       # minutes, 1-99
POMODORO_BREAK = 5        # minutes, 1-99
POMODORO_LONG_BREAK = 15  # minutes, 1-99
POMODORO_CYCLES = 4       # focus sessions before the long break, 1-9
BEEP_HZ = 2000            # piezo tone, 1000-5000
BEEP_VOLUME = 50          # 0 (silent) - 100

# Custom 5x8 LCD characters, top row first. In text, slot n is chr(n), except slot 0,
# which is chr(8): code 0 would end the line on the Arduino, and the LCD repeats
# slots 0-7 at codes 8-15. Don't use 8 + n for the others: 10 and 13 are \n and \r.
GLYPHS = [
    [".#.#.",   # 0: CPU chip
     "#####",
     "#...#",
     "#.#.#",
     "#...#",
     "#####",
     ".#.#.",
     "....."],
    [".....",   # 1: RAM module, contacts at the bottom
     "#####",
     "#...#",
     "#...#",
     "#####",
     "#.#.#",
     "#.#.#",
     "....."],
    [".....",   # 2: power symbol, for uptime
     "..#..",
     ".###.",
     "#.#.#",
     "#.#.#",
     "#...#",
     ".###.",
     "....."],
    ["..#..",   # 3: down arrow
     "..#..",
     "..#..",
     "..#..",
     "#.#.#",
     ".###.",
     "..#..",
     "....."],
    ["..#..",   # 4: up arrow
     ".###.",
     "#.#.#",
     "..#..",
     "..#..",
     "..#..",
     "..#..",
     "....."],
    [".....",
     "#####",   # 5: app window, for an app's own share (the others are the PC's total)
     "#####",
     "#...#",
     "#...#",
     "#...#",
     "#####",
     "....."],
]
CPU_ICON = chr(8)  # or "C" for a plain letter
RAM_ICON = chr(1)  # or "R"
UPTIME_ICON = chr(2)
DOWN_ICON = chr(3)
UP_ICON = chr(4)
APP_ICON = chr(5)

# ---------------------------------------------------------------- internals

PROTOCOL = b"2"       # what the firmware must say in READY
BAUD = 115200
INTERVAL = 0.05       # seconds between readings
READY_WAIT = 10       # the Arduino resets when the port opens and may home
CHECK_INTERVAL = 5    # seconds between screen and network interface checks
CLOCK_INTERVAL = 3600 # seconds between clock syncs
TEXT_INTERVAL = 0.25  # seconds between LCD text updates
LCD_WIDTH = 16
NAMES = {"C": "CPU", "R": "RAM"}

# Arduino, Arduino.org, CH340, FTDI, CP210x
KNOWN_VIDS = {0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4}


def find_port():
    ports = [p.device for p in list_ports.comports() if p.vid in KNOWN_VIDS]
    return ports[0] if ports else None


def connect(port):
    # Exclusive: macOS and Linux let a second program open the port, and the Arduino
    # then gets both streams of readings. A second gauge.py waits instead (the lock
    # is advisory: programs that don't ask for it can still open the port).
    s = serial.Serial(port, BAUD, timeout=0.1, write_timeout=1, exclusive=True)
    deadline = time.monotonic() + READY_WAIT
    while time.monotonic() < deadline:
        line = s.readline().strip()
        if line.startswith(b"READY"):
            check_protocol(s, line)
            break
    else:
        print("No READY from the Arduino: assuming it runs the matching firmware")
    print(f"Connected to {port}")
    return s


def check_protocol(s, ready):
    if ready.split()[1:] != [PROTOCOL]:
        s.close()
        sys.exit(f"The Arduino speaks another protocol ({ready.decode()!r}): "
                 "upload firmware/gauge/gauge.ino")


def read_lines(s, buf):
    """Complete lines received so far, and the rest of buf. Never waits."""
    buf += s.read(s.in_waiting)
    *lines, rest = buf.split(b"\n")
    return [line.strip() for line in lines], rest


def park(s):
    """The Arduino's answer: PARKED, POMODORO ON (the needle stays with it) or None."""
    s.reset_input_buffer()
    s.write(b"\nP\n")  # the newline ends a line cut short by Ctrl+C
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        line = s.readline().strip()
        if line in (b"PARKED", b"POMODORO ON"):
            return line
    return None


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


def scroll(text, width):
    """The part of text to show now: slides left when it doesn't fit, pausing at
    each end."""
    extra = len(text) - width
    if extra <= 0:
        return text
    t = time.monotonic() % (2 * SCROLL_PAUSE + extra * SCROLL_STEP)
    shift = min(extra, max(0, int((t - SCROLL_PAUSE) / SCROLL_STEP)))
    return text[shift:shift + width]


def metric_row(icon, value, app):
    # "C 5% Code", "C 72% Code": low values leave more room for the name
    row = f"{icon} {value:.0f}% "
    return row + scroll(app, LCD_WIDTH - len(row))


def hot(needle, value, was):
    """Whether the needle's metric is past HOT: on from its first value, off below
    the second."""
    on, off = HOT[needle]
    return value >= (off if was else on)


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


def lcd_rows(mode, cpu, ram, top_cpu, top_ram, net, cpu_app, ram_app):
    """The two LCD rows while the needle shows mode. The first row shows the metric
    the needle isn't; the second, uptime and network, or with cpu_app / ram_app the
    app behind the needle and its own share. top_cpu and top_ram are (name, % of
    the total)."""
    # "⏻ 03:26h   ↓1.2M": the faster of download/upload right-aligned
    left = f"{UPTIME_ICON} {uptime()}"
    bottom = left + net.text().rjust(LCD_WIDTH - len(left))
    # An app's share can't pass the total: summed RSS counts shared pages once per
    # process, and the share is from the last scan, up to APPS_INTERVAL ago
    top_cpu = top_cpu[0], min(top_cpu[1], cpu)
    top_ram = top_ram[0], min(top_ram[1], ram)
    if mode == "C":
        return (metric_row(RAM_ICON, ram, top_ram[0]),
                metric_row(APP_ICON, top_cpu[1], top_cpu[0]) if cpu_app and top_cpu[0] else bottom)
    return (metric_row(CPU_ICON, cpu, top_cpu[0]),
            metric_row(APP_ICON, top_ram[1], top_ram[0]) if ram_app and top_ram[0] else bottom)


def glyph_lines():
    for slot, rows in enumerate(GLYPHS):
        bits = "".join(f"{int(row.replace('#', '1').replace('.', '0'), 2):02X}" for row in rows)
        yield f"G{slot}{bits}\n"


def clock_line():
    """UTC time and the local zone, for the clock shown with no data. The Arduino
    applies summer time with the EU rule when the zone has it."""
    return f"Z{int(time.time())} {-time.timezone // 60} {time.daylight}\n"


def text_line(row, text):
    # ASCII only: the LCD has no accents
    return f"T{row}{text.encode('ascii', 'replace').decode()[:LCD_WIDTH]}\n"


def setup_lines(awake):
    """Everything the Arduino keeps: sent on connecting and when it restarts."""
    yield from glyph_lines()
    yield f"M{POMODORO_FOCUS} {POMODORO_BREAK} {POMODORO_LONG_BREAK} {POMODORO_CYCLES}\n"
    yield f"S{BEEP_HZ} {BEEP_VOLUME}\n"
    yield f"B{BRIGHTNESS if awake else 0}\n"
    yield clock_line()


if sys.platform == "darwin":
    _cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
    _cg.CGMainDisplayID.restype = ctypes.c_uint32
    _cg.CGDisplayIsAsleep.argtypes = [ctypes.c_uint32]


def display_on():
    """False while the computer's screen is asleep. True when it can't tell."""
    if sys.platform == "darwin":
        return not _cg.CGDisplayIsAsleep(_cg.CGMainDisplayID())
    if not sys.platform.startswith("linux"):
        return True
    if os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY") and shutil.which("xset"):
        out = subprocess.run(["xset", "q"], capture_output=True, text=True).stdout
        return "Monitor is" not in out or "Monitor is On" in out  # no DPMS: always on
    # Wayland or console: the kernel's state of each connected output
    states = [Path(p).read_text().strip() for p in glob.glob("/sys/class/drm/*/dpms")
              if Path(p).with_name("status").read_text().strip() == "connected"]
    return not states or "On" in states


def on_signal(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", help="serial port (default: auto-detect)")
    parser.add_argument("-v", "--verbose", action="store_true", help="print the values sent")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, on_signal)
    # no SIGHUP on Windows; keep nohup working elsewhere
    if hasattr(signal, "SIGHUP") and signal.getsignal(signal.SIGHUP) != signal.SIG_IGN:
        signal.signal(signal.SIGHUP, on_signal)

    psutil.cpu_percent()  # the first reading is always 0
    top_apps()            # same for each process
    time.sleep(INTERVAL)
    cpu = psutil.cpu_percent()
    mode = MODES[0]
    notice_until = 0      # "Needle: ..." on row 0 until then
    next_check = next_scan = next_text = next_net = next_clock = 0
    awake = True
    hot_cpu = hot_ram = False  # past HOT
    stale_cpu = False  # the next scan's CPU figures can't be trusted
    fresh_cpu = False  # top_cpu is from a scan with real CPU figures
    top_cpu = top_ram = ("", 0)  # if it starts with the screen asleep
    net = Network()
    # Lines other than readings, sent one per reading: the Arduino's serial buffer
    # holds 64 bytes and it stops reading for ~10 ms while it redraws the LCD
    queue = deque()
    sent_rows = [None, None]  # LCD rows as last queued
    rx = b""

    s = None
    try:
        while True:
            try:
                now = time.monotonic()
                restarted = False
                if s is None:
                    port = args.port or find_port()
                    if not port:
                        raise serial.SerialException("Arduino not found")
                    s = connect(port)
                    rx = b""
                    queue.clear()
                    restarted = True

                lines, rx = read_lines(s, rx)
                for line in lines:
                    if line.startswith(b"READY"):
                        check_protocol(s, line)
                        print("The Arduino restarted.")
                        queue.clear()
                        restarted = True
                    elif line == b"CLICK" and awake:
                        mode = MODES[(MODES.index(mode) + 1) % len(MODES)]
                        notice_until = now + NOTICE
                        next_text = now  # the new text at once
                        if args.verbose:
                            print(f"Needle: {NAMES[mode]}")
                    elif line in (b"POMODORO ON", b"POMODORO OFF", b"TEST"):
                        print(line.decode().capitalize())
                if restarted:
                    queue.extend(setup_lines(awake))
                    sent_rows = [None, None]
                    next_check = next_scan = next_text = 0
                    next_clock = now + CLOCK_INTERVAL

                cpu = cpu * (1 - ALPHA) + psutil.cpu_percent() * ALPHA
                ram = psutil.virtual_memory().percent

                was_high, was_cpu_hot = hot_cpu or hot_ram, hot_cpu
                hot_cpu, hot_ram = hot("C", cpu, hot_cpu), hot("R", ram, hot_ram)
                high = hot_cpu or hot_ram
                if high and not was_high:
                    next_scan = now  # the last scan is from before the rise
                # A process's first scan reads 0% CPU: whatever started the rise may
                # be new, and only shows on the scan after
                if hot_cpu and not was_cpu_hot:
                    stale_cpu = True

                woke = False
                if now >= next_check:
                    if display_on() != awake:
                        awake = woke = not awake
                        print("Screen on." if awake else "Screen asleep: parking.")
                        queue.append(f"B{BRIGHTNESS}\n" if awake else "B0\nP\n")
                        if not awake:
                            net.last = None  # the next reading would span the sleep
                    elif not awake:
                        queue.append("B0\n")  # still asleep: the Arduino forgets B0 after 15 s
                    if awake:
                        net.find_interface()  # Wi-Fi and cable may swap
                    next_check = now + CHECK_INTERVAL
                if now >= next_clock:
                    queue.append(clock_line())
                    next_clock = now + CLOCK_INTERVAL

                # Faster while high, so the app keeps up. Just after waking, each
                # app's CPU spans the sleep. With stale CPU figures, keep the last
                # app on row 0 and none on row 1 (uptime and network) until the
                # next scan, 1 s later
                stale_cpu |= woke
                if awake and (woke or now >= next_scan):
                    app_cpu, top_ram = top_apps()
                    if not stale_cpu:
                        top_cpu = app_cpu
                    fresh_cpu = not stale_cpu
                    if args.verbose:
                        print(f"Top CPU {top_cpu[0]} {top_cpu[1]:.0f}%  "
                              f"Top RAM {top_ram[0]} {top_ram[1]:.0f}%")
                    next_scan = now + (HOT_APPS_INTERVAL if stale_cpu or high else APPS_INTERVAL)
                    stale_cpu = False

                if awake:
                    value = cpu if mode == "C" else ram
                    s.write(f"N{value:.1f}\n".encode())
                    if args.verbose:
                        print(f"CPU {cpu:5.1f}%  RAM {ram:5.1f}%")
                    if now >= next_net:
                        net.read()
                        next_net = now + NET_INTERVAL
                    if now >= next_text:
                        rows = list(lcd_rows(mode, cpu, ram, top_cpu, top_ram, net,
                                             hot_cpu and fresh_cpu, hot_ram))
                        if now < notice_until:
                            rows[0] = f"Needle: {NAMES[mode]}"
                        for row, text in enumerate(rows):
                            if text != sent_rows[row]:
                                # A row still queued is out of date: replace it
                                sent_rows[row] = text
                                queue = deque(l for l in queue if not l.startswith(f"T{row}"))
                                queue.append(text_line(row, text))
                        next_text = now + TEXT_INTERVAL
                if queue:
                    s.write(queue.popleft().encode())
                time.sleep(INTERVAL)
            except serial.SerialException as e:
                print(f"{e} - retrying...")
                if s is not None:
                    s.close()
                    s = None
                time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        if s is not None:
            print("Parking the needle...")
            try:
                answer = park(s)
                print({b"PARKED": "Parked.",
                       b"POMODORO ON": "Pomodoro running: the needle stays with it."}
                      .get(answer, "No confirmation from the Arduino."))
            except serial.SerialException:
                pass
            s.close()

if __name__ == "__main__":
    main()
