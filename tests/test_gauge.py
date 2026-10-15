"""pc/gauge.py against a fake serial port. Run with tests/run.sh."""

import io
import random
import sys
import time
import unittest
from collections import namedtuple
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pc"))
import gauge  # noqa: E402
import lcd  # noqa: E402
from plugins import Context, Plugin  # noqa: E402
from plugins import email as email_plugin, status, system  # noqa: E402

CPU, RAM = 20.0, 60.0


class FakeSerial:
    """Answers like the Arduino. events: (seconds after opening, line) to receive."""

    def __init__(self, ready=b"READY 2", events=(), park_answer=b"PARKED", duration=1.0,
                 drop_at=None):
        self.start = time.monotonic()
        self.ready = ready
        self.boot = [ready + b"\n"] if ready else []
        self.drop_at = drop_at  # the USB drops then; the Arduino restarts on reconnecting
        self.events = sorted(events)
        self.park_answer = park_answer
        self.duration = duration
        self.written = []  # (seconds after opening, bytes)

    def elapsed(self):
        return time.monotonic() - self.start

    @property
    def in_waiting(self):
        if self.elapsed() > self.duration:
            raise KeyboardInterrupt  # ends main(), which then parks
        if self.drop_at is not None and self.elapsed() > self.drop_at:
            self.drop_at = None
            self.boot = [self.ready + b"\n"]
            raise gauge.serial.SerialException("device disconnected")
        return sum(len(line) for t, line in self.events if t <= self.elapsed())

    def read(self, n):
        due = [line for t, line in self.events if t <= self.elapsed()]
        self.events = [(t, line) for t, line in self.events if t > self.elapsed()]
        return b"".join(due)

    def readline(self):
        if self.boot:
            return self.boot.pop(0)
        if self.written and self.written[-1][1].endswith(b"P\n"):
            return self.park_answer + b"\n"
        time.sleep(0.01)
        return b""

    def write(self, data):
        self.written.append((self.elapsed(), data))

    def reset_input_buffer(self):
        pass

    def close(self):
        pass

    def lines(self, start=0, end=float("inf")):
        """Lines written between start and end seconds after opening."""
        data = b"".join(d for t, d in self.written if start <= t < end)
        return data.decode().splitlines()


def run_main(port, display_on=True, scans=None, plugins=None, **patches):
    """Runs gauge.main() on port with fixed readings; returns what it printed.
    scans: a list that gets the time of every top apps scan. plugins: PLUGINS (by
    default a fresh System and Status)."""
    def top_apps():
        if scans is not None:
            scans.append(port.elapsed())
        return ("Firefox", 5.0), ("Firefox", 10.0)

    out = io.StringIO()
    memory = namedtuple("Memory", "percent")(RAM)
    with mock.patch.object(gauge.serial, "Serial", lambda *a, **k: port), \
            mock.patch.object(gauge, "find_port", lambda: "fake"), \
            mock.patch.object(system.psutil, "cpu_percent", lambda *a, **k: CPU), \
            mock.patch.object(system.psutil, "virtual_memory", lambda: memory), \
            mock.patch.object(system, "top_apps", top_apps), \
            mock.patch.object(gauge, "display_on", lambda: display_on), \
            mock.patch.object(gauge, "load_env", lambda: None), \
            mock.patch.object(status.Network, "find_interface", lambda self: None), \
            mock.patch.object(status.Network, "read", lambda self: None), \
            mock.patch.multiple(gauge, **{"NOTICE": gauge.NOTICE, **patches,
                                          "PLUGINS": plugins or [system.System(), status.Status()]}), \
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
        self.assertFalse(any(line.startswith("G") for line in lines))  # they go with the rows
        self.assertIn(f"M{gauge.POMODORO_FOCUS} {gauge.POMODORO_BREAK} "
                      f"{gauge.POMODORO_LONG_BREAK} {gauge.POMODORO_CYCLES}\n", lines)
        self.assertIn(f"S{gauge.BEEP_HZ} {gauge.BEEP_VOLUME}\n", lines)
        self.assertIn(f"B{gauge.BRIGHTNESS}\n", lines)
        self.assertTrue(any(line.startswith("Z") for line in lines))
        self.assertIn("B0\n", list(gauge.setup_lines(False)))

    def test_system_rows_by_needle(self):
        p = system.System()
        p.cpu, p.ram, p.top_cpu, p.top_ram = CPU, RAM, ("Code", 5.0), ("Firefox", 10.0)
        cpu_rows = p.rows(Context(0, "CPU", False))
        self.assertEqual(cpu_rows, (f"{system.RAM_ICON} 60% Firefox", None))
        self.assertEqual(p.rows(Context(0, "RAM", False))[0], f"{system.CPU_ICON} 20% Code")
        # Past hot, row 1 shows the app behind the needle
        p.hot_cpu = p.fresh_cpu = True
        self.assertEqual(p.rows(Context(0, "CPU", False))[1], f"{system.APP_ICON} 5% Code")

    def test_app_names_fit_after_dropping_accents(self):
        cafe = "Cafe\u0301 Cafe\u0301 Cafe\u0301"  # decomposed, as in macOS paths
        self.assertEqual(system.metric_row("C", 5, cafe), "C 5% Cafe Cafe C")
        self.assertEqual(system.metric_row("C", 5, "Word\u2122"), "C 5% WordTM")

    def test_needles_of_the_base_class_are_read_only(self):
        with self.assertRaises(TypeError):
            Plugin().needles["X"] = lambda: 0

    def test_load_env(self):
        with mock.patch.dict(gauge.os.environ, {"B": "set"}, clear=True):
            path = mock.Mock(read_text=lambda: '# comment\nA = "one # two" # note\nexport B=two\nC\n'
                                               "D=pass#word # note\nE='x'")
            gauge.load_env(path)
            self.assertEqual(gauge.os.environ, {"A": "one # two", "B": "set", "D": "pass#word",
                                                "E": "x"})  # set ones win


class Rows(unittest.TestCase):
    class Fixed(Plugin):
        def __init__(self, rows):
            self.fixed = rows
            self.asked = 0

        def rows(self, ctx):
            self.asked += 1
            return self.fixed

    def test_first_plugin_with_text_wins_each_row(self):
        top = self.Fixed((None, "mail"))
        rest = self.Fixed(("cpu", "net"))
        last = self.Fixed(("x", "y"))
        rows = gauge.lcd_rows([top, rest, last], Context(0, "CPU", False))
        self.assertEqual(rows, ["cpu", "mail"])
        self.assertEqual(last.asked, 0)  # both rows taken: not asked
        self.assertEqual(gauge.lcd_rows([top], None), ["", "mail"])

    def test_rows_stay_as_plugins_fit_them(self):
        # Converting after fitting would make "TM" of it and push the row past 16
        self.assertEqual(gauge.lcd_rows([self.Fixed(("a\u2122", None))], None), ["a\u2122", ""])
        self.assertEqual(gauge.text_line(0, "Needle: N\u00ba \u2122"), "T0Needle: N? ?\n")

    def test_lcd_text_has_no_accents(self):
        self.assertEqual(lcd.ascii("João Ação ✓"), "Joao Acao ?")
        icon = system.CPU_ICON
        self.assertEqual(lcd.ascii(icon + " é"), icon + " e")

    def test_fit_keeps_the_start_still(self):
        text = "> " + "abcdefghijklmnopqrstuvwxyz"
        start = time.monotonic()
        self.assertEqual(lcd.fit(text, 2, since=start), "> abcdefghijklmn")
        later = start - lcd.SCROLL_PAUSE - 3 * lcd.SCROLL_STEP  # 3 steps in
        self.assertEqual(lcd.fit(text, 2, since=later), "> defghijklmnopq")
        self.assertEqual(lcd.scroll_time("a" * 20, 16), 2 * lcd.SCROLL_PAUSE + 4 * lcd.SCROLL_STEP)
        self.assertEqual(lcd.scroll_time("short"), 0)

    def test_next_update_is_on_the_grid(self):
        self.assertEqual(lcd.next_update(1.30), 1.5)
        self.assertEqual(lcd.next_update(1.5), 1.75)

    def test_scroll_steps_are_even(self):
        # Updates as the loop runs them: up to 60 ms late in their slot
        text = "".join(chr(65 + n % 26) for n in range(60))
        jitter = random.Random(1)
        now = since = 1000.13  # off the grid, as an email shown at ctx.now
        shown, steps = None, []
        while now < since + lcd.scroll_time(text) - 1:  # before it wraps to the start
            with mock.patch.object(lcd.time, "monotonic", lambda: now):
                row = lcd.scroll(text, since=since)
            if row != shown and shown is not None:
                steps.append(now)
            shown = row
            now = lcd.next_update(now) + jitter.uniform(0, 0.06)
        gaps = [b - a for a, b in zip(steps, steps[1:])]
        self.assertEqual(len(gaps), len(text) - lcd.WIDTH - 1)
        for gap in gaps:
            self.assertAlmostEqual(gap, lcd.SCROLL_STEP, delta=0.1)


class Glyphs(unittest.TestCase):
    def setUp(self):
        self.icons = [lcd.glyph([f"{n:05b}".replace("0", ".").replace("1", "#")] * 8)
                      for n in range(10)]

    def test_loads_once_and_uses_the_slot_character(self):
        g = lcd.Glyphs()
        a, b = self.icons[:2]
        lines, rows = g.place([a + " A", b + " B"], [None, None])
        self.assertEqual([l[:2] for l in lines], ["G0", "G1"])
        self.assertEqual(rows, [chr(8) + " A", chr(1) + " B"])  # slot 0 is chr(8)
        lines, rows = g.place([b + " B", a + " A"], rows)
        self.assertEqual(lines, [])
        self.assertEqual(rows, [chr(1) + " B", chr(8) + " A"])

    def test_keeps_slots_on_the_lcd_while_others_are_free(self):
        g = lcd.Glyphs()
        g.slots = self.icons[:8]
        shown = [self.icons[0], self.icons[1]]
        # The new rows need neither 0 nor 1: 2 is the first slot off the LCD
        lines, _ = g.place([self.icons[8], ""], shown)
        self.assertEqual(lines[0][:2], "G2")
        # With every slot on the LCD, one the new rows don't need
        lines, _ = g.place([self.icons[9], self.icons[8]], ["".join(self.icons[:8]), ""])
        self.assertEqual([l[:2] for l in lines], ["G0"])

    def test_past_8_the_rest_are_spaces(self):
        g = lcd.Glyphs()
        lines, rows = g.place(["".join(self.icons[:9]), ""], [None, None])
        self.assertEqual(len(lines), 8)
        self.assertEqual(rows[0][-1], " ")
        g.reset()
        self.assertEqual(g.slots, [None] * 8)


class FakeIMAP:
    """An IMAP server with one mailbox: mail is {uid: [sender, subject, seen]}."""

    def __init__(self, mail):
        self.mail = mail
        self.searches = 0
        self.no_uid = set()  # UIDs whose FETCH answer doesn't say which UID
        self.validity = 7
        self.folders = []  # as sent

    def __call__(self, host, timeout=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def login(self, user, password):
        if password != "secret":
            raise email_plugin.imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def status(self, folder, items):
        self.folders.append(folder)
        if folder not in ('"INBOX"', '"[Gmail]/All Mail"'):
            return "NO", [b"[NONEXISTENT] Unknown Mailbox"]
        return "OK", [f"{folder} (UIDNEXT {max(self.mail, default=0) + 1} "
                      f"UIDVALIDITY {self.validity})".encode()]

    def select(self, folder, readonly=False):
        assert readonly  # never marks anything read

    def uid(self, command, *args):
        if command == "SEARCH":
            self.searches += 1
            first = int(args[1].split()[-1].split(":")[0])
            uids = [u for u in self.mail if u >= first] or [max(self.mail)]  # like n:*
            return "OK", [" ".join(str(u) for u in uids if not self.mail[u][2]).encode()]
        assert "PEEK" in args[1]
        data = []
        for u in map(int, args[0].split(",")):
            sender, subject = self.mail[u][:2]
            data += [(f"{u} ({'' if u in self.no_uid else f'UID {u} '}BODY[HEADER] {{99}}".encode(),
                      f"From: {sender}\r\nSubject: {subject}\r\n\r\n".encode()), b")"]
        return "OK", data


class EmailPlugin(unittest.TestCase):
    def setUp(self):
        self.mail = {1: ["Old <old@x.pt>", "Before the start", False]}
        self.imap = FakeIMAP(self.mail)
        patches = [mock.patch.object(email_plugin.imaplib, "IMAP4_SSL", self.imap),
                   mock.patch.dict(email_plugin.os.environ, {
                       "EMAIL_HOST": "imap.x.pt", "EMAIL_USER": "me", "EMAIL_PASSWORD": "secret"})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.now = time.monotonic()

    def plugin(self, **options):
        options.setdefault("rows", ("{icon} {sender}", "{subject}"))  # rows apart: easier to check
        p = email_plugin.Email(**options)
        with redirect_stdout(io.StringIO()):
            p.start()
            self.poll(p)  # the first only remembers where the mailbox is
            p.collect()
        return p

    def poll(self, p):
        p.update(Context(self.now, "CPU", False))
        p.thread.join()

    def rows(self, p, after=0):
        self.now += after
        return p.rows(Context(self.now, "CPU", False))

    def test_shows_only_mail_after_the_start(self):
        p = self.plugin()
        self.assertEqual(self.rows(p), (None, None))
        self.mail[2] = ["João Silva <joao@x.pt>", "Almoço", False]
        self.poll(p)
        icon = email_plugin.ENVELOPE_ICON
        self.assertEqual(self.rows(p), (f"{icon} Joao Silva", "Almoco"))
        self.assertEqual(self.rows(p, after=4.9)[1], "Almoco")
        self.assertEqual(self.rows(p, after=0.2), (None, None))  # min_seconds, 5 s

    def test_stays_until_the_longest_row_has_scrolled(self):
        p = self.plugin(rows=(None, "{sender} - {subject}"))
        self.mail[2] = ["<a_very_long_address@example.com>", "Test", False]
        self.poll(p)
        row = self.rows(p)[1]
        self.assertEqual(row, "a_very_long_addr")
        text = "a_very_long_address@example.com - Test"
        seconds = lcd.scroll_time(text)
        self.assertEqual(self.rows(p, after=seconds - 0.1)[0], None)
        self.assertIsNotNone(self.rows(p)[1])
        self.assertEqual(self.rows(p, after=0.2), (None, None))

    def test_one_at_a_time_and_read_ones_go(self):
        p = self.plugin()
        self.mail.update({2: ["A <a@x.pt>", "One", False], 3: ["B <b@x.pt>", "Two", False],
                          4: ["C <c@x.pt>", "Three", False]})
        self.poll(p)
        self.assertEqual(self.rows(p)[1], "One")
        self.mail[3][2] = True  # read on the phone
        self.poll(p)
        self.assertEqual(self.rows(p, after=5.1)[1], "Three")
        self.assertEqual(self.rows(p, after=5.1), (None, None))

    def test_backlog_shows_the_newest_with_the_total(self):
        p = self.plugin(rows=("{icon} {count}", "{sender} - {subject}"))
        for u in range(2, 9):
            self.mail[u] = [f"S{u} <s@x.pt>", f"Mail {u}", False]
        self.poll(p)
        shown = [self.rows(p, after=5.1) for _ in range(3)]
        icon = email_plugin.ENVELOPE_ICON
        self.assertEqual(shown, [(f"{icon} 7 new emails", f"S{u} - Mail {u}") for u in (6, 7, 8)])
        self.assertEqual(self.rows(p, after=5.1), (None, None))

    def test_count_is_the_batchs(self):
        p = self.plugin(rows=("{icon} {count}", "{subject}"), count=("{n} email", "{n} emails"))
        icon = email_plugin.ENVELOPE_ICON
        self.mail.update({2: ["A <a@x.pt>", "One", False], 3: ["B <b@x.pt>", "Two", False]})
        self.poll(p)
        self.assertEqual(self.rows(p), (f"{icon} 2 emails", "One"))
        self.mail[4] = ["C <c@x.pt>", "Three", False]
        self.poll(p)  # while the batch shows: joins it
        self.assertEqual(self.rows(p, after=1), (f"{icon} 2 emails", "One"))  # not mid-message
        self.mail[3][2] = True  # Two read on the phone: it goes, still counted
        self.poll(p)
        self.assertEqual(self.rows(p, after=4.1), (f"{icon} 3 emails", "Three"))
        self.assertEqual(self.rows(p, after=5.1), (None, None))
        self.mail[5] = ["D <d@x.pt>", "Four", False]
        self.poll(p)  # a new batch
        self.assertEqual(self.rows(p), (f"{icon} 1 email", "Four"))

    def test_nothing_new_doesnt_search(self):
        p = self.plugin()
        self.poll(p)
        self.assertEqual(self.imap.searches, 0)

    def test_resume_shows_it_again_from_the_start(self):
        p = self.plugin()
        self.mail[2] = ["A <a@x.pt>", "One", False]
        self.poll(p)
        self.rows(p)
        p.resume()  # a Pomodoro started 1 s in
        self.assertEqual(self.rows(p, after=600)[1], "One")
        self.assertEqual(self.rows(p, after=4.9)[1], "One")

    def test_errors_are_printed_once(self):
        p = self.plugin()
        email_plugin.os.environ["EMAIL_PASSWORD"] = "wrong"
        p.start()
        out = io.StringIO()
        with redirect_stdout(out):
            for _ in range(3):
                self.poll(p)
                self.rows(p)
        self.assertEqual(out.getvalue().count("AUTHENTICATIONFAILED"), 1)

    def test_a_poll_ending_meanwhile_is_taken_in_first(self):
        p = self.plugin()
        self.mail[2] = ["A <a@x.pt>", "One", False]
        result = email_plugin.fetch("h", "u", "secret", "INBOX", p.last, p.validity, float("inf"), 3)

        class Ending:  # the poll ends just as update() looks
            def is_alive(self):
                p.result = result
                return False

        p.thread = Ending()
        with mock.patch.object(email_plugin, "fetch", wraps=email_plugin.fetch) as fetch:
            self.poll(p)
        self.assertEqual(fetch.call_args[0][4], 2)  # from the new last UID
        self.assertEqual(self.rows(p)[1], "One")
        self.assertEqual(self.rows(p, after=5.1), (None, None))  # once

    def test_a_message_without_uid_is_skipped(self):
        p = self.plugin()
        self.imap.no_uid = {2}
        self.mail[2] = ["A <a@x.pt>", "One", False]
        self.poll(p)
        self.assertEqual(self.rows(p), (None, None))
        self.assertIsNone(p.error)
        self.assertEqual(p.last, 2)  # moves on anyway

    def test_a_running_poll_is_left_alone(self):
        p = self.plugin()
        p.thread = mock.Mock(is_alive=lambda: True)
        p.result = (5, 7, None, [], 0)  # may be half written: not taken yet
        p.collect()
        self.assertEqual((p.last, p.result), (1, (5, 7, None, [], 0)))

    def test_count_includes_mail_whose_headers_didnt_come(self):
        p = self.plugin(rows=("{icon} {count}", "{subject}"))
        self.mail.update({2: ["A <a@x.pt>", "One", False], 3: ["B <b@x.pt>", "Two", False]})
        self.imap.no_uid = {2}
        self.poll(p)
        self.assertEqual(self.rows(p), (f"{email_plugin.ENVELOPE_ICON} 2 new emails", "Two"))

    def test_max_shown_0_shows_none(self):
        p = self.plugin(max_shown=0)
        for u in range(2, 6):
            self.mail[u] = [f"S{u} <s@x.pt>", f"Mail {u}", False]
        self.poll(p)
        self.assertEqual(self.rows(p), (None, None))
        self.assertEqual(p.total, 0)

    def test_a_renumbered_mailbox_drops_the_queue(self):
        p = self.plugin()
        self.mail.update({2: ["A <a@x.pt>", "One", False], 3: ["B <b@x.pt>", "Two", False]})
        self.poll(p)
        self.assertEqual(self.rows(p)[1], "One")  # Two waits
        self.mail[4] = ["C <c@x.pt>", "Three", False]
        self.imap.validity = 8
        self.poll(p)
        self.assertEqual(self.rows(p, after=5.1), (None, None))  # Two's UID means nothing now
        self.assertEqual(p.last, 4)  # from here on

    def test_folder_is_quoted_and_a_missing_one_says_so(self):
        p = self.plugin(folder="[Gmail]/All Mail")
        self.assertEqual(self.imap.folders[-1], '"[Gmail]/All Mail"')
        p = email_plugin.Email(folder="Nope")
        with redirect_stdout(io.StringIO()) as out:
            p.start()
            self.poll(p)
            self.rows(p)
        self.assertIn('"Nope": [NONEXISTENT] Unknown Mailbox', out.getvalue())

    def test_off_without_an_account(self):
        del email_plugin.os.environ["EMAIL_PASSWORD"]
        p = email_plugin.Email()
        out = io.StringIO()
        with redirect_stdout(out):
            p.start()
        self.assertIn("Email off", out.getvalue())
        p.update(Context(self.now, "CPU", False))
        self.assertIsNone(p.thread)
        self.assertEqual(self.rows(p), (None, None))


class Main(unittest.TestCase):
    def test_sends_the_settings_then_the_needle(self):
        port = FakeSerial(duration=1.0)
        run_main(port)
        lines = port.lines()
        self.assertEqual(lines[0], f"N{CPU:.1f}")  # the needle at once
        settings = [line for line in lines if line[:1] in ("M", "S", "B", "Z")]  # one per reading
        self.assertEqual(len(settings), 4)  # once
        glyphs = [line for line in lines if line.startswith("G")]
        self.assertEqual(len(glyphs), 3)  # the rows' icons: RAM, power, down arrow
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
        self.assertTrue(rows[-1].endswith(f" {CPU:.0f}% Firefox"))  # RAM on the needle, CPU on the LCD

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

    def test_pomodoro_only_keeps_a_slow_needle(self):
        # Rows and settings sent by 0.8 s; Pomodoro from 1.0 s to 3.0 s
        port = FakeSerial(events=[(1.0, b"POMODORO ON\n"), (3.0, b"POMODORO OFF\n")],
                          duration=4.0)
        scans = []
        run_main(port, scans=scans)
        during = port.lines(1.3, 2.9)
        self.assertTrue(4 <= len(during) <= 8)  # 4 a second, needle only
        self.assertTrue(all(line.startswith("N") for line in during))
        self.assertFalse([t for t in scans if 1.3 < t < 2.9])
        after = port.lines(3.0)
        self.assertGreater(sum(line.startswith("N") for line in after), 10)  # back to 20 a second
        self.assertIn("T0", [line[:2] for line in after])  # rows resent at once
        self.assertIn("T1", [line[:2] for line in after])
        self.assertTrue([t for t in scans if t > 3.0])

    def test_reconnecting_ends_the_pomodoro(self):
        # The Arduino restarts when the port opens again, which ends its Pomodoro
        port = FakeSerial(events=[(0.5, b"POMODORO ON\n")], drop_at=1.0, duration=4.5)
        resumed = []

        class Recorder(Plugin):
            def resume(self):
                resumed.append(port.elapsed())

        output = run_main(port, plugins=[system.System(), status.Status(), Recorder()])
        self.assertIn("device disconnected", output)
        self.assertEqual(len(resumed), 1)  # as after POMODORO OFF
        self.assertGreater(resumed[0], 1.0)
        after = port.lines(3.0)
        self.assertGreater(sum(line.startswith("N") for line in after), 10)  # 20 a second
        self.assertIn("T0", [line[:2] for line in after])

    def test_exit_parks(self):
        self.assertIn("Parked.", run_main(FakeSerial(duration=0.3)))
        output = run_main(FakeSerial(duration=0.3, park_answer=b"POMODORO ON"))
        self.assertIn("Pomodoro running: the needle stays with it.", output)


if __name__ == "__main__":
    unittest.main()
