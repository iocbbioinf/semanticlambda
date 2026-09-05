"""Type-ahead selection — the KG browser's search box in a plain terminal.

The list must redraw on EVERY keystroke and the arrows must move the highlight,
as the Textual Input+ListView did on branch `browser`. Driving that means a real
terminal, so these tests run the widget under a pty and read what it painted.
"""

import os
import pty
import select as _sel
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from incremental_select import _read_key, select

FAILED = []


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        FAILED.append(msg)


ROWS = ["Aspirin", "Aspirin resistance", "COX enzymes", "COX-1", "COX-2",
        "Inflammation"]

DRIVER = """
import sys; sys.path.insert(0, {root!r})
from incremental_select import select
ROWS = {rows!r}
def search(q):
    return [r for r in ROWS if all(w in r.lower() for w in q.lower().split())]
def render(r, s):
    return ("> " if s else "  ") + r
print("CHOSE:", select("pick one", search, render))
"""


def run_under_pty(keystrokes, pause=0.35):
    """Run the widget in a pty, send keys, return everything it painted."""
    root = str(Path(__file__).resolve().parent.parent)
    code = DRIVER.format(root=root, rows=ROWS)
    pid, fd = pty.fork()
    if pid == 0:
        os.execv(sys.executable, [sys.executable, "-c", code])
    time.sleep(0.8)
    for keys in keystrokes:
        os.write(fd, keys)
        time.sleep(pause)
    out = b""
    deadline = time.time() + 4
    while time.time() < deadline:
        r, _, _ = _sel.select([fd], [], [], 0.4)
        if not r:
            break
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    try:
        os.waitpid(pid, os.WNOHANG)
        os.kill(pid, 9)
    except OSError:
        pass
    return out.decode(errors="replace")


def frames(text):
    """The painted frames, split on the clear-and-move-up escape."""
    return text.split("\x1b[")


def test_key_decoding():
    print("\nkeys — arrows, enter, backspace, escape")
    import io

    def k(s):
        return _read_key(io.StringIO(s))

    check(k("\r") == "enter", "carriage return is enter")
    check(k("\n") == "enter", "newline is enter too")
    check(k("\x1b[A") == "up", "the up arrow is decoded")
    check(k("\x1b[B") == "down", "the down arrow is decoded")
    check(k("\x7f") == "backspace", "delete is backspace")
    check(k("\x03") == "^C", "ctrl-C is seen")
    check(k("\x1bx") == "esc", "a bare escape does not swallow the next key")
    check(k("a") == "a", "an ordinary character comes through")
    check(k("") == "esc", "end of input reads as escape")


def test_list_narrows_on_every_keystroke():
    print("\nthe list narrows as each character is typed")
    out = run_under_pty([b"c", b"o", b"x", b"\r"])

    check("Inflammation" in out, "the full list is shown before typing")
    # After "cox" only the COX rows can remain.
    tail = out[out.rfind("> cox"):]
    check("COX enzymes" in tail, "the matches remain")
    check("Aspirin" not in tail, "and the non-matches are gone")
    check("CHOSE: COX enzymes" in out, "enter takes the highlighted row")


def test_arrows_move_the_highlight():
    print("\nthe arrows move the highlight")
    out = run_under_pty([b"cox", b"\x1b[B", b"\r"])
    check("CHOSE: COX-1" in out,
          "down moves from the first match to the second")

    out = run_under_pty([b"cox", b"\x1b[B", b"\x1b[A", b"\r"])
    check("CHOSE: COX enzymes" in out, "and up moves back")

    # The highlight wraps rather than sticking at the end.
    out = run_under_pty([b"cox", b"\x1b[A", b"\r"])
    check("CHOSE: COX-2" in out, "up from the first wraps to the last")


def test_backspace_and_cancel():
    print("\nbackspace widens again; escape cancels")
    out = run_under_pty([b"cox", b"\x7f\x7f\x7f", b"\r"])
    check("CHOSE: Aspirin" in out,
          "deleting the query brings the whole list back")

    out = run_under_pty([b"cox", b"\x1b"])
    check("CHOSE: None" in out, "escape returns nothing")


def test_no_match_is_survivable():
    print("\na query matching nothing")
    out = run_under_pty([b"zzzz", b"\x7f\x7f\x7f\x7f", b"\r"])
    check("nothing matches" in out, "the empty state is shown")
    check("CHOSE: Aspirin" in out, "and typing back recovers")


def test_falls_back_without_a_tty():
    """Piped input must still work — the app is scripted in other tests."""
    print("\nno tty — a plain prompt instead")
    import io

    stream = io.StringIO("cox\n1\n")
    out = io.StringIO()
    got = select("pick one", lambda q: [r for r in ROWS if q.lower() in r.lower()],
                 lambda r, s: r, stream=stream, out=out)
    check(got == "COX enzymes", "the fallback still selects")
    check("type to filter" in out.getvalue(), "and explains itself")

    stream = io.StringIO("c\n")
    check(select("pick one", lambda q: ROWS, lambda r, s: r,
                 stream=stream, out=io.StringIO()) is None,
          "`c` cancels in the fallback")


if __name__ == "__main__":
    for t in (test_key_decoding,
              test_list_narrows_on_every_keystroke,
              test_arrows_move_the_highlight,
              test_backspace_and_cancel,
              test_no_match_is_survivable,
              test_falls_back_without_a_tty):
        t()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED")
        for m in FAILED:
            print(f"  - {m}")
        sys.exit(1)
    print("all passed")
