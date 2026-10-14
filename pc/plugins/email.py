"""Incoming email, read over IMAP: each new unread message shows for as long as its
longest row takes to scroll through once.

The account comes from .env (or the environment): EMAIL_HOST, EMAIL_USER and
EMAIL_PASSWORD (for Gmail, an app password). Without them the plugin stays idle.
Messages are never marked as read.
"""

import email
import email.policy
import imaplib
import os
import re
import string
import threading
from collections import deque, namedtuple
from email.utils import parseaddr

import lcd
from plugins import Plugin

ENVELOPE_ICON = lcd.glyph([
    ".....",   # envelope
    "#####",
    "##.##",
    "#.#.#",
    "#...#",
    "#...#",
    "#####",
    "....."])

Message = namedtuple("Message", "uid sender address subject")


def fixed_length(template, icon):
    """Characters at the start of template's text that stay still: everything before
    its first field other than {icon}."""
    n = 0
    for literal, field, _, _ in string.Formatter().parse(template):
        n += len(literal)
        if field != "icon":
            return n
        n += len(icon)
    return n


def parse_headers(uid, raw):
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    name, address = parseaddr(str(msg["From"] or ""))
    subject = " ".join(str(msg["Subject"] or "").split())  # no line breaks or tabs
    return Message(uid, lcd.ascii(name or address), lcd.ascii(address),
                   lcd.ascii(subject or "(no subject)"))


def fetch(host, user, password, folder, last, validity, first, newest):
    """One poll. last: the highest UID seen (None for none yet); validity: its
    mailbox's UIDVALIDITY; first: the lowest UID still waiting to be shown. Returns
    (last, validity, UIDs from first on still unread or None when it didn't look, the
    newest new unread messages, up to newest of them, how many new unread ones were
    left out)."""
    with imaplib.IMAP4_SSL(host, timeout=30) as imap:
        imap.login(user, password)
        _, data = imap.status(folder, "(UIDNEXT UIDVALIDITY)")
        status = dict(re.findall(rb"(UIDNEXT|UIDVALIDITY) (\d+)", data[0]))
        top, now_validity = int(status[b"UIDNEXT"]) - 1, int(status[b"UIDVALIDITY"])
        # First poll, or the server renumbered the mailbox: only remember where it is
        if last is None or now_validity != validity:
            return top, now_validity, None, [], 0
        if top <= last and first > top:
            return last, validity, None, [], 0  # nothing new, nothing waiting
        imap.select(folder, readonly=True)
        _, data = imap.uid("SEARCH", None, f"UNSEEN UID {min(first, last + 1)}:*")
        # "n:*" also matches the last message when n is past it
        unread = {int(u) for u in data[0].split() if int(u) >= first or int(u) > last}
        new = sorted(u for u in unread if u > last)
        messages = []
        if new[-newest:]:
            _, data = imap.uid("FETCH", ",".join(map(str, new[-newest:])),
                               "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            for i, part in enumerate(data):
                if isinstance(part, tuple):
                    # The UID comes before the headers or, on some servers, after them
                    after = data[i + 1] if i + 1 < len(data) and isinstance(data[i + 1], bytes) else b""
                    uid = re.search(rb"UID (\d+)", part[0]) or re.search(rb"UID (\d+)", after)
                    if uid:  # without it, skipped: last still moves on, from STATUS
                        messages.append(parse_headers(int(uid.group(1)), part[1]))
        return (max([top, last] + new), validity, unread, sorted(messages),
                max(0, len(new) - newest))


class Email(Plugin):
    """rows: a template per LCD row, None for a row it leaves to the next plugin.
    Fields: {icon}, {sender} (name, else address), {address}, {subject}. What comes
    before the first field other than {icon} stays still; the rest scrolls.
    E.g. rows=("{icon} {sender}", "{subject}") or rows=(None, "{sender} - {subject}").
    max_shown: when more are new (after a Pomodoro, or the computer asleep), only the
    newest max_shown show, then summary for summary_seconds, on the first row rows
    uses. min_seconds: how long a message that fits stays. interval: seconds between
    polls (in a thread: the needle never waits)."""

    def __init__(self, rows=("{icon} {sender}", "{subject}"), summary="{icon} +{count} emails",
                 max_shown=3, summary_seconds=5, min_seconds=5, interval=60,
                 icon=ENVELOPE_ICON, folder="INBOX"):
        self.templates, self.summary = rows, summary
        self.max_shown, self.summary_seconds = max_shown, summary_seconds
        self.min_seconds, self.interval = min_seconds, interval
        self.icon, self.folder = icon, folder
        self.account = None
        self.last = self.validity = None
        self.queue = deque()  # Messages to show, oldest first
        self.extra = 0        # new ones left out, for the summary
        self.showing = None   # (Message or the summary's count, rows, fixed, since, until)
        self.thread = self.result = self.error = None

    def start(self):
        account = [os.environ.get(k) for k in ("EMAIL_HOST", "EMAIL_USER", "EMAIL_PASSWORD")]
        if all(account):
            self.account = account
        else:
            print("Email off: set EMAIL_HOST, EMAIL_USER and EMAIL_PASSWORD in .env")

    def update(self, ctx):
        if not self.account:
            return
        # Alive first: a poll that ends between the two would leave its result for
        # later, and the next poll would start from the same last UID
        if self.thread and self.thread.is_alive():
            return
        self.collect()
        first = self.queue[0].uid if self.queue else float("inf")
        args = (*self.account, self.folder, self.last, self.validity, first, self.max_shown)
        self.thread = threading.Thread(target=self.poll, args=args, daemon=True)
        self.thread.start()

    def poll(self, *args):
        try:
            self.result = fetch(*args)
        except Exception as e:  # network, login, server: tried again next poll
            self.result = e

    def collect(self):
        """Takes in the last poll's result, if one came."""
        result, self.result = self.result, None
        if result is None:
            return
        if isinstance(result, Exception):
            error = f"Email: {str(result) or type(result).__name__}"
            if error != self.error:
                print(error)
            self.error = error
            return
        if self.error:
            print("Email: back")
        self.error = None
        self.last, self.validity, unread, messages, left_out = result
        if unread is not None:  # those read elsewhere meanwhile go
            self.queue = deque(m for m in self.queue if m.uid in unread)
        self.queue.extend(messages)
        self.extra += left_out
        while len(self.queue) > self.max_shown:
            self.queue.popleft()
            self.extra += 1

    def resume(self):
        # The Pomodoro or the sleep cut it short: shown again from the start
        if self.showing:
            item = self.showing[0]
            if isinstance(item, Message):
                self.queue.appendleft(item)
            else:
                self.extra += item
        self.showing = None

    def rows(self, ctx):
        self.collect()
        if self.showing and ctx.now >= self.showing[4]:
            self.showing = None
        if not self.showing:
            if self.queue:
                m = self.queue.popleft()
                fields = dict(icon=self.icon, sender=m.sender, address=m.address, subject=m.subject)
                rows = [t and t.format(**fields) for t in self.templates]
                fixed = [t and fixed_length(t, self.icon) for t in self.templates]
                seconds = max([self.min_seconds] + [lcd.scroll_time(r[f:], lcd.WIDTH - f)
                                                    for r, f in zip(rows, fixed) if r])
                self.showing = (m, rows, fixed, ctx.now, ctx.now + seconds)
            elif self.extra:
                # The summary on the first row the layout uses
                text = self.summary.format(icon=self.icon, count=self.extra)
                rows = [None, None]
                rows[0 if self.templates[0] else 1] = text
                self.showing = (self.extra, rows, [lcd.WIDTH] * 2, ctx.now,
                                ctx.now + self.summary_seconds)
                self.extra = 0
            else:
                return None, None
        _, rows, fixed, since, _ = self.showing
        return tuple(r and lcd.fit(r, f, since) for r, f in zip(rows, fixed))
