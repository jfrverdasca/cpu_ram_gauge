#!/usr/bin/env python3
"""Send CPU and RAM usage to the Arduino needle gauge over serial.

Each reading is "C45.3 R72.1". The LCD text is composed here and sent as
"T<row><needle><text>": row 0 or 1, for when the needle shows CPU (C) or RAM (R).
Custom LCD characters are sent as "G<slot><8 rows in hex>", the backlight as
"B<0-255>". Change lcd_rows() and GLYPHS to show something else. The time for the
Arduino's clock module goes as "Z<UTC seconds> <standard offset, minutes> <0/1>".

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

BAUD = 115200
INTERVAL = 0.05    # seconds between readings
ALPHA = 0.04       # CPU smoothing: s = s*0.96 + new*0.04 (~1.2 s time constant)
RESET_WAIT = 2.5   # the Arduino resets when the port opens and may home
APPS_INTERVAL = 5     # seconds between top app scans (a scan costs ~15 ms of CPU)
TEXT_INTERVAL = 0.25  # seconds between LCD text updates
NET_INTERVAL = 1      # seconds between network speed readings (~70 us each)
SCROLL_STEP = 0.5     # seconds per character when an app name doesn't fit (2 text updates)
SCROLL_PAUSE = 2      # seconds still at each end
LCD_WIDTH = 16
HOT_ON = 60           # needle metric %, from which row 1 shows the app using the most
HOT_OFF = 50          # ... until it drops below this, so the row doesn't flicker
BRIGHTNESS = 128      # LCD backlight, 1-255

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
]
CPU_ICON = chr(8)  # or "C" for a plain letter
RAM_ICON = chr(1)  # or "R"
UPTIME_ICON = chr(2)
DOWN_ICON = chr(3)
UP_ICON = chr(4)

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
    time.sleep(RESET_WAIT)
    s.reset_input_buffer()
    print(f"Connected to {port}")
    return s


def park(s):
    s.reset_input_buffer()
    s.write(b"\nP\n")  # the newline ends a line cut short by Ctrl+C
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if s.readline().strip() == b"PARKED":
            return True
    return False


_denied = set()  # system processes need root: skip them on later scans
_cores = psutil.cpu_count() or 1  # None when it can't tell
_ram_total = psutil.virtual_memory().total


def top_apps():
    """(name, % of the total) of the apps using the most CPU and RAM, leaving out
    this script. Helper processes ("Google Chrome Helper (Renderer)") count towards
    their app."""
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
                name, c, m = p.name(), p.cpu_percent(), p.memory_info().rss
        except psutil.AccessDenied:
            denied.add(p)
            continue
        except psutil.NoSuchProcess:
            continue
        if not name:
            continue
        name = name.split(" Helper")[0]
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


_hot = {"C": False, "R": False}  # needle metric above HOT_ON, until below HOT_OFF


def hot(needle, value):
    _hot[needle] = value >= (HOT_OFF if _hot[needle] else HOT_ON)
    return _hot[needle]


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


def lcd_rows(cpu, ram, top_cpu, top_ram, net):
    """LCD text by "<row><needle>". The first row shows the metric the needle isn't;
    the second, uptime and network, or the app behind the needle and its own share
    while the needle is high. top_cpu and top_ram are (name, % of the total)."""
    # "⏻ 03:26h   ↓1.2M": the faster of download/upload right-aligned
    left = f"{UPTIME_ICON} {uptime()}"
    bottom = left + net.text().rjust(LCD_WIDTH - len(left))
    # An app's share can't pass the total: summed RSS counts shared pages once per
    # process, and the share is from the last scan, up to APPS_INTERVAL ago
    top_cpu = top_cpu[0], min(top_cpu[1], cpu)
    top_ram = top_ram[0], min(top_ram[1], ram)
    return {
        "0C": metric_row(RAM_ICON, ram, top_ram[0]),
        "0R": metric_row(CPU_ICON, cpu, top_cpu[0]),
        "1C": metric_row(CPU_ICON, top_cpu[1], top_cpu[0]) if hot("C", cpu) and top_cpu[0] else bottom,
        "1R": metric_row(RAM_ICON, top_ram[1], top_ram[0]) if hot("R", ram) and top_ram[0] else bottom,
    }


def glyph_lines():
    for slot, rows in enumerate(GLYPHS):
        bits = "".join(f"{int(row.replace('#', '1').replace('.', '0'), 2):02X}" for row in rows)
        yield f"G{slot}{bits}\n"


def clock_line():
    """UTC time and the local zone, for the clock shown with no data. The Arduino
    applies summer time with the EU rule when the zone has it."""
    return f"Z{int(time.time())} {-time.timezone // 60} {time.daylight}\n"


def text_lines(rows):
    # ASCII only: the LCD has no accents
    for key, text in rows.items():
        yield f"T{key}{text.encode('ascii', 'replace').decode()[:LCD_WIDTH]}\n"


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
    next_apps = next_text = next_net = 0
    awake = True
    top_cpu = top_ram = ("", 0)  # if it starts with the screen asleep
    net = Network()
    # Lines other than readings, sent one per reading: the Arduino's serial buffer
    # holds 64 bytes and it stops reading for ~10 ms while it redraws the LCD
    queue = deque()

    s = None
    try:
        while True:
            try:
                if s is None:
                    port = args.port or find_port()
                    if not port:
                        raise serial.SerialException("Arduino not found")
                    s = connect(port)
                    next_apps = next_text = 0
                    queue.clear()

                cpu = cpu * (1 - ALPHA) + psutil.cpu_percent() * ALPHA
                ram = psutil.virtual_memory().percent

                # Every few seconds, also resent in case the Arduino missed them
                now = time.monotonic()
                if now >= next_apps:
                    woke = False
                    if display_on() != awake:
                        awake = woke = not awake
                        print("Screen on." if awake else "Screen asleep: parking.")
                        if not awake:
                            net.last = None  # the next reading would span the sleep
                    queue.append(f"B{BRIGHTNESS if awake else 0}\n")
                    queue.append(clock_line())
                    if awake:
                        app_cpu, top_ram = top_apps()
                        # Just after waking, each app's CPU spans the sleep: keep the
                        # old name and scan again in a second
                        if not woke:
                            top_cpu = app_cpu
                        net.find_interface()  # Wi-Fi and cable may swap
                        queue.extend(glyph_lines())
                        if args.verbose:
                            print(f"Top CPU {top_cpu[0]} {top_cpu[1]:.0f}%  "
                                  f"Top RAM {top_ram[0]} {top_ram[1]:.0f}%")
                    else:
                        queue.append("P\n")
                    next_apps = now + (1 if woke else APPS_INTERVAL)

                if awake:
                    s.write(f"C{cpu:.1f} R{ram:.1f}\n".encode())
                    if args.verbose:
                        print(f"CPU {cpu:5.1f}%  RAM {ram:5.1f}%")
                    if now >= next_net:
                        net.read()
                        next_net = now + NET_INTERVAL
                    if now >= next_text:
                        # Text still queued is out of date: drop it, or a slow loop
                        # lets the queue grow forever
                        queue = deque(line for line in queue if line[0] != "T")
                        queue.extend(text_lines(lcd_rows(cpu, ram, top_cpu, top_ram, net)))
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
                print("Parked." if park(s) else "No confirmation from the Arduino.")
            except serial.SerialException:
                pass
            s.close()


if __name__ == "__main__":
    main()
