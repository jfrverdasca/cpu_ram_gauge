"""Incoming email, read over IMAP: each new unread message shows for as long as its
longest row takes to scroll through once, with how many came in.

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
from concurrent.futures import Future
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
# On the LCD: a Message
Showing = namedtuple("Showing", "message rows fixed since until")


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
    newest new unread messages, up to newest of them, how many new unread ones there
    are, shown or not)."""
    # Quoted: imaplib sends it as is, and a name with spaces ("[Gmail]/All Mail") fails
    folder = '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'
    with imaplib.IMAP4_SSL(host, timeout=30) as imap:
        imap.login(user, password)
        ok, data = imap.status(folder, "(UIDNEXT UIDVALIDITY)")
        if ok != "OK":  # e.g. no such folder: the server's words
            raise imaplib.IMAP4.error(f"{folder}: {data[0].decode(errors='replace')}")
        status = dict(re.findall(rb"(UIDNEXT|UIDVALIDITY) (\d+)", data[0]))
        top, now_validity = int(status[b"UIDNEXT"]) - 1, int(status[b"UIDVALIDITY"])
        # First poll, or the server renumbered the mailbox: only remember where it is.
        # After a renumbering, the UIDs waiting to be shown mean nothing: none unread
        if last is None or now_validity != validity:
            return top, now_validity, None if last is None else set(), [], 0
        if top <= last and first > top:
            return last, validity, None, [], 0  # nothing new, nothing waiting
        imap.select(folder, readonly=True)
        _, data = imap.uid("SEARCH", None, f"UNSEEN UID {min(first, last + 1)}:*")
        # "n:*" also matches the last message when n is past it
        unread = {int(u) for u in data[0].split() if int(u) >= first or int(u) > last}
        new = sorted(u for u in unread if u > last)
        messages = []
        wanted = new[-newest:] if newest > 0 else []  # new[-0:] would be all of them
        if wanted:
            _, data = imap.uid("FETCH", ",".join(map(str, wanted)),
                               "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            for i, part in enumerate(data):
                if isinstance(part, tuple):
                    # The UID comes before the headers or, on some servers, after them
                    after = data[i + 1] if i + 1 < len(data) and isinstance(data[i + 1], bytes) else b""
                    uid = re.search(rb"UID (\d+)", part[0]) or re.search(rb"UID (\d+)", after)
                    if uid:  # without it, skipped: last still moves on, from STATUS
                        messages.append(parse_headers(int(uid.group(1)), part[1]))
        return max([top, last] + new), validity, unread, sorted(messages), len(new)


class Email(Plugin):
    """rows: a template per LCD row, None for a row it leaves to the next plugin.
    Fields: {icon}, {count} (how many came in, from count), {sender} (name, else
    address), {address}, {subject}. What comes before the first field other than
    {icon} stays still; the rest scrolls. E.g. rows=("{icon} {count}", "{sender} -
    {subject}") or rows=(None, "{sender} - {subject}").
    count: the text for one and for more, {n} the number. It's the batch's: the mail
    that came in since the LCD had none, left-out ones included. Mail that comes
    while the batch shows joins it: the number changes between messages, never
    during one.
    max_shown: when more are new (after a Pomodoro, or the computer asleep), only the
    newest max_shown show. min_seconds: how long a message that fits stays.
    interval: seconds between polls (in a thread: the needle never waits)."""

    def __init__(self, rows=("{icon} {count}", "{sender} - {subject}"),
                 count=("{n} new email", "{n} new emails"), max_shown=3, min_seconds=5,
                 interval=60, icon=ENVELOPE_ICON, folder="INBOX"):
        self.templates, self.count = rows, count
        self.max_shown, self.min_seconds, self.interval = max_shown, min_seconds, interval
        self.icon, self.folder = icon, folder
        self.account = None
        self.last = self.validity = None
        self.queue = deque()  # Messages to show, oldest first
        self.total = 0        # new ones in this batch, shown or not
        self.showing = None   # a Showing
        self.future = self.error = None  # future: the poll running or not taken in yet

    def start(self):
        account = [os.environ.get(k) for k in ("EMAIL_HOST", "EMAIL_USER", "EMAIL_PASSWORD")]
        if all(account):
            self.account = account
        else:
            print("Email off: set EMAIL_HOST, EMAIL_USER and EMAIL_PASSWORD in .env")

    def update(self, ctx):
        if not self.account:
            return
        self.collect()
        if self.future:  # still polling
            return
        first = self.queue[0].uid if self.queue else float("inf")
        args = (*self.account, self.folder, self.last, self.validity, first, self.max_shown)
        self.future = Future()
        # Not a ThreadPoolExecutor: its threads aren't daemons, and Ctrl+C would wait
        # for a poll stuck on the network
        threading.Thread(target=self.poll, args=(self.future, *args), daemon=True).start()

    @staticmethod
    def poll(future, *args):
        try:
            future.set_result(fetch(*args))
        except Exception as e:  # network, login, server: tried again next poll
            future.set_exception(e)

    def collect(self):
        """Takes in the last poll's result, once it has ended."""
        if not (self.future and self.future.done()):
            return
        future, self.future = self.future, None
        try:
            result = future.result()
        except Exception as e:
            error = f"Email: {str(e) or type(e).__name__}"
            if error != self.error:
                print(error)
            self.error = error
            return
        if self.error:
            print("Email: back")
        self.error = None
        # new: all of them, also those left out or whose headers didn't come
        self.last, self.validity, unread, messages, new = result
        if unread is not None:  # those read elsewhere meanwhile go
            self.queue = deque(m for m in self.queue if m.uid in unread)
        self.queue.extend(messages)
        self.total += new
        while len(self.queue) > self.max_shown:
            self.queue.popleft()

    def resume(self):
        # The Pomodoro or the sleep cut it short: shown again from the start
        if self.showing:
            self.queue.appendleft(self.showing.message)
        self.showing = None

    def notifying(self, ctx):
        return self.showing is not None

    def rows(self, ctx):
        self.collect()
        if self.showing and ctx.now >= self.showing.until:
            self.showing = None
        if not self.showing:
            if not self.queue:
                self.total = 0  # the batch is over
                return None, None
            m = self.queue.popleft()
            count = self.count[self.total != 1].format(n=self.total)
            fields = dict(icon=self.icon, count=count, sender=m.sender, address=m.address,
                          subject=m.subject)
            rows = [t and t.format(**fields) for t in self.templates]
            fixed = [t and fixed_length(t, self.icon) for t in self.templates]
            seconds = max([self.min_seconds] + [lcd.scroll_time(r[f:], lcd.WIDTH - f)
                                                for r, f in zip(rows, fixed) if r])
            self.showing = Showing(m, rows, fixed, ctx.now, ctx.now + seconds)
        s = self.showing
        return tuple(r and lcd.fit(r, f, s.since) for r, f in zip(s.rows, s.fixed))
