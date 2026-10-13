"""pc/gauge.py against a fake serial port. Run with tests/run.sh."""

import io
import sys
import time
import unittest
from collections import namedtuple
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pc"))
import gauge  # noqa: E402

CPU, RAM = 20.0, 60.0


class FakeSerial:
    """Answers like the Arduino. events: (seconds after opening, line) to receive."""

    def __init__(self, ready=b"READY 2", events=(), park_answer=b"PARKED", duration=1.0):
        self.start = time.monotonic()
        self.boot = [ready + b"\n"] if ready else []
        self.events = sorted(events)
        self.park_answer = park_answer
        self.duration = duration
        self.written = []

    def elapsed(self):
        return time.monotonic() - self.start

    @property
    def in_waiting(self):
        if self.elapsed() > self.duration:
            raise KeyboardInterrupt  # ends main(), which then parks
        return sum(len(line) for t, line in self.events if t <= self.elapsed())

    def read(self, n):
        due = [line for t, line in self.events if t <= self.elapsed()]
        self.events = [(t, line) for t, line in self.events if t > self.elapsed()]
        return b"".join(due)

    def readline(self):
        if self.boot:
            return self.boot.pop(0)
        if self.written and self.written[-1].endswith(b"P\n"):
            return self.park_answer + b"\n"
        time.sleep(0.01)
        return b""

    def write(self, data):
        self.written.append(data)

    def reset_input_buffer(self):
        pass

    def close(self):
        pass

    def lines(self):
        return b"".join(self.written).decode().splitlines()


def run_main(port, display_on=True, **patches):
    """Runs gauge.main() on port with fixed readings; returns what it printed."""
    out = io.StringIO()
    memory = namedtuple("Memory", "percent")(RAM)
    with mock.patch.object(gauge.serial, "Serial", lambda *a, **k: port), \
            mock.patch.object(gauge, "find_port", lambda: "fake"), \
            mock.patch.object(gauge.psutil, "cpu_percent", lambda *a, **k: CPU), \
            mock.patch.object(gauge.psutil, "virtual_memory", lambda: memory), \
            mock.patch.object(gauge, "top_apps", lambda: (("Firefox", 5.0), ("Firefox", 10.0))), \
            mock.patch.object(gauge, "display_on", lambda: display_on), \
            mock.patch.object(gauge.Network, "find_interface", lambda self: None), \
            mock.patch.object(gauge.Network, "read", lambda self: None), \
            mock.patch.multiple(gauge, **{"NOTICE": gauge.NOTICE, **patches}), \
            mock.patch.object(sys, "argv", ["gauge.py"]), \
            redirect_stdout(out):
        gauge.main()
    return out.getvalue()


class Helpers(unittest.TestCase):
    def test_read_lines_keeps_a_partial_line(self):
        port = FakeSerial(events=[(0, b"CLI")])
        lines, rest = gauge.read_lines(port, b"")
        self.assertEqual((lines, rest), ([], b"CLI"))
        port.events = [(0, b"CK\nTEST\nPOMO")]
        lines, rest = gauge.read_lines(port, rest)
        self.assertEqual((lines, rest), ([b"CLICK", b"TEST"], b"POMO"))

    def test_protocol_must_match(self):
        gauge.check_protocol(FakeSerial(), b"READY 2")
        for ready in (b"READY", b"READY 3"):  # an old firmware says only READY
            with self.assertRaises(SystemExit):
                gauge.check_protocol(FakeSerial(), ready)

    def test_setup_lines(self):
        lines = list(gauge.setup_lines(True))
        self.assertEqual(sum(line.startswith("G") for line in lines), len(gauge.GLYPHS))
        self.assertIn(f"M{gauge.POMODORO_FOCUS} {gauge.POMODORO_BREAK} "
                      f"{gauge.POMODORO_LONG_BREAK} {gauge.POMODORO_CYCLES}\n", lines)
        self.assertIn(f"S{gauge.BEEP_HZ} {gauge.BEEP_VOLUME}\n", lines)
        self.assertIn(f"B{gauge.BRIGHTNESS}\n", lines)
        self.assertTrue(any(line.startswith("Z") for line in lines))
        self.assertIn("B0\n", list(gauge.setup_lines(False)))

    def test_rows_by_mode(self):
        net = gauge.Network()
        args = (CPU, RAM, ("Code", 5.0), ("Firefox", 10.0), net, False, False)
        cpu_rows = gauge.lcd_rows("C", *args)
        ram_rows = gauge.lcd_rows("R", *args)
        self.assertTrue(cpu_rows[0].startswith(f"{gauge.RAM_ICON} 60% Firefox"))
        self.assertTrue(ram_rows[0].startswith(f"{gauge.CPU_ICON} 20% Code"))
        # Past HOT, row 1 shows the app behind the needle
        hot = gauge.lcd_rows("C", CPU, RAM, ("Code", 5.0), ("Firefox", 10.0), net, True, False)
        self.assertEqual(hot[1], f"{gauge.APP_ICON} 5% Code")


class Main(unittest.TestCase):
    def test_sends_the_settings_then_the_needle(self):
        port = FakeSerial(duration=1.0)
        run_main(port)
        lines = port.lines()
        self.assertEqual(lines[0], f"N{CPU:.1f}")  # the needle at once
        settings = [line for line in lines if line[:1] in ("G", "M", "S", "B", "Z")]  # one per reading
        self.assertEqual(len(settings), len(gauge.GLYPHS) + 4)  # once
        self.assertTrue(any(line.startswith("T0") for line in lines))
        self.assertTrue(any(line.startswith("T1") for line in lines))

    def test_click_switches_the_needle_and_shows_it(self):
        port = FakeSerial(events=[(0.5, b"CLICK\n")], duration=1.2)
        run_main(port)
        lines = port.lines()
        needles = [line for line in lines if line.startswith("N")]
        self.assertEqual(needles[0], f"N{CPU:.1f}")
        self.assertEqual(needles[-1], f"N{RAM:.1f}")
        self.assertIn("T0Needle: RAM", lines)

    def test_notice_goes_after_a_while(self):
        port = FakeSerial(events=[(0.2, b"CLICK\n")], duration=1.2)
        run_main(port, NOTICE=0.5)
        rows = [line for line in port.lines() if line.startswith("T0")]
        self.assertEqual(rows[-2], "T0Needle: RAM")
        self.assertTrue(rows[-1].startswith(f"T0{gauge.CPU_ICON}"))  # RAM on the needle, CPU on the LCD

    def test_rows_go_only_when_they_change(self):
        port = FakeSerial(duration=1.0)
        run_main(port)
        lines = port.lines()
        self.assertEqual(sum(line.startswith("T0") for line in lines), 1)
        self.assertEqual(sum(line.startswith("T1") for line in lines), 1)
        self.assertGreater(sum(line.startswith("N") for line in lines), 10)

    def test_ready_resends_everything(self):
        # After the first rows have gone (12 lines queued, one per reading)
        port = FakeSerial(events=[(1.0, b"READY 2\n")], duration=2.5)
        output = run_main(port)
        lines = port.lines()
        self.assertIn("The Arduino restarted.", output)
        self.assertEqual(sum(line.startswith("M") for line in lines), 2)
        self.assertEqual(sum(line.startswith("G0") for line in lines), 2)
        self.assertEqual(sum(line.startswith("T0") for line in lines), 2)

    def test_old_firmware_stops_the_script(self):
        port = FakeSerial(ready=b"READY")
        with self.assertRaises(SystemExit):
            run_main(port)

    def test_screen_asleep_parks_and_keeps_b0_coming(self):
        port = FakeSerial(events=[(0.3, b"CLICK\n")], duration=1.0)
        output = run_main(port, display_on=False, CHECK_INTERVAL=0.2)
        lines = port.lines()
        self.assertIn("Screen asleep: parking.", output)
        self.assertFalse(any(line.startswith("N") for line in lines))
        self.assertGreaterEqual(lines.count("B0"), 3)
        self.assertEqual(lines.count("P"), 2)  # on falling asleep, and on exit
        self.assertNotIn("T0Needle: RAM", lines)  # the click is ignored

    def test_pomodoro_events_are_logged(self):
        port = FakeSerial(events=[(0.2, b"POMODORO ON\n"), (0.4, b"TEST\n")], duration=0.6)
        output = run_main(port)
        self.assertIn("Pomodoro on", output)
        self.assertIn("Test", output)

    def test_exit_parks(self):
        self.assertIn("Parked.", run_main(FakeSerial(duration=0.3)))
        output = run_main(FakeSerial(duration=0.3, park_answer=b"POMODORO ON"))
        self.assertIn("Pomodoro running: the needle stays with it.", output)


if __name__ == "__main__":
    unittest.main()
