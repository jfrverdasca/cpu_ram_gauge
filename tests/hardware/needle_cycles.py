"""Checks on the real gauge that the needle loses no steps: it climbs and falls the
way the smoothed CPU does, many times, then goes to 0%. If it then sits exactly on
the zero mark, no step was lost (the motor has no sensor: the needle is the only
witness). It holds 0% for 10 s, then parks. Stop gauge.py first: the port is
exclusive.

    python3 tests/hardware/needle_cycles.py [cycles]
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pc"))
import gauge  # noqa: E402

ALPHA = 0.04        # as the System plugin's CPU smoothing
LOW, HIGH = 5, 95   # % the "CPU" jumps between
SECONDS = 4         # per climb or fall


def send(s, value):
    s.write(f"N{value:.1f}\n".encode())
    time.sleep(gauge.INTERVAL)


def main():
    cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    port = gauge.find_port()
    if not port:
        sys.exit("Arduino not found")
    s = gauge.connect(port)
    value = LOW
    for _ in range(int(3 / gauge.INTERVAL)):  # from wherever it was to LOW
        send(s, value)
    for n in range(cycles):
        for goal in (HIGH, LOW):
            for _ in range(int(SECONDS / gauge.INTERVAL)):
                value = value * (1 - ALPHA) + goal * ALPHA
                send(s, value)
        print(f"Cycle {n + 1}/{cycles}")
    print("The needle should sit exactly on 0% now (10 s)")
    for _ in range(int(10 / gauge.INTERVAL)):
        send(s, 0)
    gauge.park(s)
    s.close()


if __name__ == "__main__":
    main()
