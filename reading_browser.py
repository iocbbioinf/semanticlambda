"""Reading browser — a terminal app that reads a query as a lambda term.

    $ python reading_browser.py
    > how does aspirin reduce inflammation

The user puts a query; the app delegates it to Claude Code (`reading_agent`),
which proposes the entities and the options. Every choice the user makes is a
READING STEP applied to R = (G(t), Pr) — see `reading_session` for the mapping of
the three interaction kinds onto contraction (options 1 and 2) and reflection.

Rendering is deliberately Claude-Code-shaped: a prompt line, streamed status
lines while the delegate is working, boxed option lists, and a running view of
the reading being built.

Commands:  resume   save the question and all its readings, and stop
           term     show the current term and its pointer set
           readings list what has been saved so far
           quit     leave without saving
"""

from __future__ import annotations

import itertools
import sys
import threading
import time
from typing import Optional

from reading_agent import AgentError, Entity, ReadingAgent
from reading_session import Reading, ReadingSession
from term_utils import _term_type

# ── styling ───────────────────────────────────────────────────────────────────

_TTY = sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def dim(s: str) -> str:      return _c("2", s)
def bold(s: str) -> str:     return _c("1", s)
def orange(s: str) -> str:   return _c("38;5;209", s)
def green(s: str) -> str:    return _c("38;5;114", s)
def blue(s: str) -> str:     return _c("38;5;110", s)
def mauve(s: str) -> str:    return _c("38;5;140", s)
def red(s: str) -> str:      return _c("38;5;174", s)


KIND_STYLE = {
    "A": (orange, "A", "entity understood several ways", "contraction · option 1"),
    "B": (mauve,  "B", "relation understood several ways", "reflection"),
    "C": (blue,   "C", "how this place was reached", "contraction · option 2"),
}


class Spinner:
    """A working indicator, in the shape Claude Code uses."""

    FRAMES = "⋅∙●∙"

    def __init__(self, label: str) -> None:
        self.label = label
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self):
        if _TTY:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        else:
            print(f"  {self.label}…")
        return self

    def _spin(self) -> None:
        t0 = time.monotonic()
        for frame in itertools.cycle(self.FRAMES):
            if self._stop.is_set():
                break
            el = time.monotonic() - t0
            sys.stdout.write(
                f"\r  {orange(frame)} {dim(self.label)} {dim(f'({el:.0f}s)')}  ")
            sys.stdout.flush()
            time.sleep(0.12)

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            sys.stdout.write("\r" + " " * 78 + "\r")
            sys.stdout.flush()
        return False


# ── rendering ─────────────────────────────────────────────────────────────────

def banner() -> None:
    print()
    print(f"  {orange('◆')} {bold('reading browser')}  "
          f"{dim('— a query, read as a term')}")
    print(dim("  the query is delegated to Claude Code; every choice you make "
              "is a reading step"))
    print(dim("  commands: resume · term · readings · quit"))
    print()


def show_term(reading: Reading) -> None:
    """The reading as it stands: term, pointer set, entities."""
    print()
    print(f"  {dim('reading')} {bold(reading.seed.short())}")
    print(f"  {dim('term')}     {green(str(reading.term))}")
    act = reading.act_pointer()
    marks = []
    for p in reading.pointers.pointers:
        here = reading.pointer_entities.get(p.pid)
        tag = f"p{p.pid}"
        if p.is_reflected():
            tag += f"·{_short(p.cast_type)}"
        if here is not None:
            tag += f"@{here.short()}"
        marks.append(bold(f"[{tag}]") if act and p.pid == act.pid
                     else dim(f" {tag} "))
    print(f"  {dim('Pr')}       {' '.join(marks)}   "
          f"{dim(f'|Pr| = {len(reading.pointers)}')}")
    if reading.used:
        print(f"  {dim('entities')} " +
              dim(", ".join(e.short() for e in reading.used)))
    print()


def show_step_header(kind: str, reading: Reading, entities: list[str]) -> None:
    """Which of A/B/C this is, and the entities it uses."""
    style, letter, what, calculus = KIND_STYLE[kind]
    print()
    print(f"  {style('●')} {style(bold(f'step {letter}'))} {dim('·')} {what}")
    print(f"    {dim('reading step:')} {style(calculus)}")
    print(f"    {dim('standing at:')}  "
          f"{bold(reading.act_entity().short() if reading.act_entity() else '?')}")
    if entities:
        print(f"    {dim('entities:')}     {', '.join(entities)}")


def _short(iri: Optional[str]) -> str:
    if not iri:
        return "?"
    return iri.split(":", 1)[-1].split("/")[-1].split("#")[-1]


def ask_choice(prompt: str, labels: list[str], extra: dict[str, str]) -> str:
    """Numbered menu. Returns "1".."n" or one of `extra`'s keys."""
    print()
    if prompt:
        print(f"  {bold(prompt)}")
    for i, lab in enumerate(labels, 1):
        print(f"    {orange(str(i))}  {lab}")
    for key, lab in extra.items():
        print(f"    {dim(key)}  {dim(lab)}")
    valid = {str(i) for i in range(1, len(labels) + 1)} | set(extra)
    while True:
        try:
            raw = input(f"\n  {orange('❯')} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "quit"
        if raw in valid:
            return raw
        print(dim(f"    choose one of: {', '.join(sorted(valid))}"))


# ── the loop ──────────────────────────────────────────────────────────────────

class Browser:
    def __init__(self, model: str) -> None:
        self.agent = ReadingAgent(model=model)
        self.session: Optional[ReadingSession] = None

    # ── entry ─────────────────────────────────────────────────────────────

    def run(self) -> int:
        banner()
        try:
            query = input(f"  {orange('❯')} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not query or query in ("quit", "exit"):
            return 0

        self.session = ReadingSession(query, self.agent)
        try:
            with Spinner("delegating the query to Claude Code"):
                seeds = self.session.start()
        except AgentError as e:
            print(f"  {red('✗')} {e}")
            return 1

        print(f"  {green('✓')} {dim('entities proposed:')} "
              f"{', '.join(bold(s.short()) for s in seeds)}")
        self.session.seed_cursor = 0
        seed = self.session.next_unread_seed()
        if seed is None:
            print(f"  {red('✗')} nothing to read")
            return 1

        try:
            return self.loop(seed)
        except (EOFError, KeyboardInterrupt):
            print()
            self.do_resume()
            return 0

    def loop(self, seed: Entity) -> int:
        s = self.session
        reading = s.open_reading(seed)
        print(f"\n  {green('◆')} opening a reading from {bold(seed.short())}"
              f"  {dim(seed.gloss)}")
        show_term(reading)

        while True:
            try:
                with Spinner(f"asking for the next step at "
                             f"{reading.act_entity().short() if reading.act_entity() else '?'}"):
                    prop = s.propose(reading)
            except AgentError as e:
                print(f"  {red('✗')} {e}")
                return 1

            if prop.kind == "none":
                # No interaction step from this pointer. Try the reading's other
                # pointers; if none is left, this reading is done and we open a
                # new one from an entity that has no reading yet.
                moved = s.mark_exhausted(reading)
                if moved is not None:
                    print("  " + dim("no step here — moving to another "
                                     "pointer of this reading"))
                    continue
                print(f"  {dim('no interaction step remains in this reading')}")
                cr = s.close_current()
                if cr is not None:
                    print(f"  {green('✓')} closed reading "
                          f"{bold(cr.name)}  {dim(str(cr.term))}")
                nxt = s.next_unread_seed()
                if nxt is None:
                    print(f"\n  {dim('every entity has a reading — resuming')}")
                    self.do_resume()
                    return 0
                reading = s.open_reading(nxt)
                print(f"\n  {green('◆')} opening a reading from "
                      f"{bold(nxt.short())}  {dim(nxt.gloss)}")
                show_term(reading)
                continue

            # Render which of A/B/C this is, and the entities it uses.
            ents = []
            for o in prop.options:
                if prop.kind == "B":
                    ents += [o.entity_a.short(), o.entity_b.short()]
                elif o.entity is not None:
                    ents.append(o.entity.short())
            seen, uniq = set(), []
            for e in ents:
                if e not in seen:
                    seen.add(e)
                    uniq.append(e)
            show_step_header(prop.kind, reading, uniq)

            labels = []
            for o in prop.options:
                if prop.kind == "B":
                    head = (f"{bold(o.entity_a.short())} {dim('(question) ⋅')} "
                            f"{bold(o.entity_b.short())} {dim('(answer)')}")
                    labels.append(f"{o.label}  {head}"
                                  + (f"\n       {dim(o.rationale)}" if o.rationale else ""))
                else:
                    tgt = (f"«{o.reading_name}»" if o.reading_name
                           else o.entity.short())
                    labels.append(f"{o.label}  {bold(tgt)}"
                                  + (f"\n       {dim(o.rationale)}" if o.rationale else ""))

            choice = ask_choice(
                prop.prompt or "which reading do you take?", labels,
                {"s": "skip this place", "t": "show the term",
                 "r": "resume (save and stop)", "q": "quit without saving"},
            )
            if choice in ("q", "quit"):
                print(dim("  left without saving"))
                return 0
            if choice == "r":
                self.do_resume()
                return 0
            if choice == "t":
                show_term(reading)
                continue
            if choice == "s":
                if s.mark_exhausted(reading) is None:
                    print(dim("  no other pointer here"))
                continue

            opt = prop.options[int(choice) - 1]
            line = s.apply(reading, prop, opt)
            print(f"\n  {green('✓')} {line}")
            show_term(reading)

    # ── resume ────────────────────────────────────────────────────────────

    def do_resume(self) -> None:
        """"resume for now means — save init question and all its readings"."""
        s = self.session
        if s is None:
            return
        if s.current is not None:
            s.close_current()
        if not s.closed:
            print(dim("  nothing to save"))
            return
        from reading_store import save_session
        path = save_session(s)
        print()
        print(f"  {green('✓')} saved {bold(str(len(s.closed)))} reading(s) for "
              f"{bold(s.query)}")
        for c in s.closed:
            print(f"      {dim('·')} {c.name}  {green(str(c.term))}")
        print(f"  {dim('→')} {dim(str(path))}")
        if self.agent.total_cost_usd:
            print(f"  {dim(f'delegation cost: ${self.agent.total_cost_usd:.4f}')}")
        print()


def main(argv: list[str]) -> int:
    model = "sonnet"
    if "--model" in argv:
        i = argv.index("--model")
        if i + 1 < len(argv):
            model = argv[i + 1]
    return Browser(model).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
