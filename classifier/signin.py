"""Sign in to Claude from Classifier, without the terminal.

claude-classifier-docker runs Claude Code in a container; signing in there
means choosing an account type, opening a long link, and pasting back a code.
This drives that conversation through a pseudo-terminal:

  1. start claude-classifier-docker (terminal mode) on /login, in an empty folder
  2. choose "Claude account with subscription"
  3. read the sign-in link out of the output   -> shown with an Open button
  4. type the code the person pastes            -> Claude finishes the sign-in
  5. /exit, so the launcher saves the new sign-in (shared with claude-docker)
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import time

from . import review

ANSI = re.compile(rb"\x1b\[[0-9;?<>=]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[()][A-Z0-9]|\x1b[=>78]")
URL = re.compile(r"https://\S*oauth/authorize\?\S+")


def _clean(b: bytes) -> str:
    return ANSI.sub(b"", b).decode("utf-8", "replace").replace("\r", "")


class Session:
    """One sign-in conversation.  Methods block; run them off the GUI thread."""

    def __init__(self, log=print):
        self.log = log
        self.proc = None
        self.fd = None
        self.buf = b""

    def _read(self, seconds: float, until=None) -> str | None:
        """Read for up to `seconds`; return the first match of `until` (regex on
        the cleaned text read since the call began), or None."""
        start = len(self.buf)
        end = time.time() + seconds
        while time.time() < end:
            if self.proc.poll() is not None:
                break
            r, _, _ = select.select([self.fd], [], [], 0.3)
            if r:
                try:
                    self.buf += os.read(self.fd, 65536)
                except OSError:
                    break
                if until:
                    m = re.search(until, _clean(self.buf[start:]), re.S)
                    if m:
                        return m.group(0)
        return None

    def recent(self, n=600) -> str:
        return _clean(self.buf)[-n:]

    def start(self) -> str:
        """Start Claude on /login and return the sign-in link."""
        exe = review.launcher()
        if not exe:
            raise RuntimeError("claude-classifier-docker is not installed")
        review.SIGNIN_DIR.mkdir(parents=True, exist_ok=True)
        (review.SIGNIN_DIR / "prompt.md").write_text("/login\n", encoding="utf-8")
        master, slave = pty.openpty()
        # Very wide, so the link is printed on one line.
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 1000, 0, 0))
        env = dict(os.environ, COLUMNS="1000", LINES="50", TERM="xterm-256color")
        self.proc = subprocess.Popen(
            [exe, str(review.SIGNIN_DIR.resolve()), "--prompt-file",
             str((review.SIGNIN_DIR / "prompt.md").resolve())],
            stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True,
            cwd=str(review.SIGNIN_DIR))
        os.close(slave)
        self.fd = master
        self.log("Starting Claude (this takes about 20 seconds)…")
        if not self._read(120, r"Select login method|login method"):
            raise RuntimeError("Claude did not reach its sign-in screen:\n" + self.recent())
        self.log("Choosing your Claude account…")
        os.write(self.fd, b"\r")               # option 1 is already selected
        link = self._read(40, r"https://\S*oauth/authorize\?[^\s]+")
        if not link:
            raise RuntimeError("No sign-in link appeared:\n" + self.recent())
        return link

    def finish(self, code: str) -> tuple[bool, str]:
        """Give Claude the code, end the session, and judge by the saved sign-in.

        Claude's on-screen wording is not relied on (a first version misread a
        successful sign-in as a failure): success means the session ended and
        left a newer, unexpired sign-in behind."""
        before = review.signin_expiry()
        code = code.strip()
        os.write(self.fd, code.encode() + b"\r")
        self.log("Checking the code with Claude…")
        hit = self._read(60, r"(?i)login successful|logged in|success|"
                             r"invalid code|invalid_grant|oauth error|failed to")
        screen = self.recent(400)
        self.log("Saving the sign-in…")
        self._read(2)
        os.write(self.fd, b"\r")                # "Press Enter to continue", if shown
        self._read(2)
        os.write(self.fd, b"\x1b")              # out of any menu
        os.write(self.fd, b"/exit\r")
        try:
            self.proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self.stop()
        state, text = review.signin_state()
        if state in ("ok", "renews") and review.signin_expiry() > before:
            return True, text
        if state == "ok":
            return True, text + " (it was already current)"
        why = ("Claude did not accept the code. Codes work once and only for a few "
               "minutes: close this window and start again." if hit and re.search(
                   r"(?i)invalid|oauth error|failed", hit) else
               "The sign-in was not saved.")
        return False, why + "\n\nLast thing Claude showed:\n" + screen

    def stop(self):
        """Cancel: end Claude (the launcher still cleans up its containers)."""
        if self.proc and self.proc.poll() is None:
            try:
                os.write(self.fd, b"\x1b")
                os.write(self.fd, b"/exit\r")
                self.proc.wait(timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                    self.proc.wait(timeout=20)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
