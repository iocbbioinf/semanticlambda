"""Type-ahead selection — the KG browser's search box, in a plain terminal.

The old Textual app (branch `browser`) gave the user an Input over a ListView:
every keystroke re-ran the search and the list below updated live, and the arrow
keys moved the highlight. This reproduces that in the REPL the reading browser
is, without pulling in a full TUI:

    the user types            the list below redraws on EVERY keystroke
    ↑ / ↓                     move the highlight
    Enter                     take the highlighted row
    Esc / Ctrl-C              cancel
    Ctrl-U                    clear the query

It needs a real terminal: `select()` falls back to a plain prompt when stdin is
not a tty (a pipe, a test harness), so scripted input still works.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Optional, Sequence

try:
    import termios
    import tty
    _RAW_OK = True
except ImportError:                                    # pragma: no cover
    _RAW_OK = False


# ── key reading ───────────────────────────────────────────────────────────────

ENTER, ESC, BACKSPACE, CTRL_C, CTRL_U = "enter", "esc", "backspace", "^C", "^U"
UP, DOWN = "up", "down"


class _FdReader:
    """Reads straight from a file descriptor, one byte at a time.

    In raw mode the terminal delivers bytes as they are typed, and an escape
    SEQUENCE (\\x1b [ A) arrives all at once while a bare Esc arrives alone. To
    tell them apart we must ask the descriptor whether more is waiting — but a
    buffered text wrapper may already have swallowed the rest into its own
    buffer, where select() cannot see it. Reading the fd directly removes that
    second buffer, so "is there more?" has one answer.
    """

    def __init__(self, fd: int) -> None:
        self.fd = fd

    def read(self, n: int = 1) -> str:
        try:
            data = os.read(self.fd, n)
        except OSError:
            return ""
        return data.decode("utf-8", "replace")

    def ready(self, timeout: float = 0.15) -> bool:
        import select as _select
        try:
            return bool(_select.select([self.fd], [], [], timeout)[0])
        except (ValueError, OSError):
            return False


def _ready(stream, timeout: float = 0.15) -> bool:
    """Is another byte already waiting? Used to tell Esc from an arrow key."""
    if isinstance(stream, _FdReader):
        return stream.ready(timeout)
    # Not a real terminal (a StringIO in the decoding tests): assume more is
    # available, which is what those tests expect.
    return True


def _read_key(stream) -> str:
    """One keypress, with the arrow escape sequences decoded."""
    ch = stream.read(1)
    if ch == "":
        return ESC
    if ch in ("\r", "\n"):
        return ENTER
    if ch in ("\x7f", "\b"):
        return BACKSPACE
    if ch == "\x03":
        return CTRL_C
    if ch == "\x15":
        return CTRL_U
    if ch == "\x1b":
        # Either a bare Esc or a CSI sequence: \x1b [ A/B. A real sequence
        # arrives whole, so its remaining bytes are already waiting; after a LONE
        # Esc nothing follows. Ask before reading — reading blind would block
        # until the user happened to press something else.
        if not _ready(stream):
            return ESC
        if stream.read(1) != "[":
            return ESC
        if not _ready(stream):
            return ESC
        return {"A": UP, "B": DOWN}.get(stream.read(1), "")
    if ch < " ":
        return ""
    return ch


# ── the widget ────────────────────────────────────────────────────────────────

def select(prompt: str,
           search: Callable[[str], Sequence],
           render: Callable[[object, bool], str],
           empty_note: str = "nothing matches",
           max_rows: int = 8,
           stream=None,
           out=None) -> Optional[object]:
    """Pick one item, searching as the user types.

    `search(query)` returns the rows to show — called on every keystroke, so it
    should be cheap. `render(row, selected)` returns one line of text.

    Returns the chosen row, or None if cancelled.
    """
    stream = stream or sys.stdin
    out = out or sys.stdout

    if not (_RAW_OK and getattr(stream, "isatty", lambda: False)()):
        return _fallback(prompt, search, render, empty_note, max_rows,
                         stream, out)

    fd = stream.fileno()
    saved = termios.tcgetattr(fd)
    query, idx, drawn = "", 0, 0
    try:
        tty.setraw(fd)
        reader = _FdReader(fd)
        while True:
            rows = list(search(query))[:max_rows]
            idx = max(0, min(idx, len(rows) - 1)) if rows else 0
            drawn = _draw(out, prompt, query, rows, idx, render, empty_note,
                          drawn)

            key = _read_key(reader)
            if key in (ESC, CTRL_C):
                _clear(out, drawn)
                return None
            if key == ENTER:
                _clear(out, drawn)
                return rows[idx] if rows else None
            if key == UP:
                idx = (idx - 1) % len(rows) if rows else 0
            elif key == DOWN:
                idx = (idx + 1) % len(rows) if rows else 0
            elif key == BACKSPACE:
                query, idx = query[:-1], 0
            elif key == CTRL_U:
                query, idx = "", 0
            elif key:
                query, idx = query + key, 0
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _draw(out, prompt, query, rows, idx, render, empty_note, previous) -> int:
    """Redraw in place; returns how many lines were written."""
    _clear(out, previous)
    lines = [f"  {prompt}", f"  > {query}█"]
    if not rows:
        lines.append(f"    {empty_note}")
    else:
        lines += ["    " + render(r, i == idx) for i, r in enumerate(rows)]
    lines.append("    ↑↓ move · enter select · esc cancel")
    # \r\n, not \n: the terminal is raw, so it does no carriage return of its own.
    out.write("\r\n".join(lines) + "\r\n")
    out.flush()
    return len(lines)


def _clear(out, n: int) -> None:
    if n:
        out.write(f"\x1b[{n}A\x1b[J")
        out.flush()


def _fallback(prompt, search, render, empty_note, max_rows, stream, out):
    """No tty: type-then-choose, so pipes and scripted input still work.

    Reads through `input()` rather than the stream directly, because that is how
    the rest of the app reads and what its scripted tests patch. When `stream`
    is an explicit object (not stdin) it is read from instead.
    """
    explicit = stream is not None and stream is not sys.stdin

    def ask() -> Optional[str]:
        if explicit:
            line = stream.readline()
            return None if not line else line.strip()
        try:
            return input().strip()
        except (EOFError, KeyboardInterrupt):
            return None

    query = ""
    while True:
        rows = list(search(query))[:max_rows]
        out.write(f"\n  {prompt}\n")
        if not rows:
            out.write(f"    {empty_note}\n")
        for i, r in enumerate(rows, 1):
            out.write(f"    {i}  {render(r, False)}\n")
        out.write("    type to filter · a number to choose · c to cancel\n")
        out.flush()

        raw = ask()
        if raw is None or raw.lower() == "c":
            return None
        if raw.isdigit():
            i = int(raw)
            if 1 <= i <= len(rows):
                return rows[i - 1]
            continue
        query = raw
