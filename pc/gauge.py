#!/usr/bin/env python3
"""Drive the Arduino needle gauge over serial: what the needle and the LCD show comes
from the plugins in PLUGINS (pc/plugins/), CPU or RAM usage by default.

The Arduino is only the physical part; this script decides what it shows. The needle
goes as "N45.3" every reading (also the sign the script is alive), the LCD rows as
"T<row><text>" when they change, with the custom characters they use as "G<slot><8
rows in hex>". The backlight goes as "B<0-255>", the Pomodoro minutes as "M<focus>
<break> <long break> <cycles>", the piezo as "S<Hz> <volume>" and the time for the
Arduino's clock module as "Z<UTC seconds> <standard offset, minutes> <0/1>": on
connecting and whenever the Arduino says READY (it restarted). A click on the
Arduino's button ("CLICK") switches the needle to the next of the plugins' needles.
While the Arduino runs the Pomodoro ("POMODORO ON") only a slow needle goes, so it
doesn't park when the Pomodoro ends; the plugins rest.

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
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import serial
from serial.tools import list_ports

import lcd
from plugins import Context
from plugins.email import Email
from plugins.status import Status
from plugins.system import System

# ---------------------------------------------------------------- settings
# Safe to change.

# What the needle and the LCD show. A click moves the needle to the next of the
# plugins' needles, in this order (System: CPU, then RAM). Each LCD row shows the
# first plugin here with something for it. Each plugin's options are in its file.
PLUGINS = [
    Email(rows=("{icon} {sender}", "{subject}")),  # or rows=(None, "{sender} - {subject}")
    System(),
    Status(),
]
BRIGHTNESS = 128      # LCD backlight, 1-255
NOTICE = 3            # seconds "Needle: RAM" stays on row 0 after a click

# Pomodoro, run by the Arduino (also without this script); saved there
POMODORO_FOCUS = 25       # minutes, 1-99
POMODORO_BREAK = 5        # minutes, 1-99
POMODORO_LONG_BREAK = 15  # minutes, 1-99
POMODORO_CYCLES = 4       # focus sessions before the long break, 1-9
BEEP_HZ = 2000            # piezo tone, 1000-5000
BEEP_VOLUME = 50          # 0 (silent) - 100

# ---------------------------------------------------------------- internals

PROTOCOL = b"2"       # what the firmware must say in READY
BAUD = 115200
INTERVAL = 0.05       # seconds between readings
# ... while the Arduino runs the Pomodoro: it ignores the needle then, which only
# keeps it from parking when the Pomodoro ends. No plugins or text meanwhile
POMODORO_INTERVAL = 0.25
READY_WAIT = 10       # the Arduino resets when the port opens and may home
CHECK_INTERVAL = 5    # seconds between screen checks
CLOCK_INTERVAL = 3600 # seconds between clock syncs
TEXT_INTERVAL = 0.25  # seconds between LCD text updates
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"  # settings kept out of git

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


def clock_line():
    """UTC time and the local zone, for the clock shown with no data. The Arduino
    applies summer time with the EU rule when the zone has it."""
    return f"Z{int(time.time())} {-time.timezone // 60} {time.daylight}\n"


def text_line(row, text):
    return f"T{row}{text[:lcd.WIDTH]}\n"


def setup_lines(awake):
    """Everything the Arduino keeps, but the custom characters (they go with the
    rows): sent on connecting and when it restarts."""
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


def load_env(path=ENV_FILE):
    """KEY=value lines from path into the environment; variables already set win.
    A value may be quoted; unquoted, " #" starts a comment."""
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        key, sep, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        if not sep or not key or key.startswith("#"):
            continue
        value = value.strip()
        end = value.find(value[:1], 1) if value[:1] in ("\"", "'") else -1
        value = value[1:end] if end > 0 else value.partition(" #")[0].rstrip()
        os.environ.setdefault(key, value)


def lcd_rows(plugins, ctx):
    """Each row from the first plugin with something for it; empty if none. Plugins
    after both rows are taken aren't asked, so what they'd show waits."""
    rows = [None, None]
    for p in plugins:
        rows = [mine if row is None else row for row, mine in zip(rows, p.rows(ctx))]
        if None not in rows:
            break
    return [lcd.ascii(row or "") for row in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", help="serial port (default: auto-detect)")
    parser.add_argument("-v", "--verbose", action="store_true", help="print the values sent")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, on_signal)
    # no SIGHUP on Windows; keep nohup working elsewhere
    if hasattr(signal, "SIGHUP") and signal.getsignal(signal.SIGHUP) != signal.SIG_IGN:
        signal.signal(signal.SIGHUP, on_signal)

    load_env()
    needles = {name: value for p in PLUGINS for name, value in p.needles.items()}
    if not needles:
        sys.exit("No plugin in PLUGINS has a needle")
    names = list(needles)
    mode = names[0]
    for p in PLUGINS:
        p.start()
    time.sleep(INTERVAL)  # psutil's first readings are always 0

    notice_until = 0      # "Needle: ..." on row 0 until then
    next_check = next_text = next_clock = 0
    due = dict.fromkeys(PLUGINS, 0)  # next update() of each plugin
    awake = True
    pomodoro = False      # the Arduino runs it: only a slow needle
    # Lines other than readings, sent one per reading: the Arduino's serial buffer
    # holds 64 bytes and it stops reading for ~10 ms while it redraws the LCD
    queue = deque()
    sent_rows = [None, None]  # LCD rows as last queued
    glyphs = lcd.Glyphs()
    rx = b""

    def resume(now):
        # After a pause (screen asleep, Pomodoro) the plugins' figures would span it
        for p in PLUGINS:
            p.resume()
            due[p] = now

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
                    restarted = True  # connect() read the READY

                lines, rx = read_lines(s, rx)
                for line in lines:
                    if line.startswith(b"READY"):
                        check_protocol(s, line)
                        print("The Arduino restarted.")
                        queue.clear()
                        restarted = True
                    elif line == b"CLICK" and awake:
                        mode = names[(names.index(mode) + 1) % len(names)]
                        notice_until = now + NOTICE
                        next_text = now  # the new text at once
                        if args.verbose:
                            print(f"Needle: {mode}")
                    elif line in (b"POMODORO ON", b"POMODORO OFF"):
                        print(line.decode().capitalize())
                        pomodoro = line == b"POMODORO ON"
                        if not pomodoro:
                            # Back at once: the LCD still has the text from before
                            sent_rows = [None, None]
                            next_text = now
                            resume(now)
                    elif line == b"TEST":
                        print("Test")
                if restarted:
                    if pomodoro:  # a reset ends it, as POMODORO OFF would
                        pomodoro = False
                        resume(now)
                    queue.extend(setup_lines(awake))
                    sent_rows = [None, None]
                    glyphs.reset()  # the LCD lost them
                    next_check = next_text = 0
                    next_clock = now + CLOCK_INTERVAL

                if now >= next_check:
                    if display_on() != awake:
                        awake = not awake
                        print("Screen on." if awake else "Screen asleep: parking.")
                        queue.append(f"B{BRIGHTNESS}\n" if awake else "B0\nP\n")
                        if awake:
                            resume(now)
                    elif not awake:
                        queue.append("B0\n")  # still asleep: the Arduino forgets B0 after 15 s
                    next_check = now + CHECK_INTERVAL
                if now >= next_clock:
                    queue.append(clock_line())
                    next_clock = now + CLOCK_INTERVAL

                ctx = Context(now, mode, args.verbose)
                if awake and not pomodoro:
                    for p in PLUGINS:
                        if now >= due[p]:
                            p.update(ctx)
                            due[p] = now + p.interval

                if awake:
                    s.write(f"N{needles[mode]():.1f}\n".encode())
                    if args.verbose:
                        print("  ".join(f"{name} {value():5.1f}%" for name, value in needles.items()))
                    if not pomodoro and now >= next_text:
                        rows = lcd_rows(PLUGINS, ctx)
                        if now < notice_until:
                            rows[0] = f"Needle: {mode}"
                        if rows != sent_rows:
                            # The custom characters first, in slots not on the LCD now
                            # if it can: a slot rewritten changes on the LCD at once
                            loads, lcd_text = glyphs.place(rows, sent_rows)
                            queue.extend(loads)
                            for row, text in enumerate(rows):
                                if text != sent_rows[row]:
                                    # A row still queued is out of date: replace it
                                    sent_rows[row] = text
                                    queue = deque(l for l in queue if not l.startswith(f"T{row}"))
                                    queue.append(text_line(row, lcd_text[row]))
                        next_text = now + TEXT_INTERVAL
                if queue:
                    s.write(queue.popleft().encode())
                time.sleep(POMODORO_INTERVAL if pomodoro else INTERVAL)
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
